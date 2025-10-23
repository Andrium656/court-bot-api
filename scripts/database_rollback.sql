USE miosv2_carteras_QA;


DROP TABLE IF EXISTS lawsuit_court_assignments;
DROP TABLE IF EXISTS court_coordinates;

-- ============================================================
-- VERIFICACIÓN
-- ============================================================

-- Verificar que se eliminaron
SELECT 
    TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
AND TABLE_NAME IN ('court_coordinates', 'lawsuit_court_assignments');

SELECT 'Rollback completado exitosamente' as resultado;