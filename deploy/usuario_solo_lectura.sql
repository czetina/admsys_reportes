-- ---------------------------------------------------------------------------------------------
-- Usuario de MariaDB / MySQL para la web de reportes ADMSys: SOLO LECTURA.
--
-- La web nunca escribe en la base (todas las consultas son SELECT). Con este usuario, aunque
-- alguien robara la contraseña, no podría modificar ni borrar datos.
--
-- Lo ejecuta el DBA con un usuario administrador. Antes de correrlo, reemplazar:
--   IP_DEL_SERVIDOR_WEB  -> IP del servidor donde corre Django (no usar '%')
--   CLAVE_LARGA_ALEATORIA -> contraseña nueva de 32+ caracteres, que solo se guarda en el
--                            servidor (variable VADMON_PASSWORD), nunca en el código ni en correos
-- ---------------------------------------------------------------------------------------------

CREATE USER 'admsys_web'@'IP_DEL_SERVIDOR_WEB' IDENTIFIED BY 'CLAVE_LARGA_ALEATORIA';

-- Lectura de la base de producción, nada más (sin INSERT, UPDATE, DELETE, CREATE, DROP, FILE...)
GRANT SELECT ON `NOMBRE_BASE_PRODUCCION`.* TO 'admsys_web'@'IP_DEL_SERVIDOR_WEB';

-- Límite de conexiones simultáneas: un error o un ataque no puede agotar las del servidor.
-- La web usa un pool pequeño (core/db.py); 20 deja margen.
ALTER USER 'admsys_web'@'IP_DEL_SERVIDOR_WEB' WITH MAX_USER_CONNECTIONS 20;

-- Si la conexión entre la web y la base pasa por una red que no es local, exigir cifrado:
-- ALTER USER 'admsys_web'@'IP_DEL_SERVIDOR_WEB' REQUIRE SSL;

FLUSH PRIVILEGES;

-- Verificación: debe mostrar solo «GRANT USAGE» y «GRANT SELECT ON `NOMBRE_BASE_PRODUCCION`.*»
SHOW GRANTS FOR 'admsys_web'@'IP_DEL_SERVIDOR_WEB';

-- Prueba (conectado como admsys_web): esto debe fallar con «command denied»
-- UPDATE NOMBRE_BASE_PRODUCCION.empresas SET razonsocial = razonsocial WHERE 1 = 0;
