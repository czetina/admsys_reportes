"""
Menú de reportes leído de vfmenuc, filtrado por los permisos del sistema VFP, para cada módulo migrado.

Misma regla que el menú de VFP (_sys_mgr.prg, llenarmenu): el usuario ve una opción solo si tiene
la fila en vfaccess para esa empresa. vfaccessperfiles es la plantilla del perfil que VFP copia a
vfaccess al asignar el perfil (_carga_user.prg); no da acceso por sí sola, y la marca de supervisor
de vfusers tampoco abre opciones en el menú. Además se exige el permiso «imprime» (en VFP habilita
el botón de imprimir del formulario: en la web generar el reporte es imprimirlo).

Un mismo formulario VFP sirve a CXC y a CXP (p. ej. con_ctacte_integracion), por eso en la web
el comando lleva el prefijo del módulo: «cxc.con_ctacte_integracion». Contabilidad va sin prefijo.
"""
import time
from dataclasses import dataclass

from .db import consulta


@dataclass(frozen=True)
class Modulo:
    directorio: str
    codsys: str
    secciones: tuple       # codigo de los submenús de vfmenuc que se migran
    prefijo: str           # prefijo del comando en la web
    nombre: str


MODULOS = (
    Modulo("CONTA", "CNT", ("20", "22", "24", "26", "30", "35", "40"), "", "Contabilidad"),
    Modulo("CTAXC", "CXC", ("50", "54", "60"), "cxc.", "Cuentas por cobrar"),
    Modulo("CTAXP", "CXP", ("50", "54", "60"), "cxp.", "Cuentas por pagar"),
    Modulo("IVA", "IVA", ("50", "52", "55"), "iva.", "Auxiliares de IVA"),
    Modulo("FACTURA", "FAC", ("50", "55"), "fac.", "Facturación"),
    Modulo("CHEQUES", "CHQ", ("12", "20", "50", "54", "55", "60", "62", "85"), "chq.", "Caja y bancos"),
    Modulo("ACFIJOS", "ACF", ("50",), "acf.", "Activos fijos"),
)


def _permitidos(usuario, modulo):
    return {
        f["codigo"]
        for f in consulta(
            """
            SELECT codigo FROM vfaccess
             WHERE codempresa = %s AND coduser = %s AND directorio = %s AND codsys = %s AND imprime = 1
            """,
            (usuario["codempresa"], usuario["coduser"], modulo.directorio, modulo.codsys),
        )
    }


_CACHE_SEGUNDOS = 60
_cache = {}


def opciones_todas(usuario):
    """
    Opciones de vfmenuc de todos los módulos migrados a las que el usuario tiene acceso; cada fila
    trae `comando` ya con el prefijo del módulo y `modulo`. Se guardan un minuto por usuario: se
    piden en el menú y en cada reporte (permiso), y contra un servidor remoto cada consulta es una
    ida y vuelta.
    """
    clave = (usuario["codempresa"], usuario["coduser"])
    guardada = _cache.get(clave)
    if guardada and time.monotonic() - guardada[0] < _CACHE_SEGUNDOS:
        return guardada[1]
    salida = []
    for modulo in MODULOS:
        filtro = " OR ".join(["m.codigo LIKE %s"] * len(modulo.secciones))
        filas = consulta(
            f"""
            SELECT m.codigo, m.superior, m.descri, m.comando
              FROM vfmenuc m
             WHERE m.directorio = %s AND m.codsys = %s AND ({filtro})
             ORDER BY m.codigo
            """,
            (modulo.directorio, modulo.codsys, *[s + "%" for s in modulo.secciones]),
        )
        permitidos = _permitidos(usuario, modulo)
        filas = [f for f in filas if f["codigo"] in permitidos]
        for f in filas:
            f["modulo"] = modulo
            if f["comando"]:
                f["comando"] = modulo.prefijo + f["comando"]
        salida += filas
    _cache[clave] = (time.monotonic(), salida)
    return salida
