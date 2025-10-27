from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import pytz
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv()

#importar configuración de base de datos
from app.utils.database import (
    DATABASE_URL, 
    DB_NAMES, 
    get_database_url, 
    test_database_connections
)

#importar funciones del bot
from app.core.geocode_courts import sync_court_coordinates
from app.core.assign_courts import process_pending_lawsuits
from app.bot_control import BotController, BotStatus

COLOMBIA_TZ = pytz.timezone('America/Bogota')

#crear FastAPI
app = FastAPI(
    title="Bot de Asignación de Juzgados - Multi-BD",
    description="API REST para asignar juzgados a demandas basado en distancia geográfica en múltiples bases de datos",
    version="2.0.0"
)

#configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://localhost:4201",
        "http://127.0.0.1:4200",
        "http://localhost:8001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

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


def execute_bot_process(limit=None):
    """Ejecuta el proceso completo del bot con control de estado"""
    
    #verificar si puede ejecutarse
    can_run, reason = BotController.can_run()
    if not can_run:
        BotController.log(f"⚠️ Intento de ejecución bloqueado: {reason}", "WARNING")
        raise Exception(reason)
    
    try:
        #marcar como ejecutando
        BotController.update_status(BotStatus.RUNNING)
        BotController.log("🚀 Iniciando ejecución del bot en todas las BDs", "INFO")
        
        #sincronizar juzgados
        BotController.log(f"📍 Sincronizando coordenadas de juzgados en {len(DB_NAMES)} BDs...", "INFO")
        sync_court_coordinates()
        
        #procesar asignaciones
        BotController.log(f"⚙️ Procesando asignaciones en {len(DB_NAMES)} BDs (limit={limit})...", "INFO")
        process_pending_lawsuits(limit=limit)
        
        #obtener estadísticas globales
        total_stats = {
            "total_procesados": 0,
            "con_juzgado_asignado": 0,
            "sin_direccion": 0
        }
        
        for db_name in DB_NAMES:
            database_url = get_database_url(db_name)
            engine = create_engine(database_url, echo=False)
            
            with engine.connect() as connection:
                result = connection.execute(text("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN court_name IS NOT NULL 
                            AND court_name NOT IN ('Sin dirección', 'Error en geocodificación', 
                                                   'Dirección incorrecta o en otra ciudad', 
                                                   'No se encuentra juzgado en ciudad')
                            THEN 1 ELSE 0 END) as asignados,
                        SUM(CASE WHEN court_name IS NULL 
                            OR court_name IN ('Sin dirección', 'Error en geocodificación', 
                                             'Dirección incorrecta o en otra ciudad', 
                                             'No se encuentra juzgado en ciudad')
                            THEN 1 ELSE 0 END) as sin_direccion
                    FROM lawsuit_court_assignments
                """))
                stats = result.fetchone()
                
                total_stats["total_procesados"] += stats[0] or 0
                total_stats["con_juzgado_asignado"] += stats[1] or 0
                total_stats["sin_direccion"] += stats[2] or 0
            
            engine.dispose()
        
        #detenido exitosamente
        BotController.update_status(BotStatus.STOPPED)
        BotController.log(
            f"✅ Ejecución completada - Asignados: {total_stats['con_juzgado_asignado']}, "
            f"Sin dirección: {total_stats['sin_direccion']}", 
            "INFO"
        )
        
        return {
            "status": "success",
            "databases_processed": len(DB_NAMES),
            **total_stats
        }
        
    except Exception as e:
        error_msg = str(e)
        
        # Detectar cuota excedida de Google API
        if "OVER_QUERY_LIMIT" in error_msg or "quota" in error_msg.lower():
            BotController.mark_no_credits()
            BotController.log(f"❌ Error: Sin créditos de Google Maps API", "ERROR")
        else:
            BotController.update_status(BotStatus.ERROR, error_msg)
            BotController.log(f"❌ Error en ejecución: {error_msg}", "ERROR")
        
        raise


#endpoints API para el front

@app.get("/")
def read_root():
    """Endpoint raíz con información de la API"""
    return {
        "service": "Bot de Asignación de Juzgados - Multi-BD",
        "version": "2.0.0",
        "status": "running",
        "databases": len(DB_NAMES),
        "database_names": DB_NAMES,
        "endpoints": {
            "health": "GET /health",
            "status": "GET /status",
            "clients": "GET /clients",
            "execute": "POST /execute",
            "stop": "POST /stop",
            "start": "POST /start",
            "logs": "GET /logs",
            "api_usage": "GET /api-usage",
            "databases_test": "GET /databases/test",
            "databases_stats": "GET /databases/stats",
            "databases_list": "GET /databases/list"
        }
    }


@app.get("/health")
def health_check():
    """Estado del servicio y conexión a TODAS las BDs"""
    try:
        db_results = test_database_connections()
        
        successful_dbs = sum(1 for r in db_results.values() if r['status'] == 'ok')
        
        first_db_url = get_database_url(DB_NAMES[0])
        engine = create_engine(first_db_url, echo=False)
        
        with engine.connect() as connection:
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
        
        engine.dispose()
        
        return {
            "status": "healthy" if successful_dbs == len(DB_NAMES) else "degraded",
            "databases": {
                "total": len(DB_NAMES),
                "connected": successful_dbs,
                "failed": len(DB_NAMES) - successful_dbs,
                "names": DB_NAMES,
                "details": db_results
            },
            "courts_geocoded": courts_count,
            "total_assignments": assignments_count,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
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
    status: Optional[str] = Query(None, description="Filtrar por estado: 'assigned' o 'no_address'"),
    database: Optional[str] = Query(None, description="Nombre de la base de datos (default: primera BD)")
):
    """
    Lista todos los clientes procesados de una BD específica
    
    - **limit**: Número máximo de resultados (default: 100)
    - **offset**: Paginación (default: 0)
    - **status**: Filtrar por estado ('assigned' o 'no_address')
    - **database**: BD específica (default: primera configurada)
    """
    try:
        # Usar primera BD
        db_name = database if database and database in DB_NAMES else DB_NAMES[0]
        
        database_url = get_database_url(db_name)
        engine = create_engine(database_url, echo=False)
        
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
                query += """ AND court_name IS NOT NULL 
                            AND court_name NOT IN ('Sin dirección', 'Error en geocodificación', 
                                                   'Dirección incorrecta o en otra ciudad', 
                                                   'No se encuentra juzgado en ciudad')"""
            elif status == "no_address":
                query += """ AND (court_name IS NULL 
                            OR court_name IN ('Sin dirección', 'Error en geocodificación', 
                                             'Dirección incorrecta o en otra ciudad', 
                                             'No se encuentra juzgado en ciudad'))"""
            
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
                    "status": "Asignado" if row[4] and row[4] not in [
                        'Sin dirección', 'Error en geocodificación', 
                        'Dirección incorrecta o en otra ciudad', 
                        'No se encuentra juzgado en ciudad'
                    ] else "Sin dirección"
                })
        
        engine.dispose()
        return clients
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
def execute_bot(request: ExecuteRequest):
    """
    Ejecuta el bot manualmente en TODAS las BDs (usando Celery en segundo plano)
    
    - **limit**: Número máximo de clientes a procesar por BD (null = todos)
    
    La ejecución manual NO afecta las tareas programadas.
    """
    try:
        # Verificar si puede ejecutarse
        can_run, reason = BotController.can_run()
        if not can_run:
            raise HTTPException(status_code=400, detail=reason)
        
        # Importar tarea de Celery
        from app.tasks import manual_execute_bot
        
        # Ejecutar en Celery (asíncrono)
        task = manual_execute_bot.delay(request.limit)
        
        BotController.log(f"🎯 [MANUAL] Ejecución encolada en {len(DB_NAMES)} BDs (Task ID: {task.id})", "INFO")
        
        return {
            "status": "queued",
            "message": f"Ejecución manual iniciada en {len(DB_NAMES)} bases de datos",
            "databases": DB_NAMES,
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


@app.get("/databases/test")
def test_databases():
    """
    Prueba la conexión a todas las bases de datos configuradas
    
    Returns:
        dict: Estado de conexión de cada BD
    """
    results = test_database_connections()
    
    success_count = sum(1 for r in results.values() if r['status'] == 'ok')
    
    return {
        "total_databases": len(DB_NAMES),
        "successful_connections": success_count,
        "failed_connections": len(DB_NAMES) - success_count,
        "databases": results,
        "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
    }


@app.get("/databases/stats")
def get_databases_stats():
    """
    Obtiene estadísticas de asignaciones para cada base de datos
    
    Returns:
        dict: Estadísticas detalladas por BD
    """
    try:
        stats_by_db = {}
        total_global = {
            "total_asignados": 0,
            "total_sin_direccion": 0,
            "total_registros": 0
        }
        
        for db_name in DB_NAMES:
            try:
                database_url = get_database_url(db_name)
                engine = create_engine(database_url, echo=False, pool_pre_ping=True)
                
                with engine.connect() as connection:
                    result = connection.execute(text("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN court_name IS NOT NULL 
                                AND court_name NOT IN ('Sin dirección', 'Error en geocodificación', 
                                                       'Dirección incorrecta o en otra ciudad', 
                                                       'No se encuentra juzgado en ciudad')
                                THEN 1 ELSE 0 END) as asignados,
                            SUM(CASE WHEN court_name IS NULL 
                                OR court_name IN ('Sin dirección', 'Error en geocodificación', 
                                                 'Dirección incorrecta o en otra ciudad', 
                                                 'No se encuentra juzgado en ciudad')
                                THEN 1 ELSE 0 END) as sin_direccion
                        FROM lawsuit_court_assignments
                    """))
                    stats = result.fetchone()
                    result = connection.execute(text("""
                        SELECT COUNT(*) 
                        FROM court_coordinates cc
                        INNER JOIN data_courts dc ON cc.court_id = dc.id
                        WHERE dc.status = 'Activo' 
                        AND dc.deleted_at IS NULL
                        AND cc.deleted_at IS NULL
                    """))
                    courts_geocoded = result.fetchone()[0]
                    
                    total_count = stats[0] or 0
                    asignados_count = stats[1] or 0
                    sin_direccion_count = stats[2] or 0
                    
                    stats_by_db[db_name] = {
                        "status": "ok",
                        "total_registros": total_count,
                        "asignados": asignados_count,
                        "sin_direccion": sin_direccion_count,
                        "courts_geocoded": courts_geocoded,
                        "percentage_assigned": round((asignados_count / total_count * 100) if total_count > 0 else 0, 2)
                    }
                    
                    total_global["total_registros"] += total_count
                    total_global["total_asignados"] += asignados_count
                    total_global["total_sin_direccion"] += sin_direccion_count
                
                engine.dispose()
                
            except Exception as e:
                stats_by_db[db_name] = {
                    "status": "error",
                    "error": str(e)
                }
        
        return {
            "databases": stats_by_db,
            "global_totals": total_global,
            "total_databases": len(DB_NAMES),
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/databases/list")
def list_databases():
    """
    Lista todas las bases de datos configuradas
    
    Returns:
        dict: Lista de BDs
    """
    return {
        "total": len(DB_NAMES),
        "databases": DB_NAMES,
        "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
    }


@app.get("/clients", response_model=List[ClientProcessed])
def get_processed_clients(
    limit: int = Query(100, description="Número máximo de resultados"),
    offset: int = Query(0, description="Número de resultados a saltar"),
    status: Optional[str] = Query(None, description="Filtrar por estado: 'assigned' o 'no_address'"),
    database: Optional[str] = Query(None, description="Nombre de la base de datos (default: primera BD)")
):
    """
    Lista todos los clientes procesados de una BD específica
    
    - **limit**: Número máximo de resultados (default: 100)
    - **offset**: Paginación (default: 0)
    - **status**: Filtrar por estado ('assigned' o 'no_address')
    - **database**: BD específica (default: primera configurada)
    """
    try:
        # Usar primera BD si no se especifica
        db_name = database if database and database in DB_NAMES else DB_NAMES[0]
        
        database_url = get_database_url(db_name)
        engine = create_engine(database_url, echo=False)
        
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
                query += """ AND court_name IS NOT NULL 
                            AND court_name NOT IN ('Sin dirección', 'Error en geocodificación', 
                                                   'Dirección incorrecta o en otra ciudad', 
                                                   'No se encuentra juzgado en ciudad')"""
            elif status == "no_address":
                query += """ AND (court_name IS NULL 
                            OR court_name IN ('Sin dirección', 'Error en geocodificación', 
                                             'Dirección incorrecta o en otra ciudad', 
                                             'No se encuentra juzgado en ciudad'))"""
            
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
                    "status": "Asignado" if row[4] and row[4] not in [
                        'Sin dirección', 'Error en geocodificación', 
                        'Dirección incorrecta o en otra ciudad', 
                        'No se encuentra juzgado en ciudad'
                    ] else "Sin dirección"
                })
        
        engine.dispose()
        return clients
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    
    # Crear carpeta de logs
    os.makedirs("logs", exist_ok=True)
    
    # Log inicial
    BotController.log(f"🚀 Iniciando API del Bot - Multi-BD ({len(DB_NAMES)} bases de datos)", "INFO")
    
    uvicorn.run(app, host="0.0.0.0", port=8001)