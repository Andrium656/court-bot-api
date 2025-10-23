import os
import requests
import time
import hashlib
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import urllib.parse

load_dotenv()

DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '3306')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = urllib.parse.quote_plus(os.getenv('DB_PASSWORD'))
DB_NAME = os.getenv('DB_NAME')
GOOGLE_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def calculate_court_hash(court_id, address, city):
    """
    Calcula un hash único basado en los datos del juzgado
    
    Si este hash NO cambia = Los datos son los mismos = NO llamar a API
    """
    data_string = f"{court_id}|{address}|{city}"
    return hashlib.sha256(data_string.encode('utf-8')).hexdigest()


def geocode_address(address, city):
    """Geocodifica una dirección usando Google Maps"""
    
    #incrementar contador de llamadas API
    from app.bot_control import BotController
    BotController.increment_api_calls()
    
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    full_address = f"{address}, {city}, Colombia"
    
    params = {
        'address': full_address,
        'key': GOOGLE_API_KEY
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data['status'] == 'OK':
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
        else:
            print(f"      ⚠️  Error: {data['status']}")
            return None, None
            
    except Exception as e:
        print(f"      ❌ Error: {str(e)}")
        return None, None


def sync_court_coordinates():
    """
    Sincroniza las coordenadas de los juzgados:
    1. Sincroniza deleted_at entre data_courts y court_coordinates
    2. Geocodifica juzgados nuevos (sin coordenadas)
    3. Actualiza SOLO juzgados que cambiaron de dirección (usando hash)
    4. Elimina coordenadas de juzgados que ya no existen
    
    ✅ OPTIMIZADO: No vuelve a llamar API si los datos no cambiaron
    """
    print("=" * 70)
    print("🔄 SINCRONIZANDO COORDENADAS DE JUZGADOS")
    print("=" * 70)
    
    engine = create_engine(DATABASE_URL, echo=False)
    
    with engine.connect() as connection:
        print("\n1️⃣ Sincronizando deleted_at...")
        
        # Copiar deleted_at de juzgados inactivos
        sync_deleted_query = text("""
            UPDATE court_coordinates cc
            INNER JOIN data_courts dc ON cc.court_id = dc.id
            SET cc.deleted_at = dc.deleted_at
            WHERE dc.deleted_at IS NOT NULL
            AND cc.deleted_at IS NULL
        """)
        
        result = connection.execute(sync_deleted_query)
        marked_inactive = result.rowcount
        connection.commit()
        
        # Quitar deleted_at de juzgados rehabilitados
        sync_active_query = text("""
            UPDATE court_coordinates cc
            INNER JOIN data_courts dc ON cc.court_id = dc.id
            SET cc.deleted_at = NULL
            WHERE dc.deleted_at IS NULL
            AND cc.deleted_at IS NOT NULL
        """)
        
        result = connection.execute(sync_active_query)
        marked_active = result.rowcount
        connection.commit()
        
        if marked_inactive > 0:
            print(f"   ✅ Juzgados marcados como inactivos: {marked_inactive}")
        if marked_active > 0:
            print(f"   ✅ Juzgados rehabilitados: {marked_active}")
        if marked_inactive == 0 and marked_active == 0:
            print(f"   ✅ Todos los deleted_at están sincronizados")
        
        
        #eliminar coordenadas que ya no existen
        print("\n2️⃣ Limpiando juzgados que ya no existen...")
        delete_query = text("""
            DELETE cc FROM court_coordinates cc
            LEFT JOIN data_courts dc ON cc.court_id = dc.id
            WHERE dc.id IS NULL
        """)
        
        result = connection.execute(delete_query)
        deleted_count = result.rowcount
        connection.commit()
        
        if deleted_count > 0:
            print(f"   ✅ Eliminadas {deleted_count} coordenadas huérfanas")
        else:
            print(f"   ✅ No hay coordenadas huérfanas")
        
        
        #detectar son el hash si cambiaron de dirección
        print("\n3️⃣ Detectando juzgados con dirección actualizada...")
        
        # Obtener todos los juzgados activos con coordenadas
        check_query = text("""
            SELECT 
                dc.id,
                dc.name,
                dc.adress,
                dc.city,
                cc.data_hash
            FROM data_courts dc
            INNER JOIN court_coordinates cc ON dc.id = cc.court_id
            WHERE dc.status = 'Activo' 
            AND dc.deleted_at IS NULL
            AND cc.deleted_at IS NULL
        """)
        
        result = connection.execute(check_query)
        existing_courts = result.fetchall()
        
        updated_courts = []
        
        for court in existing_courts:
            court_id = court[0]
            court_name = court[1]
            current_address = court[2]
            current_city = court[3]
            stored_hash = court[4]
            
            # Calcular hash actual
            current_hash = calculate_court_hash(court_id, current_address, current_city)
            
            # Si el hash cambió, la dirección cambió
            if stored_hash != current_hash:
                updated_courts.append({
                    'id': court_id,
                    'name': court_name,
                    'address': current_address,
                    'city': current_city,
                    'new_hash': current_hash
                })
        
        updated_count = len(updated_courts)
        
        if updated_count > 0:
            print(f"   ⚠️  {updated_count} juzgados con dirección actualizada")
            
            for court in updated_courts:
                court_id = court['id']
                court_name = court['name']
                new_address = court['address']
                new_city = court['city']
                new_hash = court['new_hash']
                
                print(f"\n   📝 {court_name}")
                print(f"      Nueva dirección: {new_address}, {new_city}")
                
                # Re-geocodificar
                lat, lng = geocode_address(new_address, new_city)
                
                if lat and lng:
                    # Actualizar coordenadas Y hash
                    update_coord_query = text("""
                        UPDATE court_coordinates
                        SET latitude = :lat,
                            longitude = :lng,
                            geocoded_address = :address,
                            data_hash = :hash,
                            updated_at = NOW()
                        WHERE court_id = :court_id
                    """)
                    
                    connection.execute(update_coord_query, {
                        "court_id": court_id,
                        "lat": lat,
                        "lng": lng,
                        "address": f"{new_address}, {new_city}",
                        "hash": new_hash
                    })
                    connection.commit()
                    
                    print(f"      ✅ Actualizado: ({lat}, {lng})")
                else:
                    print(f"      ❌ Error al geocodificar")
                
                time.sleep(0.1)
        else:
            print(f"   ✅ No hay direcciones actualizadas")
        
        
        #geocodificar juzgados nuevos
        print("\n4️⃣ Geocodificando juzgados nuevos...")
        new_query = text("""
            SELECT 
                dc.id,
                dc.name,
                dc.adress,
                dc.city,
                dc.type_cuantity
            FROM data_courts dc
            LEFT JOIN court_coordinates cc ON dc.id = cc.court_id
            WHERE dc.status = 'Activo' 
            AND dc.deleted_at IS NULL
            AND cc.id IS NULL
        """)
        
        result = connection.execute(new_query)
        new_courts = result.fetchall()
        
        new_count = len(new_courts)
        
        if new_count > 0:
            print(f"   🆕 {new_count} juzgados nuevos por geocodificar\n")
            
            success_count = 0
            error_count = 0
            
            for i, court in enumerate(new_courts, 1):
                court_id = court[0]
                court_name = court[1]
                court_address = court[2]
                court_city = court[3]
                court_cuantia = court[4]
                
                print(f"   [{i}/{new_count}] {court_name} ({court_cuantia})")
                print(f"      📍 {court_address}, {court_city}")
                
                # Calcular hash
                court_hash = calculate_court_hash(court_id, court_address, court_city)
                
                # Geocodificar
                lat, lng = geocode_address(court_address, court_city)
                
                if lat and lng:
                    # Guardar en BD con hash
                    insert_query = text("""
                        INSERT INTO court_coordinates 
                        (court_id, latitude, longitude, geocoded_address, data_hash)
                        VALUES (:court_id, :lat, :lng, :address, :hash)
                    """)
                    
                    connection.execute(insert_query, {
                        "court_id": court_id,
                        "lat": lat,
                        "lng": lng,
                        "address": f"{court_address}, {court_city}",
                        "hash": court_hash
                    })
                    connection.commit()
                    
                    print(f"      ✅ Guardado: ({lat}, {lng})")
                    success_count += 1
                else:
                    error_count += 1
                
                time.sleep(0.1)
                print()
            
            print(f"   ✅ Nuevos geocodificados: {success_count}")
            if error_count > 0:
                print(f"   ❌ Errores: {error_count}")
        else:
            print(f"   ✅ No hay juzgados nuevos")
        
        
        #resumen final
        print("\n" + "=" * 70)
        print("📊 RESUMEN DE SINCRONIZACIÓN")
        print("=" * 70)
        
        verify_query = text("""
            SELECT COUNT(*) 
            FROM court_coordinates cc
            INNER JOIN data_courts dc ON cc.court_id = dc.id
            WHERE dc.status = 'Activo' 
            AND dc.deleted_at IS NULL
            AND cc.deleted_at IS NULL
        """)
        result = connection.execute(verify_query)
        total_active = result.fetchone()[0]
        
        courts_query = text("""
            SELECT COUNT(*) 
            FROM data_courts 
            WHERE status = 'Activo' AND deleted_at IS NULL
        """)
        result = connection.execute(courts_query)
        total_courts = result.fetchone()[0]
        
        print(f"🏛️  Juzgados activos: {total_courts}")
        print(f"📍 Juzgados geocodificados: {total_active}")
        print(f"🔄 Sincronizaciones deleted_at: {marked_inactive + marked_active}")
        print(f"❌ Coordenadas eliminadas: {deleted_count}")
        print(f"🔄 Direcciones actualizadas: {updated_count}")
        print(f"🆕 Nuevos geocodificados: {success_count if new_count > 0 else 0}")
        
        
        skipped_count = len(existing_courts) - updated_count
        if skipped_count > 0:
            print(f"💰 Llamadas API ahorradas: {skipped_count}")
        
        if total_active == total_courts:
            print("\n✅ Todos los juzgados activos están geocodificados")
        else:
            missing = total_courts - total_active
            print(f"\n⚠️  {missing} juzgados sin geocodificar (probablemente con errores)")
        
        print("=" * 70)


if __name__ == "__main__":
    sync_court_coordinates()