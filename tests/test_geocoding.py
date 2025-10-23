import os
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import urllib.parse
from math import radians, cos, sin, asin, sqrt

# Cargar variables de entorno
load_dotenv()

# Configuración
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '3306')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = urllib.parse.quote_plus(os.getenv('DB_PASSWORD'))
DB_NAME = os.getenv('DB_NAME')
GOOGLE_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def geocode_address(address, city):
    """
    Geocodifica una dirección usando Google Maps Geocoding API
    """
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    
    # Construir dirección completa
    full_address = f"{address}, {city}, Colombia"
    
    params = {
        'address': full_address,
        'key': GOOGLE_API_KEY
    }
    
    print(f"   📍 Geocodificando: {full_address}")
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data['status'] == 'OK':
            location = data['results'][0]['geometry']['location']
            lat = location['lat']
            lng = location['lng']
            print(f"   ✅ Coordenadas: ({lat}, {lng})")
            return lat, lng
        else:
            print(f"   ❌ Error geocodificando: {data['status']}")
            return None, None
            
    except Exception as e:
        print(f"   ❌ Error en API: {str(e)}")
        return None, None


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calcula la distancia en kilómetros entre dos puntos (en línea recta)
    usando la fórmula de Haversine
    """
    # Convertir a radianes
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    
    # Fórmula de Haversine
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    # Radio de la Tierra en kilómetros
    r = 6371
    
    return c * r


def test_basic_assignment():
    """
    Prueba básica: tomar 1 cliente y 1 juzgado, calcular distancia
    """
    print("=" * 70)
    print("🧪 PRUEBA BÁSICA DE ASIGNACIÓN")
    print("=" * 70)
    
    engine = create_engine(DATABASE_URL, echo=False)
    
    with engine.connect() as connection:
        
        # 1. Obtener UN cliente pendiente
        print("\n1️⃣ Obteniendo cliente pendiente...")
        query = text("""
            SELECT 
                l.id as lawsuit_id,
                l.client_id,
                l.lawsuit_status
            FROM lawsuits l
            WHERE l.lawsuit_status = 'Pendiente'
            LIMIT 1
        """)
        
        result = connection.execute(query)
        lawsuit = result.fetchone()
        
        if not lawsuit:
            print("❌ No hay clientes pendientes")
            return
        
        lawsuit_id = lawsuit[0]
        client_id = lawsuit[1]
        
        print(f"   ✅ Cliente encontrado: lawsuit_id={lawsuit_id}, client_id={client_id}")
        
        
        # 2. Obtener dirección del cliente
        print("\n2️⃣ Obteniendo dirección del cliente...")
        query = text("""
            SELECT 
                a.adress,
                a.city_id,
                a.neighborhood
            FROM adresses a
            WHERE a.client_id = :client_id
            LIMIT 1
        """)
        
        result = connection.execute(query, {"client_id": client_id})
        address_row = result.fetchone()
        
        if not address_row:
            print(f"   ❌ No se encontró dirección para client_id={client_id}")
            return
        
        client_address = address_row[0]
        city_id = address_row[1]
        neighborhood = address_row[2] or ""
        
        print(f"   ✅ Dirección: {client_address}")
        print(f"   ✅ Barrio: {neighborhood}")
        print(f"   ✅ City ID: {city_id}")
        
        
        # 3. Obtener nombre de la ciudad
        print("\n3️⃣ Obteniendo nombre de ciudad...")
        query = text("""
            SELECT city_name
            FROM v_cities
            WHERE id = :city_id
            LIMIT 1
        """)

        result = connection.execute(query, {"city_id": city_id})
        city_row = result.fetchone()

        if not city_row:
            print(f"   ❌ No se encontró ciudad con id={city_id}")
            return

        city_name = city_row[0]
        print(f"   ✅ Ciudad: {city_name}")
                
        # 4. Geocodificar dirección del cliente
        print("\n4️⃣ Geocodificando dirección del cliente...")
        client_lat, client_lng = geocode_address(client_address, city_name)
        
        if not client_lat:
            print("   ❌ No se pudo geocodificar la dirección del cliente")
            return
        
        
        # 5. Obtener UN juzgado activo
        print("\n5️⃣ Obteniendo juzgado activo...")
        query = text("""
            SELECT 
                id,
                name,
                adress,
                city,
                type_cuantity
            FROM data_courts
            WHERE status = 'Activo' 
            AND deleted_at IS NULL
            LIMIT 1
        """)
        
        result = connection.execute(query)
        court = result.fetchone()
        
        if not court:
            print("   ❌ No hay juzgados activos")
            return
        
        court_id = court[0]
        court_name = court[1]
        court_address = court[2]
        court_city = court[3]
        court_cuantia = court[4]
        
        print(f"   ✅ Juzgado: {court_name}")
        print(f"   ✅ Dirección: {court_address}")
        print(f"   ✅ Ciudad: {court_city}")
        print(f"   ✅ Cuantía: {court_cuantia}")
        
        
        # 6. Geocodificar dirección del juzgado
        print("\n6️⃣ Geocodificando dirección del juzgado...")
        court_lat, court_lng = geocode_address(court_address, court_city)
        
        if not court_lat:
            print("   ❌ No se pudo geocodificar la dirección del juzgado")
            return
        
        
        # 7. Calcular distancia
        print("\n7️⃣ Calculando distancia...")
        distance_km = haversine_distance(client_lat, client_lng, court_lat, court_lng)
        print(f"   ✅ Distancia en línea recta: {distance_km:.2f} km")
        
        
        # 8. Resumen
        print("\n" + "=" * 70)
        print("📊 RESUMEN DE LA PRUEBA")
        print("=" * 70)
        print(f"Cliente:")
        print(f"  - Lawsuit ID: {lawsuit_id}")
        print(f"  - Dirección: {client_address}, {city_name}")
        print(f"  - Coordenadas: ({client_lat}, {client_lng})")
        print()
        print(f"Juzgado:")
        print(f"  - Nombre: {court_name}")
        print(f"  - Dirección: {court_address}, {court_city}")
        print(f"  - Cuantía: {court_cuantia}")
        print(f"  - Coordenadas: ({court_lat}, {court_lng})")
        print()
        print(f"📏 Distancia: {distance_km:.2f} km")
        print("=" * 70)
        print("✅ PRUEBA COMPLETADA EXITOSAMENTE")
        print("=" * 70)


if __name__ == "__main__":
    test_basic_assignment()