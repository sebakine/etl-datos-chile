-- Una corrida de indicadores puede cargar algunos y fallar en otros
-- (mindicador.cl a veces no responde). Ese caso queda como 'parcial'.
ALTER TABLE etl.ejecuciones DROP CONSTRAINT ejecuciones_estado_check;
ALTER TABLE etl.ejecuciones
    ADD CONSTRAINT ejecuciones_estado_check CHECK (estado IN ('en_curso', 'ok', 'parcial', 'error'));
