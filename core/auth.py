"""
Autenticación contra los usuarios del sistema VFP (tabla vfusers).

Replica login_empresa.scx: la clave se compara como SHA2(clave, gcBaseEncrypt)
y solo se listan empresas donde el usuario no tiene fecha de baja.
"""
from datetime import date

from django.conf import settings

from .db import consulta, consulta_uno, fecha_valida

CLAVE_SESION = "usuario_sistema"


def empresas_del_usuario(coduser, clave):
    filas = consulta(
        """
        SELECT e.codempresa, e.razonsocial, u.fecexp
          FROM vfusers u
          JOIN empresas e ON e.codempresa = u.codempresa
         WHERE u.coduser = %s
           AND u.codpassword = SHA2(%s, %s)
           AND (u.fecha_baja IS NULL OR u.fecha_baja = '0000-00-00')
           AND u.es_inactivo = 0
         ORDER BY e.codempresa
        """,
        (coduser, clave, settings.VADMON_BASE_ENCRYPT),
    )
    hoy = date.today()
    for f in filas:
        vence = fecha_valida(f.pop("fecexp"))
        f["clave_vencida"] = bool(vence and vence <= hoy)
    return filas


def datos_usuario(coduser, codempresa):
    return consulta_uno(
        """
        SELECT u.coduser, u.nombre, u.perfil, u.supervisor,
               COALESCE(p.es_super_admin, 0) AS es_super_admin
          FROM vfusers u
          LEFT JOIN vfperfiles p ON p.perfil = u.perfil
         WHERE u.coduser = %s AND u.codempresa = %s
        """,
        (coduser, codempresa),
    )


def iniciar_sesion(request, coduser, empresa):
    usr = datos_usuario(coduser, empresa["codempresa"])
    request.session.cycle_key()
    request.session[CLAVE_SESION] = {
        "coduser": usr["coduser"],
        "nombre": usr["nombre"],
        "perfil": usr["perfil"],
        "supervisor": bool(usr["supervisor"] or usr["es_super_admin"]),
        "codempresa": empresa["codempresa"],
        "razonsocial": empresa["razonsocial"],
    }


def usuario_actual(request):
    return request.session.get(CLAVE_SESION)
