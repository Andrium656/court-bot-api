from app.celery_app import celery_app
from app.core.geocode_courts import sync_court_coordinates
from app.core.assign_courts import process_pending_lawsuits
from app.bot_control import BotController, BotStatus
from datetime import datetime
import logging


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@celery_app.task(name='app.tasks.scheduled_sync_and_assign')
def scheduled_sync_and_assign():
    """
    Tarea PROGRAMADA: 
    1. Sincronizar coordenadas de juzgados
    2. Asignar juzgados a clientes pendientes
    
    Se ejecuta automáticamente según configuración en celery_app.py
    """
    # Verificar si el bot puede ejecutarse
    can_run, reason = BotController.can_run()
    if not can_run:
        BotController.log(f"⏭️ Ejecución programada omitida: {reason}", "WARNING")
        return {
            "status": "skipped",
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
    
    BotController.log("🚀 [AUTO] Iniciando proceso programado completo...", "INFO")
    
    try:
        #marcar como ejecutando
        BotController.update_status(BotStatus.RUNNING)
        
        #sincronizar juzgados
        BotController.log("📍 [AUTO] Paso 1/2: Sincronizando juzgados...", "INFO")
        sync_court_coordinates()
        BotController.log("✅ [AUTO] Sincronización completada", "INFO")
        
        #asignar clientes
        BotController.log("⚙️ [AUTO] Paso 2/2: Procesando asignaciones...", "INFO")
        
        # Capturar output
        import io
        import sys
        
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        
        #procesar clientes
        process_pending_lawsuits(limit=None)
        
        output = buffer.getvalue()
        sys.stdout = old_stdout
        
        #detendio
        BotController.update_status(BotStatus.STOPPED)
        BotController.log("✅ [AUTO] Proceso programado completado exitosamente", "INFO")
        
        return {
            "status": "success",
            "type": "scheduled",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        error_msg = str(e)
        
        #error por exceder cuotade la api de google maps
        if "OVER_QUERY_LIMIT" in error_msg or "quota" in error_msg.lower():
            BotController.mark_no_credits()
            BotController.log(f"❌ [AUTO] Sin créditos de Google Maps API", "ERROR")
        else:
            BotController.update_status(BotStatus.ERROR, error_msg)
            BotController.log(f"❌ [AUTO] Error en proceso programado: {error_msg}", "ERROR")
        
        raise


@celery_app.task(name='app.tasks.manual_execute_bot')
def manual_execute_bot(limit=None):
    """
    Tarea MANUAL: Ejecutar bot desde endpoint /execute
    NO interfiere con las tareas programadas
    """
    BotController.log(f"🎯 [MANUAL] Ejecución manual iniciada (limit={limit})", "INFO")
    
    #Verificar si el bot puede ejecutarse
    can_run, reason = BotController.can_run()
    if not can_run:
        BotController.log(f"❌ [MANUAL] Ejecución bloqueada: {reason}", "ERROR")
        raise Exception(reason)
    
    try:
        #Marcar como ejecutando
        BotController.update_status(BotStatus.RUNNING)
        
        #Sincronizar juzgados primero
        BotController.log("📍 [MANUAL] Sincronizando juzgados...", "INFO")
        sync_court_coordinates()
        
        #Procesar asignaciones
        BotController.log(f"⚙️ [MANUAL] Procesando asignaciones (limit={limit})...", "INFO")
        
        import io
        import sys
        
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        
        process_pending_lawsuits(limit=limit)
        
        output = buffer.getvalue()
        sys.stdout = old_stdout
        
        # Marcar como detenido
        BotController.update_status(BotStatus.STOPPED)
        BotController.log(f"✅ [MANUAL] Ejecución manual completada", "INFO")
        
        return {
            "status": "success",
            "type": "manual",
            "limit": limit,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        error_msg = str(e)
        
        # Detectar error de cuota excedida
        if "OVER_QUERY_LIMIT" in error_msg or "quota" in error_msg.lower():
            BotController.mark_no_credits()
            BotController.log(f"❌ [MANUAL] Sin créditos de Google Maps API", "ERROR")
        else:
            BotController.update_status(BotStatus.ERROR, error_msg)
            BotController.log(f"❌ [MANUAL] Error: {error_msg}", "ERROR")
        
        raise

@celery_app.task(name='app.tasks.reset_daily_api_counter')
def reset_daily_api_counter():
    """
    Resetea el contador de llamadas API diario a medianoche
    El contador mensual se resetea automáticamente al cambiar de mes
    """
    from app.bot_control import BotController
    
    old_count = BotController.reset_daily_counter()
    
    #Obtener estadísticas actuales
    usage = BotController.get_api_usage()
    
    BotController.log(
        f"📊 Resumen diario - Llamadas: {old_count} | Mes actual: {usage['monthly']['calls']}/{usage['monthly']['limit']}", 
        "INFO"
    )
    
    return {
        "status": "success",
        "daily_calls_yesterday": old_count,
        "monthly_calls_total": usage['monthly']['calls'],
        "timestamp": datetime.now().isoformat()
    }