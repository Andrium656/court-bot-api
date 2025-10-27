import os
import time
import hashlib
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from typing import Optional

load_dotenv()

#importar configuración de BD y funciones de API
from app.utils.database import DB_NAMES, get_database_url
from app.utils.google_api import geocode_address_with_logging


def calculate_court_hash(court_id, address, city):
    """Calcula un hash para los datos del juzgado"""
    data_string = f"{court_id}|{address}|{city}"
    return hashlib.sha256(data_string.encode('utf-8')).hexdigest()


def sync_court_coordinates_single_db(db_name: str):
    """
    Sincroniza coordenadas de juzgados para UNA base de datos específica
    
    Args:
        db_name: Nombre de la base de datos
    """
    from app.bot_control import BotController
    print(f"🔄 SINCRONIZANDO JUZGADOS - BD: {db_name}")
    BotController.log(f"🔄 Iniciando sincronización de juzgados - BD: {db_name}", "INFO")
    database_url = get_database_url(db_name)
    engine = create_engine(database_url, echo=False)
    with engine.connect() as connection:
        
        print("\n1️⃣ Sincronizando deleted_at...")
        
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
            BotController.log(f"✅ [{db_name}] Juzgados marcados como inactivos: {marked_inactive}", "INFO")
            print(f"   ✅ Juzgados marcados como inactivos: {marked_inactive}")
        if marked_active > 0:
            BotController.log(f"✅ [{db_name}] Juzgados rehabilitados: {marked_active}", "INFO")
            print(f"   ✅ Juzgados rehabilitados: {marked_active}")
        if marked_inactive == 0 and marked_active == 0:
            print(f"   ✅ Todos los deleted_at están sincronizados")
        
        #quitar coordenadas huerfanas
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
            BotController.log(f"✅ [{db_name}] Eliminadas {deleted_count} coordenadas huérfanas", "INFO")
            print(f"   ✅ Eliminadas {deleted_count} coordenadas huérfanas")
        else:
            print(f"   ✅ No hay coordenadas huérfanas")
        
        #revisar si hay direcciones actualizadas
        print("\n3️⃣ Detectando juzgados con dirección actualizada...")
        
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
            
            current_hash = calculate_court_hash(court_id, current_address, current_city)
            
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
            BotController.log(f"⚠️ [{db_name}] {updated_count} juzgados con dirección actualizada", "WARNING")
            print(f"   ⚠️  {updated_count} juzgados con dirección actualizada")
            
            for court in updated_courts:
                court_id = court['id']
                court_name = court['name']
                new_address = court['address']
                new_city = court['city']
                new_hash = court['new_hash']
                
                print(f"\n   📝 {court_name}")
                print(f"      Nueva dirección: {new_address}, {new_city}")
                
                #funcion con logging
                lat, lng, _ = geocode_address_with_logging(
                    new_address, 
                    new_city,
                    db_name=db_name
                )
                
                if lat and lng:
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
        success_count = 0
        error_count = 0
        
        if new_count > 0:
            BotController.log(f"🆕 [{db_name}] {new_count} juzgados nuevos por geocodificar", "INFO")
            print(f"   🆕 {new_count} juzgados nuevos por geocodificar\n")
            
            for i, court in enumerate(new_courts, 1):
                court_id = court[0]
                court_name = court[1]
                court_address = court[2]
                court_city = court[3]
                court_cuantia = court[4]
                
                print(f"   [{i}/{new_count}] {court_name} ({court_cuantia})")
                print(f"      📍 {court_address}, {court_city}")
                
                court_hash = calculate_court_hash(court_id, court_address, court_city)

                #funcion con logging
                lat, lng, _ = geocode_address_with_logging(
                    court_address,
                    court_city,
                    db_name=db_name
                )
                
                if lat and lng:
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
            
            BotController.log(f"✅ [{db_name}] Nuevos geocodificados: {success_count}", "INFO")
            print(f"   ✅ Nuevos geocodificados: {success_count}")
            if error_count > 0:
                BotController.log(f"❌ [{db_name}] Errores: {error_count}", "ERROR")
                print(f"   ❌ Errores: {error_count}")
        else:
            print(f"   ✅ No hay juzgados nuevos")
        
        # Resumen final
        print("\n" + "=" * 70)
        print(f"📊 RESUMEN - BD: {db_name}")
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
            BotController.log(f"✅ [{db_name}] Todos los juzgados activos están geocodificados", "INFO")
            print("\n✅ Todos los juzgados activos están geocodificados")
        else:
            missing = total_courts - total_active
            BotController.log(f"⚠️ [{db_name}] {missing} juzgados sin geocodificar", "WARNING")
            print(f"\n⚠️  {missing} juzgados sin geocodificar (probablemente con errores)")
        
        print("=" * 70)


def sync_court_coordinates():
    """
    Sincroniza coordenadas de juzgados en TODAS las bases de datos configuradas
    """
    from app.bot_control import BotController
    
    print("\n" + "🌟" * 35)
    print("🚀 INICIANDO SINCRONIZACIÓN MULTI-BASE DE DATOS")
    print("🌟" * 35)
    
    BotController.log(f"🚀 Iniciando sincronización en {len(DB_NAMES)} bases de datos", "INFO")
    
    results = {}
    
    for i, db_name in enumerate(DB_NAMES, 1):
        print(f"\n{'='*70}")
        print(f"📦 BASE DE DATOS {i}/{len(DB_NAMES)}: {db_name}")
        print(f"{'='*70}")
        
        try:
            sync_court_coordinates_single_db(db_name)
            results[db_name] = "✅ SUCCESS"
        except Exception as e:
            error_msg = str(e)
            results[db_name] = f"❌ ERROR: {error_msg}"
            BotController.log(f"❌ Error en BD {db_name}: {error_msg}", "ERROR")
            print(f"\n❌ ERROR: {error_msg}")
    
    # Resumen de todo
    print("\n" + "🌟" * 35)
    print("📊 RESUMEN GLOBAL DE SINCRONIZACIÓN")
    print("🌟" * 35)
    
    for db_name, status in results.items():
        print(f"{status} - {db_name}")
    
    success_count = sum(1 for s in results.values() if "SUCCESS" in s)
    BotController.log(
        f"🏁 Sincronización completada: {success_count}/{len(DB_NAMES)} exitosas",
        "INFO"
    )
    
    print("🌟" * 35)


if __name__ == "__main__":
    sync_court_coordinates()