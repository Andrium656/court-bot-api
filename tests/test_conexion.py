import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import urllib.parse

# Cargar variables de entorno
load_dotenv()

# Configuración de la base de datos
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '3306')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = urllib.parse.quote_plus(os.getenv('DB_PASSWORD'))
DB_NAME = os.getenv('DB_NAME')

# Crear conexión
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def test_connection():
    """Prueba la conexión a la base de datos"""
    print("=" * 60)
    print("🔍 PROBANDO CONEXIÓN A LA BASE DE DATOS")
    print("=" * 60)
    
    try:
        engine = create_engine(DATABASE_URL, echo=False)
        
        with engine.connect() as connection:
            # Test 1: Conexión básica
            connection.execute(text("SELECT 1"))
            print("✅ Conexión a MySQL exitosa")
            
            # Test 2: Verificar tabla lawsuit_court_assignments
            result = connection.execute(text(
                "SELECT COUNT(*) as total FROM lawsuit_court_assignments"
            ))
            count = result.fetchone()[0]
            print(f"✅ Tabla lawsuit_court_assignments existe ({count} registros)")
            
            # Test 3: Verificar tabla court_coordinates
            result = connection.execute(text(
                "SELECT COUNT(*) as total FROM court_coordinates"
            ))
            count = result.fetchone()[0]
            print(f"✅ Tabla court_coordinates existe ({count} registros)")
            
            # Test 4: Verificar tabla lawsuits (clientes)
            result = connection.execute(text(
                "SELECT COUNT(*) as total FROM lawsuits WHERE lawsuit_status = 'Pendiente'"
            ))
            count = result.fetchone()[0]
            print(f"✅ Clientes con demanda pendiente: {count}")
            
            # Test 5: Verificar tabla data_courts (juzgados)
            result = connection.execute(text(
                "SELECT COUNT(*) as total FROM data_courts WHERE status = 'Activo' AND deleted_at IS NULL"
            ))
            count = result.fetchone()[0]
            print(f"✅ Juzgados activos disponibles: {count}")
            
            # Test 6: Verificar tabla adresses
            result = connection.execute(text(
                "SELECT COUNT(*) as total FROM adresses"
            ))
            count = result.fetchone()[0]
            print(f"✅ Direcciones en BD: {count}")
            
            print("\n" + "=" * 60)
            print("🎉 TODAS LAS PRUEBAS PASARON EXITOSAMENTE")
            print("=" * 60)
            
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        print("\n💡 Verifica:")
        print("   1. Que el archivo .env tenga los datos correctos")
        print("   2. Que el servidor MySQL esté corriendo")
        print("   3. Que el usuario tenga permisos")
        return False
    
    return True

if __name__ == "__main__":
    test_connection()