"""Acceso de solo lectura a la base vadmon (MariaDB 10.4 / MySQL)."""
import queue
import time
from contextlib import contextmanager
from datetime import date

import pymysql
from django.conf import settings

# Pool de conexiones: abrir una conexión cuesta ~5 ms en local pero 1.2–1.6 s contra un
# servidor remoto (TCP + saludo MySQL), y un reporte hace de 3 a 8 consultas.
POOL_MAXIMO = 4
POOL_INACTIVA = 300          # segundos; una conexión más vieja se cierra en lugar de reutilizarse
POOL_VERIFICAR = 30          # segundos sin uso a partir de los cuales se hace ping antes de reusar
_pool = queue.LifoQueue(maxsize=POOL_MAXIMO)
_CAIDA = {2006, 2013, 2055}  # server has gone away / lost connection: se reintenta con otra conexión


def _nueva():
    cfg = settings.VADMON
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10,
        # Seguro adicional: la sesión queda en solo lectura y el servidor rechaza cualquier
        # escritura aunque el usuario tenga permisos (en producción `consulte` tiene ALL PRIVILEGES).
        init_command="SET SESSION TRANSACTION READ ONLY",
    )


def _tomar():
    while True:
        try:
            cnx, usada = _pool.get_nowait()
        except queue.Empty:
            return _nueva()
        inactiva = time.monotonic() - usada
        if inactiva > POOL_INACTIVA:
            _cerrar(cnx)
            continue
        if inactiva < POOL_VERIFICAR:      # cada ping es una ida y vuelta (~160 ms en remoto)
            return cnx
        try:
            cnx.ping(reconnect=False)
            return cnx
        except pymysql.MySQLError:
            _cerrar(cnx)


def _cerrar(cnx):
    try:
        cnx.close()
    except Exception:
        pass


@contextmanager
def conexion():
    cnx = _tomar()
    sana = True
    try:
        yield cnx
    except (pymysql.OperationalError, pymysql.InterfaceError):
        sana = False          # conexión caída: no regresa al pool
        raise
    finally:
        if sana:
            try:
                _pool.put_nowait((cnx, time.monotonic()))
            except queue.Full:
                _cerrar(cnx)
        else:
            _cerrar(cnx)


def _limpia(fila):
    # Los códigos vienen rellenos con espacios (CHAR heredado de FoxPro)
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in fila.items()}


def consulta(sql, params=None, cnx=None):
    if cnx is None:
        try:
            with conexion() as c:
                return consulta(sql, params, c)
        except pymysql.OperationalError as e:
            if e.args and e.args[0] in _CAIDA:     # conexión del pool cerrada por el servidor
                with conexion() as c:
                    return consulta(sql, params, c)
            raise
    with cnx.cursor() as cur:
        cur.execute(sql, params or ())
        return [_limpia(f) for f in cur.fetchall()]


def consulta_uno(sql, params=None, cnx=None):
    filas = consulta(sql, params, cnx)
    return filas[0] if filas else None


def fecha_valida(valor):
    """PyMySQL devuelve '0000-00-00' como texto; eso equivale a fecha vacía."""
    return valor if isinstance(valor, date) else None
