from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import pytz
from sqlalchemy import create_engine, text
import urllib.parse
import os
from dotenv import load_dotenv
import pytz

#funciones del bot
from app.core.geocode_courts import sync_court_coordinates
from app.core.assign_courts import process_pending_lawsuits
from app.bot_control import BotController, BotStatus

load_dotenv()

#BD
from app.utils.database import DATABASE_URL

COLOMBIA_TZ = pytz.timezone('America/Bogota')

#Crear FastAPI
app = FastAPI(
    title="Bot de Asignación de Juzgados",
    description="API REST para asignar juzgados a demandas basado en distancia geográfica",
    version="1.0.0"
)

# ============================================================
# ✅ CONFIGURAR CORS
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://localhost:4201",
        "http://127.0.0.1:4200",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# MODELOS PYDANTIC
# ============================================================

class ClientProcessed(BaseModel):
    lawsuit_id: int
    client_identification: Optional[str]
    client_address: Optional[str]
    client_city: Optional[str]
    court_name: Optional[str]
    distance_km: Optional[float]
    assigned_at: datetime
    status: str


class BotStatusResponse(BaseModel):
    status: str
    last_execution: Optional[str]
    last_error: Optional[dict]
    api_calls_today: int
    api_quota_exceeded: bool
    is_manual_stopped: bool
    message: str


class ExecuteRequest(BaseModel):
    limit: Optional[int] = None


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def execute_bot_process(limit=None):
    """Ejecuta el proceso completo del bot con control de estado"""
    
    #Verifica si puede ejecutarse
    can_run, reason = BotController.can_run()
    if not can_run:
        BotController.log(f"⚠️ Intento de ejecución bloqueado: {reason}", "WARNING")
        raise Exception(reason)
    
    try:
        #Marcar como ejecutando
        BotController.update_status(BotStatus.RUNNING)
        BotController.log("🚀 Iniciando ejecución del bot", "INFO")
        
        #sincronizar juzgados
        BotController.log("📍 Sincronizando coordenadas de juzgados...", "INFO")
        sync_court_coordinates()
        
        #procesar asignaciones
        BotController.log(f"⚙️ Procesando asignaciones (limit={limit})...", "INFO")
        
        #redirigir output para logs
        import io
        import sys
        
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        
        process_pending_lawsuits(limit=limit)
        
        output = buffer.getvalue()
        sys.stdout = old_stdout
        
        #obtener estadísticas
        engine = create_engine(DATABASE_URL, echo=False)
        
        with engine.connect() as connection:
            result = connection.execute(text("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN court_name IS NOT NULL THEN 1 ELSE 0 END) as asignados,
                    SUM(CASE WHEN court_name IS NULL THEN 1 ELSE 0 END) as sin_direccion
                FROM lawsuit_court_assignments
            """))
            stats = result.fetchone()
        
        #detenido exitosamente
        BotController.update_status(BotStatus.STOPPED)
        BotController.log(f"✅ Ejecución completada - Asignados: {stats[1]}, Sin dirección: {stats[2]}", "INFO")
        
        return {
            "status": "success",
            "total_procesados": stats[0],
            "con_juzgado_asignado": stats[1],
            "sin_direccion": stats[2]
        }
        
    except Exception as e:
        error_msg = str(e)
        
        #detectar error de cuota excedida de Google API
        if "OVER_QUERY_LIMIT" in error_msg or "quota" in error_msg.lower():
            BotController.mark_no_credits()
            BotController.log(f"❌ Error: Sin créditos de Google Maps API", "ERROR")
        else:
            BotController.update_status(BotStatus.ERROR, error_msg)
            BotController.log(f"❌ Error en ejecución: {error_msg}", "ERROR")
        
        raise


# ============================================================
# ENDPOINTS PARA EL FRONT DE SOUL
# ============================================================

@app.get("/")
def read_root():
    """Endpoint raíz con información de la API"""
    return {
        "service": "Bot de Asignación de Juzgados",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "health": "GET /health",
            "status": "GET /status",
            "clients": "GET /clients",
            "execute": "POST /execute",
            "stop": "POST /stop",
            "start": "POST /start",
            "logs": "GET /logs"
        }
    }


@app.get("/health")
def health_check():
    """estado del servicio y la conexión a BD"""
    try:
        engine = create_engine(DATABASE_URL, echo=False)
        
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            
            result = connection.execute(text("""
                SELECT COUNT(*) FROM court_coordinates cc
                INNER JOIN data_courts dc ON cc.court_id = dc.id
                WHERE dc.status = 'Activo' 
                AND dc.deleted_at IS NULL
                AND cc.deleted_at IS NULL
            """))
            courts_count = result.fetchone()[0]
            
            result = connection.execute(text("SELECT COUNT(*) FROM lawsuit_court_assignments"))
            assignments_count = result.fetchone()[0]
            
        return {
            "status": "healthy",
            "database": "connected",
            "courts_geocoded": courts_count,
            "total_assignments": assignments_count,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "database": "disconnected",
                "error": str(e),
                "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
            }
        )


@app.get("/status", response_model=BotStatusResponse)
def get_bot_status():
    """
    Obtiene el estado actual del bot
    
    Estados posibles:
    - running: Bot en ejecución
    - stopped: Bot detenido (normal o manual)
    - no_api_credits: Sin créditos de Google Maps API
    - error: Error en última ejecución
    """
    state = BotController.get_state()

    last_error = state.get('last_error')
    error_message = last_error.get('message', 'Error desconocido') if last_error else 'Error desconocido'
    
    #determinar mensaje según estado
    messages = {
        BotStatus.RUNNING: "Bot en ejecución",
        BotStatus.STOPPED: "Bot detenido (esperando próxima ejecución programada)" if not state.get("is_manual_stopped") else "Bot detenido manualmente",
        BotStatus.NO_API_CREDITS: "Sin créditos de Google Maps API - Contactar administrador",
        BotStatus.ERROR: f"Error: {error_message}"
    }
    
    return {
        **state,
        "message": messages.get(state["status"], "Estado desconocido")
    }


@app.get("/clients", response_model=List[ClientProcessed])
def get_processed_clients(
    limit: int = Query(100, description="Número máximo de resultados"),
    offset: int = Query(0, description="Número de resultados a saltar"),
    status: Optional[str] = Query(None, description="Filtrar por estado: 'assigned' o 'no_address'")
):
    """
    Lista todos los clientes procesados
    
    - **limit**: Número máximo de resultados (default: 100)
    - **offset**: Paginación (default: 0)
    - **status**: Filtrar por estado ('assigned' o 'no_address')
    """
    try:
        engine = create_engine(DATABASE_URL, echo=False)
        
        with engine.connect() as connection:
            query = """
                SELECT 
                    lawsuit_id,
                    client_identification,
                    client_address,
                    client_city,
                    court_name,
                    distance_km,
                    assigned_at
                FROM lawsuit_court_assignments
                WHERE 1=1
            """
            
            params = {}
            
            if status == "assigned":
                query += " AND court_name IS NOT NULL"
            elif status == "no_address":
                query += " AND court_name IS NULL"
            
            query += " ORDER BY assigned_at DESC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset
            
            result = connection.execute(text(query), params)
            rows = result.fetchall()
            
            clients = []
            for row in rows:
                clients.append({
                    "lawsuit_id": row[0],
                    "client_identification": row[1],
                    "client_address": row[2],
                    "client_city": row[3],
                    "court_name": row[4],
                    "distance_km": float(row[5]) if row[5] else None,
                    "assigned_at": row[6],
                    "status": "Asignado" if row[4] else "Sin dirección"
                })
            
            return clients
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
def execute_bot(request: ExecuteRequest):
    """
    Ejecuta el bot manualmente (usando Celery en segundo plano)
    
    - **limit**: Número máximo de clientes a procesar (null = todos)
    
    La ejecución manual NO afecta las tareas programadas.
    """
    try:
        #verificar si puede ejecutarse
        can_run, reason = BotController.can_run()
        if not can_run:
            raise HTTPException(status_code=400, detail=reason)
        
        #importar tarea de Celery
        from app.tasks import manual_execute_bot
        
        #ejecutar en Celery (asíncrono)
        task = manual_execute_bot.delay(request.limit)
        
        BotController.log(f"🎯 [MANUAL] Ejecución encolada (Task ID: {task.id})", "INFO")
        
        return {
            "status": "queued",
            "message": "Ejecución manual iniciada en segundo plano",
            "task_id": task.id,
            "limit": request.limit,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stop")
def stop_bot():
    """
    Detiene el bot manualmente
    
    El bot no se ejecutará automáticamente hasta que se llame a /start
    """
    try:
        BotController.manual_stop()
        BotController.log("🛑 Bot detenido manualmente", "INFO")
        
        return {
            "status": "stopped",
            "message": "Bot detenido exitosamente",
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/start")
def start_bot():
    """
    Inicia el bot (después de haberlo detenido manualmente o por falta de créditos)
    
    Resetea el estado para permitir ejecuciones automáticas
    """
    try:
        BotController.manual_start()
        BotController.log("▶️ Bot iniciado/habilitado manualmente", "INFO")
        
        return {
            "status": "started",
            "message": "Bot habilitado exitosamente",
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs", response_class=PlainTextResponse)
def get_logs(lines: int = Query(100, description="Número de líneas a mostrar")):
    """
    Obtiene los logs del bot
    
    - **lines**: Número de líneas a mostrar (default: 100, max: 1000)
    """
    if lines > 1000:
        lines = 1000
    
    try:
        log_lines = BotController.get_logs(lines=lines)
        return "".join(log_lines) if log_lines else "No hay logs disponibles"
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Monitorear uso de API de Google Maps
@app.get("/api-usage")
def get_api_usage():
    """
    Obtiene estadísticas de uso de la API de Google Maps
    
    Returns:
        - daily: Estadísticas diarias
        - monthly: Estadísticas mensuales
        - status: Estado actual (OK, WARNING, CRITICAL)
    """
    usage = BotController.get_api_usage()
    return {
        **usage,
        "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
    }

# ============================================================
# EJECUTAR SERVIDOR
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    #crear carpeta de logs
    os.makedirs("logs", exist_ok=True)
    
    #log inicial
    BotController.log("🚀 Iniciando API del Bot de Asignación de Juzgados", "INFO")
    
    uvicorn.run(app, host="0.0.0.0", port=8000)