from celery import Celery
from app.celery_app import celery_app
from app.bot_control import BotController, BotStatus
from app.core.geocode_courts import sync_court_coordinates
from app.core.assign_courts import process_pending_lawsuits
from app.utils.database import DB_NAMES, get_database_url
from sqlalchemy import create_engine, text
from datetime import datetime
import pytz

COLOMBIA_TZ = pytz.timezone('America/Bogota')


@celery_app.task(name='app.tasks.scheduled_sync_and_assign')
def scheduled_sync_and_assign():
    """
    Tarea programada que ejecuta:
    1. Sincronización de coordenadas de juzgados
    2. Asignación de juzgados a demandas
    
    EN TODAS LAS BASES DE DATOS
    """
    
    # Verificar si puede ejecutarse
    can_run, reason = BotController.can_run()
    if not can_run:
        BotController.log(f"⏸️ [SCHEDULED] Ejecución cancelada: {reason}", "WARNING")
        return {
            "status": "skipped",
            "reason": reason,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
    
    try:
        BotController.update_status(BotStatus.RUNNING)
        BotController.log("🤖 [SCHEDULED] Iniciando ejecución programada en todas las BDs", "INFO")

        BotController.log(f"📍 [SCHEDULED] Sincronizando juzgados en {len(DB_NAMES)} BDs...", "INFO")
        sync_court_coordinates()

        BotController.log(f"⚙️ [SCHEDULED] Procesando asignaciones en {len(DB_NAMES)} BDs...", "INFO")
        process_pending_lawsuits()
        
        stats_by_db = {}
        total_asignados = 0
        total_sin_direccion = 0
        
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
                
                stats_by_db[db_name] = {
                    "total": stats[0],
                    "asignados": stats[1],
                    "sin_direccion": stats[2]
                }
                
                total_asignados += stats[1]
                total_sin_direccion += stats[2]
        
        #detenido
        BotController.update_status(BotStatus.STOPPED)
        BotController.log(
            f"✅ [SCHEDULED] Ejecución completada - "
            f"Total asignados: {total_asignados}, Sin dirección: {total_sin_direccion}",
            "INFO"
        )
        
        return {
            "status": "success",
            "databases": len(DB_NAMES),
            "total_asignados": total_asignados,
            "total_sin_direccion": total_sin_direccion,
            "stats_by_db": stats_by_db,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except Exception as e:
        error_msg = str(e)
        
        #cuota excedida
        if "OVER_QUERY_LIMIT" in error_msg or "quota" in error_msg.lower():
            BotController.mark_no_credits()
            BotController.log(f"❌ [SCHEDULED] Sin créditos de Google Maps API", "ERROR")
        else:
            BotController.update_status(BotStatus.ERROR, error_msg)
            BotController.log(f"❌ [SCHEDULED] Error: {error_msg}", "ERROR")
        
        return {
            "status": "error",
            "error": error_msg,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }


@celery_app.task(name='app.tasks.manual_execute_bot')
def manual_execute_bot(limit=None):
    """
    Tarea para ejecución manual del bot EN TODAS LAS BDs
    
    Args:
        limit: Número máximo de demandas a procesar por BD (None = todas)
    """
    
    can_run, reason = BotController.can_run()
    if not can_run:
        BotController.log(f"⏸️ [MANUAL] Ejecución cancelada: {reason}", "WARNING")
        return {
            "status": "skipped",
            "reason": reason,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
    
    try:
        BotController.update_status(BotStatus.RUNNING)
        BotController.log(f"🎯 [MANUAL] Ejecución manual iniciada (limit={limit}) en {len(DB_NAMES)} BDs", "INFO")
        
        #procesar en todas las BDs
        sync_court_coordinates()
        process_pending_lawsuits(limit=limit)
        
        stats_by_db = {}
        total_asignados = 0
        
        for db_name in DB_NAMES:
            database_url = get_database_url(db_name)
            engine = create_engine(database_url, echo=False)
            
            with engine.connect() as connection:
                result = connection.execute(text("""
                    SELECT 
                        SUM(CASE WHEN court_name IS NOT NULL 
                            AND court_name NOT IN ('Sin dirección', 'Error en geocodificación', 
                                                   'Dirección incorrecta o en otra ciudad', 
                                                   'No se encuentra juzgado en ciudad')
                            THEN 1 ELSE 0 END) as asignados
                    FROM lawsuit_court_assignments
                """))
                asignados = result.fetchone()[0] or 0
                
                stats_by_db[db_name] = {"asignados": asignados}
                total_asignados += asignados
        
        BotController.update_status(BotStatus.STOPPED)
        BotController.log(f"✅ [MANUAL] Ejecución completada - Total asignados: {total_asignados}", "INFO")
        
        return {
            "status": "success",
            "limit": limit,
            "databases": len(DB_NAMES),
            "total_asignados": total_asignados,
            "stats_by_db": stats_by_db,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }
        
    except Exception as e:
        error_msg = str(e)
        
        if "OVER_QUERY_LIMIT" in error_msg or "quota" in error_msg.lower():
            BotController.mark_no_credits()
            BotController.log(f"❌ [MANUAL] Sin créditos de Google Maps API", "ERROR")
        else:
            BotController.update_status(BotStatus.ERROR, error_msg)
            BotController.log(f"❌ [MANUAL] Error: {error_msg}", "ERROR")
        
        return {
            "status": "error",
            "error": error_msg,
            "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
        }


@celery_app.task(name='app.tasks.reset_daily_api_counter')
def reset_daily_api_counter():
    """Resetea el contador diario de llamadas API a medianoche"""
    old_count = BotController.reset_daily_counter()
    
    BotController.log(
        f"🔄 [SCHEDULED] Contador diario reseteado automáticamente: {old_count} → 0",
        "INFO"
    )
    
    return {
        "status": "success",
        "old_count": old_count,
        "new_count": 0,
        "timestamp": datetime.now(COLOMBIA_TZ).isoformat()
    }