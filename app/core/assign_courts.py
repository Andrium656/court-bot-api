import os
import requests
import time
import hashlib
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from geopy.distance import geodesic
import urllib.parse

load_dotenv()

DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '3306')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = urllib.parse.quote_plus(os.getenv('DB_PASSWORD'))
DB_NAME = os.getenv('DB_NAME')
GOOGLE_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def calculate_client_hash(lawsuit_id, address, neighborhood, city, department, type_quantity):
    """
    Calcula un hash único basado en los datos relevantes del cliente
    
    Si este hash NO cambia = Los datos son los mismos = NO llamar a API
    """
    # Crear string con todos los datos relevantes (usar cadenas vacías para None)
    data_string = (
        f"{lawsuit_id}|"
        f"{address or ''}|"
        f"{neighborhood or ''}|"
        f"{city or ''}|"
        f"{department or ''}|"
        f"{type_quantity or ''}"
    )
    
    # Calcular hash SHA256
    return hashlib.sha256(data_string.encode('utf-8')).hexdigest()


def normalize_city(city):
    """Normaliza nombre de ciudad (mayúsculas, sin espacios extra)"""
    if not city:
        return None
    return city.strip().upper()


def geocode_address(address, city, department=None, neighborhood=None):
    """
    Geocodifica una dirección usando Google Maps y valida la ciudad
    
    Args:
        address: Dirección del cliente
        city: Ciudad del cliente
        department: Departamento del cliente (opcional pero recomendado)
        neighborhood: Barrio del cliente (opcional)
    
    Returns:
        tuple: (lat, lng, found_city) o (None, None, None) si falla
    """
    # Incrementar contador ANTES de hacer la llamada
    from app.bot_control import BotController
    BotController.increment_api_calls()

    url = "https://maps.googleapis.com/maps/api/geocode/json"
    
    # Construir dirección completa: dirección, barrio, ciudad, departamento, país
    address_parts = [address]
    
    if neighborhood:
        address_parts.append(neighborhood)
    
    address_parts.append(city)
    
    if department:
        address_parts.append(department)
    
    address_parts.append("Colombia")
    
    full_address = ", ".join(address_parts)
    
    params = {
        'address': full_address,
        'key': GOOGLE_API_KEY
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data['status'] == 'OK':
            result = data['results'][0]
            location = result['geometry']['location']
            
            # Extraer ciudad de los componentes de dirección
            found_city = None
            for component in result['address_components']:
                if 'locality' in component['types']:
                    found_city = component['long_name']
                    break
                elif 'administrative_area_level_2' in component['types']:
                    found_city = component['long_name']
                    break
            
            return location['lat'], location['lng'], found_city
        else:
            print(f"      ⚠️  Error geocodificación: {data['status']}")
            return None, None, None
            
    except Exception as e:
        print(f"      ❌ Error en API: {str(e)}")
        return None, None, None


def get_distance_matrix(origin_lat, origin_lng, destinations):
    """
    Calcula distancias reales por carretera usando Google Distance Matrix API
    
    destinations: lista de tuplas (lat, lng, court_id, court_name)
    """
    if not destinations:
        return []

    # Incrementar contador ANTES de hacer la llamada
    from app.bot_control import BotController
    BotController.increment_api_calls()
    
    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    
    origin = f"{origin_lat},{origin_lng}"
    dest_coords = [f"{lat},{lng}" for lat, lng, _, _ in destinations]
    
    params = {
        'origins': origin,
        'destinations': '|'.join(dest_coords),
        'key': GOOGLE_API_KEY,
        'mode': 'driving',
        'units': 'metric'
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data['status'] == 'OK':
            results = []
            elements = data['rows'][0]['elements']
            
            for i, element in enumerate(elements):
                if element['status'] == 'OK':
                    distance_km = element['distance']['value'] / 1000
                    lat, lng, court_id, court_name = destinations[i]
                    results.append({
                        'court_id': court_id,
                        'court_name': court_name,
                        'distance_km': distance_km,
                        'lat': lat,
                        'lng': lng
                    })
            
            return sorted(results, key=lambda x: x['distance_km'])
        
        return []
        
    except Exception as e:
        print(f"      ❌ Error en Distance Matrix: {str(e)}")
        return []


def process_pending_lawsuits(limit=None):
    """
    Procesa demandas pendientes y asigna/actualiza juzgados
    
    ✅ OPTIMIZADO: Solo geocodifica si los datos del cliente cambiaron (usando hash)
    
    REGLAS:
    1. Calcular hash de datos del cliente (lawsuit_id, dirección, barrio, ciudad, departamento, cuantía)
    2. Si el hash es igual al guardado Y ya tiene juzgado asignado → OMITIR (ahorra llamadas API)
    3. Si el hash cambió O no tiene juzgado → PROCESAR
    4. Actualizar hash después de procesar
    """
    engine = create_engine(DATABASE_URL, echo=False)
    
    with engine.connect() as connection:
        
        #obtener demandas activas pero pendientes
        query = """
            SELECT 
                l.id as lawsuit_id,
                l.client_id,
                l.type_quantity,
                c.identification,
                a.adress,
                a.neighborhood,
                vc.city_name as city,
                vc.department as department
            FROM lawsuits l
            INNER JOIN clients c ON l.client_id = c.id
            LEFT JOIN adresses a ON c.id = a.client_id AND a.deleted_at IS NULL
            LEFT JOIN v_cities vc ON a.city_id = vc.id
            WHERE l.lawsuit_status = 'Pendiente'
            AND l.deleted_at IS NULL
            AND c.deleted_at IS NULL
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        result = connection.execute(text(query))
        lawsuits = result.fetchall()
        
        if not lawsuits:
            print("\n✅ No hay demandas pendientes para procesar")
            return
        
        print(f"\n📋 Demandas a procesar: {len(lawsuits)}")
        print("=" * 70)
        
        success_count = 0
        no_address_count = 0
        no_court_in_city_count = 0
        wrong_city_count = 0
        error_count = 0
        updated_count = 0
        inserted_count = 0
        skipped_count = 0
        
        for i, lawsuit in enumerate(lawsuits, 1):
            lawsuit_id = lawsuit[0]
            client_id = lawsuit[1]
            type_quantity = lawsuit[2]
            client_identification = lawsuit[3]
            client_address = lawsuit[4]
            client_neighborhood = lawsuit[5]
            client_city = lawsuit[6]
            client_department = lawsuit[7]
            
            print(f"\n[{i}/{len(lawsuits)}] Cliente: {client_identification} (Lawsuit ID: {lawsuit_id}, Client ID: {client_id})")
            print(f"   Tipo cuantía: {type_quantity}")
            print(f"   Ciudad: {client_city or 'N/A'}")
            
            # Calcular hash y verificar si cambio
            current_hash = calculate_client_hash(
                lawsuit_id,
                client_address,
                client_neighborhood,
                client_city,
                client_department,
                type_quantity
            )
            
            # Verificar si ya existe registro
            check_query = text("""
                SELECT id, data_hash, court_name 
                FROM lawsuit_court_assignments 
                WHERE client_id = :client_id
            """)
            existing = connection.execute(check_query, {"client_id": client_id}).fetchone()
            
            if existing:
                existing_id = existing[0]
                stored_hash = existing[1]
                existing_court = existing[2]
                
                #si el hash es igual Y ya tiene juzgado asignado → OMITIR
                if stored_hash == current_hash and existing_court and existing_court not in [
                    "Sin dirección",
                    "Error en geocodificación",
                    "Dirección incorrecta o en otra ciudad",
                    "No se encuentra juzgado en ciudad"
                ]:
                    print(f"   ⏭️  Sin cambios detectados - Omitiendo (ahorrando llamadas API)")
                    skipped_count += 1
                    continue
                else:
                    print(f"   🔄 Actualizando registro existente (ID: {existing_id}) - Datos cambiaron")
            else:
                print(f"   🆕 Creando nuevo registro")
            
            #validaqr que tenga direccion y ciudad
            if not client_address or not client_city:
                print(f"   ⚠️  Sin dirección válida")
                
                if existing:
                    update_query = text("""
                        UPDATE lawsuit_court_assignments
                        SET lawsuit_id = :lawsuit_id,
                            client_identification = :identification,
                            client_address = :address,
                            client_city = :city,
                            court_id = NULL,
                            court_name = :message,
                            distance_km = NULL,
                            cuantia_type = NULL,
                            data_hash = :hash,
                            updated_at = NOW()
                        WHERE client_id = :client_id
                    """)
                    connection.execute(update_query, {
                        "lawsuit_id": lawsuit_id,
                        "client_id": client_id,
                        "identification": client_identification,
                        "address": "Sin dirección",
                        "city": client_city or "N/A",
                        "message": "Sin dirección",
                        "hash": current_hash
                    })
                    updated_count += 1
                else:
                    insert_query = text("""
                        INSERT INTO lawsuit_court_assignments 
                        (lawsuit_id, client_id, client_identification, client_address, 
                         client_city, court_name, distance_km, data_hash)
                        VALUES (:lawsuit_id, :client_id, :identification, :address, 
                                :city, :message, NULL, :hash)
                    """)
                    connection.execute(insert_query, {
                        "lawsuit_id": lawsuit_id,
                        "client_id": client_id,
                        "identification": client_identification,
                        "address": "Sin dirección",
                        "city": client_city or "N/A",
                        "message": "Sin dirección",
                        "hash": current_hash
                    })
                    inserted_count += 1
                
                connection.commit()
                no_address_count += 1
                continue
                
            #geocodificar direccion del cliente
            full_address = f"{client_address}, {client_neighborhood or ''}, {client_city}"
            print(f"   📍 Geocodificando: {full_address}")
            
            client_lat, client_lng, found_city = geocode_address(
                client_address,
                client_city,
                client_department,
                client_neighborhood
            )
            
            if not client_lat or not client_lng:
                print(f"   ❌ Error al geocodificar")
                
                if existing:
                    update_query = text("""
                        UPDATE lawsuit_court_assignments
                        SET lawsuit_id = :lawsuit_id,
                            client_identification = :identification,
                            client_address = :address,
                            client_city = :city,
                            court_id = NULL,
                            court_name = :message,
                            distance_km = NULL,
                            cuantia_type = NULL,
                            data_hash = :hash,
                            updated_at = NOW()
                        WHERE client_id = :client_id
                    """)
                    connection.execute(update_query, {
                        "lawsuit_id": lawsuit_id,
                        "client_id": client_id,
                        "identification": client_identification,
                        "address": full_address,
                        "city": client_city,
                        "message": "Error en geocodificación",
                        "hash": current_hash
                    })
                    updated_count += 1
                else:
                    insert_query = text("""
                        INSERT INTO lawsuit_court_assignments 
                        (lawsuit_id, client_id, client_identification, client_address, 
                         client_city, court_name, distance_km, data_hash)
                        VALUES (:lawsuit_id, :client_id, :identification, :address, 
                                :city, :message, NULL, :hash)
                    """)
                    connection.execute(insert_query, {
                        "lawsuit_id": lawsuit_id,
                        "client_id": client_id,
                        "identification": client_identification,
                        "address": full_address,
                        "city": client_city,
                        "message": "Error en geocodificación",
                        "hash": current_hash
                    })
                    inserted_count += 1
                
                connection.commit()
                error_count += 1
                continue
            
            print(f"   ✅ Coordenadas: ({client_lat}, {client_lng})")
            print(f"   🏙️  Ciudad encontrada: {found_city or 'N/A'}")
            
            #validar que la dirección esté en la ciudad correcta
            if found_city:
                normalized_found_city = normalize_city(found_city)
                normalized_client_city = normalize_city(client_city)
                
                if normalized_found_city != normalized_client_city:
                    print(f"   ⚠️  Dirección geocodificada en ciudad diferente")
                    print(f"       Esperada: {client_city}")
                    print(f"       Encontrada: {found_city}")
                    
                    if existing:
                        update_query = text("""
                            UPDATE lawsuit_court_assignments
                            SET lawsuit_id = :lawsuit_id,
                                client_identification = :identification,
                                client_address = :address,
                                client_city = :city,
                                court_id = NULL,
                                court_name = :message,
                                distance_km = NULL,
                                cuantia_type = NULL,
                                data_hash = :hash,
                                updated_at = NOW()
                            WHERE client_id = :client_id
                        """)
                        connection.execute(update_query, {
                            "lawsuit_id": lawsuit_id,
                            "client_id": client_id,
                            "identification": client_identification,
                            "address": f"Dirección en {found_city}, no en {client_city}",
                            "city": client_city,
                            "message": "Dirección incorrecta o en otra ciudad",
                            "hash": current_hash
                        })
                        updated_count += 1
                    else:
                        insert_query = text("""
                            INSERT INTO lawsuit_court_assignments 
                            (lawsuit_id, client_id, client_identification, client_address, 
                             client_city, court_name, distance_km, data_hash)
                            VALUES (:lawsuit_id, :client_id, :identification, :address, 
                                    :city, :message, NULL, :hash)
                        """)
                        connection.execute(insert_query, {
                            "lawsuit_id": lawsuit_id,
                            "client_id": client_id,
                            "identification": client_identification,
                            "address": f"Dirección en {found_city}, no en {client_city}",
                            "city": client_city,
                            "message": "Dirección incorrecta o en otra ciudad",
                            "hash": current_hash
                        })
                        inserted_count += 1
                    
                    connection.commit()
                    wrong_city_count += 1
                    continue
            
            #buscar juzgados en la ciudad del cliente y del tipo de cuantía
            normalized_city = normalize_city(client_city)
            
            courts_query = text("""
                SELECT 
                    dc.id,
                    dc.name,
                    dc.adress,
                    dc.city,
                    dc.type_cuantity,
                    cc.latitude,
                    cc.longitude
                FROM data_courts dc
                INNER JOIN court_coordinates cc ON dc.id = cc.court_id
                WHERE dc.status = 'Activo'
                AND dc.deleted_at IS NULL
                AND cc.deleted_at IS NULL
                AND dc.type_cuantity = :cuantia
                AND UPPER(TRIM(dc.city)) = :city
            """)
            
            result = connection.execute(courts_query, {
                "cuantia": type_quantity,
                "city": normalized_city
            })
            courts = result.fetchall()
            
            if not courts:
                print(f"   ⚠️  No hay juzgados de '{type_quantity}' en {client_city}")
                
                if existing:
                    update_query = text("""
                        UPDATE lawsuit_court_assignments
                        SET lawsuit_id = :lawsuit_id,
                            client_identification = :identification,
                            client_address = :address,
                            client_city = :city,
                            court_id = NULL,
                            court_name = :message,
                            distance_km = NULL,
                            cuantia_type = :cuantia,
                            data_hash = :hash,
                            updated_at = NOW()
                        WHERE client_id = :client_id
                    """)
                    connection.execute(update_query, {
                        "lawsuit_id": lawsuit_id,
                        "client_id": client_id,
                        "identification": client_identification,
                        "address": full_address,
                        "city": client_city,
                        "message": "No se encuentra juzgado en ciudad",
                        "cuantia": type_quantity,
                        "hash": current_hash
                    })
                    updated_count += 1
                else:
                    insert_query = text("""
                        INSERT INTO lawsuit_court_assignments 
                        (lawsuit_id, client_id, client_identification, client_address, 
                         client_city, court_name, distance_km, cuantia_type, data_hash)
                        VALUES (:lawsuit_id, :client_id, :identification, :address, 
                                :city, :message, NULL, :cuantia, :hash)
                    """)
                    connection.execute(insert_query, {
                        "lawsuit_id": lawsuit_id,
                        "client_id": client_id,
                        "identification": client_identification,
                        "address": full_address,
                        "city": client_city,
                        "message": "No se encuentra juzgado en ciudad",
                        "cuantia": type_quantity,
                        "hash": current_hash
                    })
                    inserted_count += 1
                
                connection.commit()
                no_court_in_city_count += 1
                continue
            
            print(f"   🏛️  Juzgados encontrados en {client_city}: {len(courts)}")
            
            #calcular distancias en línea recta y ordenar
            courts_with_distance = []
            
            for court in courts:
                court_id = court[0]
                court_name = court[1]
                court_lat = float(court[5])
                court_lng = float(court[6])
                
                distance = geodesic(
                    (client_lat, client_lng),
                    (court_lat, court_lng)
                ).kilometers
                
                courts_with_distance.append({
                    'court_id': court_id,
                    'court_name': court_name,
                    'lat': court_lat,
                    'lng': court_lng,
                    'straight_distance': distance
                })
            
            courts_with_distance.sort(key=lambda x: x['straight_distance'])
            top_courts = courts_with_distance[:5]
            
            #calcular distancias reales usando Matrix API
            destinations = [
                (c['lat'], c['lng'], c['court_id'], c['court_name'])
                for c in top_courts
            ]
            
            real_distances = get_distance_matrix(client_lat, client_lng, destinations)
            
            if not real_distances:
                print(f"   ⚠️  Error al calcular distancias reales, usando línea recta")
                closest_court = top_courts[0]
                final_distance = closest_court['straight_distance']
            else:
                closest_court_data = real_distances[0]
                closest_court = next(
                    c for c in top_courts
                    if c['court_id'] == closest_court_data['court_id']
                )
                final_distance = closest_court_data['distance_km']
            
            #guardar asignación dejuzgado
            print(f"   ✅ Juzgado asignado: {closest_court['court_name']}")
            print(f"   📏 Distancia: {final_distance:.2f} km")
            
            if existing:
                update_query = text("""
                    UPDATE lawsuit_court_assignments
                    SET lawsuit_id = :lawsuit_id,
                        client_identification = :identification,
                        client_address = :client_address,
                        client_city = :client_city,
                        court_id = :court_id,
                        court_name = :court_name,
                        distance_km = :distance,
                        cuantia_type = :cuantia,
                        data_hash = :hash,
                        updated_at = NOW()
                    WHERE client_id = :client_id
                """)
                connection.execute(update_query, {
                    "lawsuit_id": lawsuit_id,
                    "client_id": client_id,
                    "identification": client_identification,
                    "client_address": full_address,
                    "client_city": client_city,
                    "court_id": closest_court['court_id'],
                    "court_name": closest_court['court_name'],
                    "distance": final_distance,
                    "cuantia": type_quantity,
                    "hash": current_hash
                })
                updated_count += 1
            else:
                insert_query = text("""
                    INSERT INTO lawsuit_court_assignments 
                    (lawsuit_id, client_id, client_identification, client_address, 
                     client_city, court_id, court_name, distance_km, cuantia_type, data_hash)
                    VALUES (:lawsuit_id, :client_id, :identification, :client_address, 
                            :client_city, :court_id, :court_name, :distance, :cuantia, :hash)
                """)
                connection.execute(insert_query, {
                    "lawsuit_id": lawsuit_id,
                    "client_id": client_id,
                    "identification": client_identification,
                    "client_address": full_address,
                    "client_city": client_city,
                    "court_id": closest_court['court_id'],
                    "court_name": closest_court['court_name'],
                    "distance": final_distance,
                    "cuantia": type_quantity,
                    "hash": current_hash
                })
                inserted_count += 1
            
            connection.commit()
            success_count += 1
            time.sleep(0.1)
        
        #resumen final
        print("\n" + "=" * 70)
        print("📊 RESUMEN DE PROCESAMIENTO")
        print("=" * 70)
        print(f"✅ Juzgados asignados: {success_count}")
        print(f"🔄 Registros actualizados: {updated_count}")
        print(f"🆕 Registros nuevos: {inserted_count}")
        print(f"⏭️  Registros omitidos (sin cambios): {skipped_count}")
        print(f"⚠️  Sin dirección: {no_address_count}")
        print(f"⚠️  Dirección en ciudad incorrecta: {wrong_city_count}")
        print(f"⚠️  Sin juzgado en ciudad: {no_court_in_city_count}")
        print(f"❌ Errores: {error_count}")
        print(f"📋 Total procesados: {len(lawsuits)}")
        print(f"💰 Llamadas API ahorradas: ~{skipped_count * 2}")
        print("=" * 70)


if __name__ == "__main__":
    process_pending_lawsuits()