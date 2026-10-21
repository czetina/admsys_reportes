from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from financieros import catalogo
from financieros.registro import REPORTES

from . import menu
from .auth import empresas_del_usuario, iniciar_sesion

PENDIENTE = "login_pendiente"
RECIENTES = "recientes"      # últimos reportes generados: [{comando, nombre, variante, anio, mes}]


def login(request):
    """Paso 1: usuario y clave. Paso 2 (si hay varias empresas): elegir empresa."""
    pendiente = request.session.get(PENDIENTE)

    if request.method == "POST" and "empresa" in request.POST and pendiente:
        empresa = next((e for e in pendiente["empresas"] if e["codempresa"] == request.POST["empresa"]), None)
        if empresa is None:
            messages.error(request, "Empresa no válida.")
        elif empresa["clave_vencida"]:
            messages.error(request, "Su contraseña ya expiró. Cámbiela en el sistema para poder ingresar.")
        else:
            coduser = pendiente["coduser"]
            del request.session[PENDIENTE]
            iniciar_sesion(request, coduser, empresa)
            return redirect("inicio")

    elif request.method == "POST":
        coduser = request.POST.get("usuario", "").strip().upper()
        clave = request.POST.get("clave", "")
        empresas = empresas_del_usuario(coduser, clave) if coduser and clave else []
        if not empresas:
            messages.error(request, "Credenciales incorrectas.")
            request.session.pop(PENDIENTE, None)
        else:
            request.session[PENDIENTE] = {"coduser": coduser, "empresas": empresas}
        return redirect("login")

    return render(request, "core/login.html", {"pendiente": pendiente})


@require_POST
def logout(request):
    request.session.flush()
    return redirect("login")


def cambiar_empresa(request):
    request.session.flush()
    return redirect("login")


def inicio(request):
    """Menú por categorías contables (financieros/catalogo.py) con los permisos de vfmenuc / vfaccess."""
    categorias = catalogo.menu_usuario(menu.opciones_todas(request.usuario), REPORTES)
    permitidos = {v["comando"] for cat in categorias for rep in cat["reportes"] for v in rep["variantes"]}
    recientes = [r for r in request.session.get(RECIENTES, []) if r["comando"] in permitidos]
    junta = any(c in permitidos for c, _ in catalogo.REPORTES_JUNTA)
    return render(request, "core/inicio.html", {"categorias": categorias, "recientes": recientes, "junta": junta,
                                                "total": sum(len(c["reportes"]) for c in categorias),
                                                "modulos": len({c["modulo"] for c in categorias})})
