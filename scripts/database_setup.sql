
USE miosv2_carteras_QA;

-- ============================================================
-- TABLA 1: court_coordinates
-- Almacena las coordenadas geocodificadas de los juzgados
-- ============================================================

CREATE TABLE IF NOT EXISTS court_coordinates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    court_id INT NOT NULL UNIQUE COMMENT 'ID del juzgado en data_courts',
    latitude DECIMAL(10, 8) NOT NULL COMMENT 'Latitud geocodificada',
    longitude DECIMAL(11, 8) NOT NULL COMMENT 'Longitud geocodificada',
    geocoded_address TEXT COMMENT 'Dirección completa usada para geocodificar',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL DEFAULT NULL COMMENT 'Sincronizado con data_courts.deleted_at',
    
    INDEX idx_court_id (court_id),
    INDEX idx_deleted_at (deleted_at),
    INDEX idx_coordinates (latitude, longitude)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Coordenadas geocodificadas de juzgados para cálculo de distancias';


-- ============================================================
-- TABLA 2: lawsuit_court_assignments
-- Almacena las asignaciones de juzgados a demandas
-- ============================================================

CREATE TABLE IF NOT EXISTS lawsuit_court_assignments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    lawsuit_id INT NOT NULL COMMENT 'ID de la demanda en lawsuits',
    client_identification VARCHAR(50) COMMENT 'Cédula del cliente',
    client_address TEXT COMMENT 'Dirección del cliente (o "Sin dirección" si no tiene)',
    client_city VARCHAR(100) COMMENT 'Ciudad del cliente',
    court_id INT NULL COMMENT 'ID del juzgado asignado (NULL si no tiene dirección)',
    court_name VARCHAR(255) COMMENT 'Nombre del juzgado asignado',
    cuantia_type VARCHAR(50) COMMENT 'Tipo de cuantía del juzgado',
    distance_km DECIMAL(10, 2) COMMENT 'Distancia en km entre cliente y juzgado',
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT 'Fecha de asignación inicial',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Última actualización',
    
    UNIQUE KEY unique_lawsuit (lawsuit_id),
    INDEX idx_court_id (court_id),
    INDEX idx_assigned_at (assigned_at),
    INDEX idx_client_address (client_address(100)) COMMENT 'Para búsqueda de "Sin dirección"'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Asignaciones de juzgados a demandas basadas en distancia geográfica';
