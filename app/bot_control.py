import json
import os
from datetime import datetime
from enum import Enum
import pytz

STATE_FILE = "bot_state.json"
LOG_FILE = "logs/bot_execution.log"
MAX_LOG_LINES = 300
ROTATION_CHECK_INTERVAL = 50

#limites de la api
MAX_API_CALLS_PER_DAY = 520
MAX_API_CALLS_PER_MONTH = 5000

COLOMBIA_TZ = pytz.timezone('America/Bogota')


class BotStatus(str, Enum): 
    RUNNING = "running"
    STOPPED = "stopped"
    NO_API_CREDITS = "no_api_credits"
    ERROR = "error"


class BotController:
    
    _log_counter = 0
    
    @staticmethod
    def get_state():
        """Estado actual del bot"""
        if not os.path.exists(STATE_FILE):
            initial_state = {
                "status": BotStatus.STOPPED,
                "last_execution": None,
                "last_error": None,
                "api_calls_today": 0,
                "api_calls_month": 0,
                "current_month": datetime.now(COLOMBIA_TZ).strftime("%Y-%m"),
                "api_quota_exceeded": False,
                "is_manual_stopped": False
            }
            BotController.save_state(initial_state)
            return initial_state
        
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    
    @staticmethod
    def save_state(state):
        """Guarda el estado del bot"""
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    
    @staticmethod
    def update_status(status: BotStatus, error_message=None):
        """Actualiza el estado del bot"""
        state = BotController.get_state()
        state["status"] = status

        now_colombia = datetime.now(COLOMBIA_TZ)
        state["last_execution"] = now_colombia.isoformat()
        
        if error_message:
            state["last_error"] = {
                "message": error_message,
                "timestamp": now_colombia.isoformat()
            }
        
        BotController.save_state(state)
    
    @staticmethod
    def increment_api_calls():
        """
        Incrementa los contadores de llamadas API (diario y mensual)
        ✅ Verifica límites ANTES de permitir más llamadas
        """
        state = BotController.get_state()
        now_colombia = datetime.now(COLOMBIA_TZ)
        current_month = now_colombia.strftime("%Y-%m")
        
        #Resetear contador mensual si cambió el mes
        if state.get("current_month") != current_month:
            BotController.log(f"🔄 Nuevo mes detectado: {current_month}. Reseteando contador mensual.", "INFO")
            state["api_calls_month"] = 0
            state["current_month"] = current_month
            state["api_quota_exceeded"] = False
        
        #Incrementar contadores
        state["api_calls_today"] = state.get("api_calls_today", 0) + 1
        state["api_calls_month"] = state.get("api_calls_month", 0) + 1
        
        #Verificar límite MENSUAL primero
        if state["api_calls_month"] >= MAX_API_CALLS_PER_MONTH:
            state["api_quota_exceeded"] = True
            BotController.save_state(state)
            BotController.log(
                f"🚨 LÍMITE MENSUAL ALCANZADO: {state['api_calls_month']}/{MAX_API_CALLS_PER_MONTH} llamadas", 
                "ERROR"
            )
            raise Exception(f"Límite mensual de API alcanzado ({MAX_API_CALLS_PER_MONTH} llamadas)")
        
        #Verificar límite DIARIO
        if state["api_calls_today"] >= MAX_API_CALLS_PER_DAY:
            state["api_quota_exceeded"] = True
            BotController.save_state(state)
            BotController.log(
                f"⚠️ Límite diario alcanzado: {state['api_calls_today']}/{MAX_API_CALLS_PER_DAY} llamadas. Bot detenido hasta mañana.", 
                "WARNING"
            )
            raise Exception(f"Límite diario de API alcanzado ({MAX_API_CALLS_PER_DAY} llamadas)")
        
        #Advertencia al 80% del límite mensual
        if state["api_calls_month"] >= MAX_API_CALLS_PER_MONTH * 0.8:
            percentage = (state["api_calls_month"] / MAX_API_CALLS_PER_MONTH) * 100
            BotController.log(
                f"⚠️ ADVERTENCIA: {percentage:.1f}% del límite mensual usado ({state['api_calls_month']}/{MAX_API_CALLS_PER_MONTH})", 
                "WARNING"
            )
        
        BotController.save_state(state)
    
    @staticmethod
    def mark_no_credits():
        """Marca que se acabaron los créditos de la API"""
        state = BotController.get_state()
        state["status"] = BotStatus.NO_API_CREDITS
        state["api_quota_exceeded"] = True

        now_colombia = datetime.now(COLOMBIA_TZ)
        state["last_error"] = {
            "message": "Se agotaron los créditos de Google Maps API",
            "timestamp": now_colombia.isoformat()
        }
        BotController.save_state(state)
    
    @staticmethod
    def manual_stop():
        """Detiene el bot manualmente"""
        state = BotController.get_state()
        state["status"] = BotStatus.STOPPED
        state["is_manual_stopped"] = True
        BotController.save_state(state)
    
    @staticmethod
    def manual_start():
        """Inicia el bot manualmente"""
        state = BotController.get_state()
        state["status"] = BotStatus.STOPPED
        state["is_manual_stopped"] = False
        state["api_quota_exceeded"] = False
        BotController.save_state(state)
    
    @staticmethod
    def can_run():
        """Verifica si el bot puede ejecutarse"""
        state = BotController.get_state()
        
        # No puede ejecutarse si está detenido manualmente
        if state.get("is_manual_stopped"):
            return False, "Bot detenido manualmente"
        
        # No puede ejecutarse si no hay créditos
        if state.get("api_quota_exceeded"):
            return False, "Sin créditos de Google Maps API"
        
        return True, "OK"
    
    @staticmethod
    def reset_daily_counter():
        """Resetea el contador diario (llamado automáticamente a medianoche)"""
        state = BotController.get_state()
        old_count = state.get("api_calls_today", 0)
        
        state["api_calls_today"] = 0
        state["api_quota_exceeded"] = False
        
        BotController.save_state(state)
        BotController.log(f"🔄 Contador diario reseteado: {old_count} → 0", "INFO")
        
        return old_count
    
    @staticmethod
    def get_api_usage():
        """
        Obtiene estadísticas de uso de la API
        Returns: dict con estadísticas diarias y mensuales
        """
        state = BotController.get_state()
        
        daily_calls = state.get("api_calls_today", 0)
        monthly_calls = state.get("api_calls_month", 0)
        
        daily_remaining = max(0, MAX_API_CALLS_PER_DAY - daily_calls)
        monthly_remaining = max(0, MAX_API_CALLS_PER_MONTH - monthly_calls)
        
        daily_percentage = (daily_calls / MAX_API_CALLS_PER_DAY) * 100
        monthly_percentage = (monthly_calls / MAX_API_CALLS_PER_MONTH) * 100
        
        # Determinar estado
        if monthly_calls >= MAX_API_CALLS_PER_MONTH:
            status = "CRITICAL - Límite mensual alcanzado"
        elif daily_calls >= MAX_API_CALLS_PER_DAY:
            status = "WARNING - Límite diario alcanzado"
        elif monthly_percentage >= 80:
            status = "WARNING - 80% del límite mensual usado"
        elif daily_percentage >= 80:
            status = "CAUTION - 80% del límite diario usado"
        else:
            status = "OK"
        
        return {
            "status": status,
            "daily": {
                "calls": daily_calls,
                "limit": MAX_API_CALLS_PER_DAY,
                "remaining": daily_remaining,
                "percentage": round(daily_percentage, 2)
            },
            "monthly": {
                "calls": monthly_calls,
                "limit": MAX_API_CALLS_PER_MONTH,
                "remaining": monthly_remaining,
                "percentage": round(monthly_percentage, 2),
                "current_month": state.get("current_month", "N/A")
            },
            "quota_exceeded": state.get("api_quota_exceeded", False)
        }
    
    @staticmethod
    def rotate_logs():
        """Rota el archivo de logs si excede el límite de líneas"""
        if not os.path.exists(LOG_FILE):
            return
        
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            if len(lines) > MAX_LOG_LINES:
                now_colombia = datetime.now(COLOMBIA_TZ)
                timestamp = now_colombia.strftime("%Y-%m-%d %H:%M:%S")
                
                with open(LOG_FILE, 'w', encoding='utf-8') as f:
                    f.write(f"[{timestamp}] [INFO] ♻️ Log rotado automáticamente - Conservadas últimas {MAX_LOG_LINES} líneas\n")
                    f.writelines(lines[-MAX_LOG_LINES:])
                
                print(f"✅ Logs rotados: {len(lines)} → {MAX_LOG_LINES} líneas")
        
        except Exception as e:
            print(f"⚠️ Error al rotar logs: {str(e)}")
    
    @staticmethod
    def log(message, level="INFO"):
        """Registra un mensaje en el archivo de logs"""
        os.makedirs("logs", exist_ok=True)

        now_colombia = datetime.now(COLOMBIA_TZ)
        timestamp = now_colombia.strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{level}] {message}\n"
        
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry)
        
        print(log_entry.strip())
        
        BotController._log_counter += 1
        if BotController._log_counter >= ROTATION_CHECK_INTERVAL:
            BotController.rotate_logs()
            BotController._log_counter = 0
    
    @staticmethod
    def get_logs(lines=100):
        """Obtiene las últimas N líneas del log"""
        if not os.path.exists(LOG_FILE):
            return []
        
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            return all_lines[-lines:]