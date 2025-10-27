import requests
import time
from typing import Tuple, Optional, List, Dict, Any

from app.utils.database import GOOGLE_API_KEY


def geocode_address_with_logging(
    address: str, 
    city: str, 
    department: Optional[str] = None, 
    neighborhood: Optional[str] = None,
    db_name: str = "unknown"
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Geocodifica una dirección usando Google Maps API con logging
    
    Args:
        address: Dirección a geocodificar
        city: Ciudad
        department: Departamento (opcional)
        neighborhood: Barrio (opcional)
        db_name: Nombre de la base de datos (para logging)
    
    Returns:
        tuple: (lat, lng, found_city) o (None, None, None) si falla
    """
    from app.bot_control import BotController
    
    #construir dirección completa
    address_parts = [address]
    if neighborhood:
        address_parts.append(neighborhood)
    address_parts.append(city)
    if department:
        address_parts.append(department)
    address_parts.append("Colombia")
    
    full_address = ", ".join(address_parts)

    BotController.log(
        f"🌍 [API CALL] Geocoding API - DB: {db_name} - Dirección: {full_address[:80]}...",
        "INFO"
    )
    
    #contador -antes de la llamada a la api
    BotController.increment_api_calls()
    
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        'address': full_address,
        'key': GOOGLE_API_KEY
    }
    
    start_time = time.time()
    
    try:
        response = requests.get(url, params=params, timeout=10)
        elapsed_time = time.time() - start_time
        
        data = response.json()
        
        if data['status'] == 'OK':
            result = data['results'][0]
            location = result['geometry']['location']
            
            # Extraer ciudad
            found_city = None
            for component in result['address_components']:
                if 'locality' in component['types']:
                    found_city = component['long_name']
                    break
                elif 'administrative_area_level_2' in component['types']:
                    found_city = component['long_name']
                    break
            
            #log de exito
            BotController.log(
                f"✅ [API SUCCESS] Geocoding - DB: {db_name} - "
                f"Coords: ({location['lat']}, {location['lng']}) - "
                f"Ciudad: {found_city or 'N/A'} - "
                f"Tiempo: {elapsed_time:.2f}s",
                "INFO"
            )
            
            return location['lat'], location['lng'], found_city
        else:
            #log de error
            BotController.log(
                f"⚠️ [API ERROR] Geocoding - DB: {db_name} - "
                f"Status: {data['status']} - "
                f"Tiempo: {elapsed_time:.2f}s",
                "WARNING"
            )
            return None, None, None
            
    except requests.Timeout:
        BotController.log(
            f"❌ [API TIMEOUT] Geocoding - DB: {db_name} - Timeout después de 10s",
            "ERROR"
        )
        return None, None, None
    except Exception as e:
        BotController.log(
            f"❌ [API EXCEPTION] Geocoding - DB: {db_name} - Error: {str(e)}",
            "ERROR"
        )
        return None, None, None


def get_distance_matrix_with_logging(
    origin_lat: float,
    origin_lng: float,
    destinations: List[Tuple[float, float, int, str]],
    db_name: str = "unknown"
) -> List[Dict[str, Any]]:
    """
    Calcula distancias usando Google Distance Matrix API con logging detallado
    
    Args:
        origin_lat: Latitud origen
        origin_lng: Longitud origen
        destinations: Lista de (lat, lng, court_id, court_name)
        db_name: Nombre de la base de datos (para logging)
    
    Returns:
        list: Lista de diccionarios con información de distancia
    """
    from app.bot_control import BotController
    
    if not destinations:
        return []
    BotController.log(
        f"🚗 [API CALL] Distance Matrix API - DB: {db_name} - "
        f"Origen: ({origin_lat}, {origin_lng}) - "
        f"Destinos: {len(destinations)}",
        "INFO"
    )
    
    #contador antes de la llamada a la api matrix
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
    
    start_time = time.time()
    
    try:
        response = requests.get(url, params=params, timeout=15)
        elapsed_time = time.time() - start_time
        
        data = response.json()
        
        if data['status'] == 'OK':
            results = []
            elements = data['rows'][0]['elements']
            
            success_count = 0
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
                    success_count += 1

            #log de exito
            BotController.log(
                f"✅ [API SUCCESS] Distance Matrix - DB: {db_name} - "
                f"Rutas calculadas: {success_count}/{len(destinations)} - "
                f"Tiempo: {elapsed_time:.2f}s",
                "INFO"
            )
            
            return sorted(results, key=lambda x: x['distance_km'])
        else:
            BotController.log(
                f"⚠️ [API ERROR] Distance Matrix - DB: {db_name} - "
                f"Status: {data['status']} - "
                f"Tiempo: {elapsed_time:.2f}s",
                "WARNING"
            )
            return []
        
    except requests.Timeout:
        BotController.log(
            f"❌ [API TIMEOUT] Distance Matrix - DB: {db_name} - Timeout después de 15s",
            "ERROR"
        )
        return []
    except Exception as e:
        BotController.log(
            f"❌ [API EXCEPTION] Distance Matrix - DB: {db_name} - Error: {str(e)}",
            "ERROR"
        )
        return []