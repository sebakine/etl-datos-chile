-- Si la API externa no responde durante toda la corrida (caída, 5xx, timeouts),
-- la ejecución queda como 'no_disponible': no es un error del pipeline y la
-- próxima corrida recupera los datos porque la marca de agua no avanzó.
ALTER TABLE etl.ejecuciones DROP CONSTRAINT ejecuciones_estado_check;
ALTER TABLE etl.ejecuciones
    ADD CONSTRAINT ejecuciones_estado_check
    CHECK (estado IN ('en_curso', 'ok', 'parcial', 'no_disponible', 'error'));
