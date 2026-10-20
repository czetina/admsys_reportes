"""
Ayudas de búsqueda de los filtros (equivalen a los «Maestro de Cuentas» / «Maestro de Directorio»
que VFP abre con F2 en los campos de cuenta y de directorio).

GET /financieros/buscar/cuentas/?q=caja&anio=2026&mes=10   -> nomenclatura del periodo
GET /financieros/buscar/directorios/?q=aero                  -> directorio del grupo de la empresa
?codigo=X devuelve solo ese código (para validar lo que se escribió a mano y mostrar su nombre).
"""
from datetime import date

from django.http import Http404, JsonResponse

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import ErrorReporte

LIMITE = 300


def _entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _palabras(q, campos):
    """Cada palabra debe aparecer en alguno de los campos («banco ind» encuentra «BANCO INDUSTRIAL»)."""
    partes, params = [], []
    for palabra in q.split()[:6]:
        partes.append("(" + " OR ".join(f"{c} LIKE %s" for c in campos) + ")")
        params += [f"%{palabra}%"] * len(campos)
    return "AND " + " AND ".join(partes), params


def _codperiodo(codempresa, request):
    """Periodo contable del año / mes (o fecha) del formulario; si no hay, el más reciente."""
    anio, mes = _entero(request.GET.get("anio")), _entero(request.GET.get("mes"))
    fecha = request.GET.get("fecha") or ""
    if (not anio or not mes) and len(fecha) >= 7:
        anio, mes = _entero(fecha[:4]), _entero(fecha[5:7])
    if not anio or not mes:
        hoy = date.today()
        anio, mes = hoy.year, hoy.month
    try:
        return ctx.periodo(codempresa, anio, mes).codperiodo
    except (ErrorReporte, ValueError):
        f = consulta_uno("SELECT MAX(codperiodo) AS codperiodo FROM empnomenclatura WHERE codempresa = %s",
                         (codempresa,))
        return (f or {}).get("codperiodo")


def _cuentas(codempresa, request, q, codigo):
    codperiodo = _codperiodo(codempresa, request)
    if codigo:
        filtro, params = "AND codcuenta = %s", [codigo]
    elif q:
        filtro, params = _palabras(q, ("codcuenta", "nomcta"))
    else:
        filtro, params = "", []
    filas = consulta(
        f"""
        SELECT codcuenta, nomcta, ultimonivelcta, naturalezacta, codsistema
          FROM empnomenclatura
         WHERE codempresa = %s AND codperiodo = %s {filtro}
         ORDER BY codcuenta
         LIMIT {LIMITE + 1}
        """, (codempresa, codperiodo, *params))
    return {
        "titulo": "Maestro de cuentas", "periodo": codperiodo,
        "columnas": ["Código", "Nombre", "Nivel", "Naturaleza", "Auxiliar"],
        "filas": [{"codigo": f["codcuenta"].strip(), "nombre": (f["nomcta"] or "").strip(),
                   "extra": ["Detalle" if f["ultimonivelcta"] == 1 else "Mayor",
                             {"D": "Deudora", "A": "Acreedora"}.get((f["naturalezacta"] or "").strip()[:1].upper(),
                                                                     (f["naturalezacta"] or "").strip()),
                             (f["codsistema"] or "").strip()]} for f in filas[:LIMITE]],
        "mas": len(filas) > LIMITE,
    }


def _directorios(codempresa, q, codigo):
    grupo = consulta_uno("SELECT codgrupo FROM empresas WHERE codempresa = %s", (codempresa,)) or {}
    if codigo:
        filtro, params = "AND coddirectorio = %s", [codigo]
    elif q:
        filtro, params = _palabras(q, ("coddirectorio", "razonsocial", "nit"))
    else:
        filtro, params = "", []
    filas = consulta(
        f"""
        SELECT coddirectorio, razonsocial, nit, es_inactivo
          FROM grudirectorio
         WHERE codgrupo = %s {filtro}
         ORDER BY razonsocial, coddirectorio
         LIMIT {LIMITE + 1}
        """, (grupo.get("codgrupo", ""), *params))
    return {
        "titulo": "Maestro de directorio",
        "columnas": ["Código", "Razón social", "NIT"],
        "filas": [{"codigo": (f["coddirectorio"] or "").strip(), "nombre": (f["razonsocial"] or "").strip(),
                   "extra": [(f["nit"] or "").strip() + (" (inactivo)" if f["es_inactivo"] == 1 else "")]}
                  for f in filas[:LIMITE]],
        "mas": len(filas) > LIMITE,
    }


def buscar(request, tipo):
    codempresa = request.usuario["codempresa"]
    q = (request.GET.get("q") or "").strip()[:60]
    codigo = (request.GET.get("codigo") or "").strip()[:30]
    if tipo == "cuentas":
        return JsonResponse(_cuentas(codempresa, request, q, codigo))
    if tipo == "directorios":
        return JsonResponse(_directorios(codempresa, q, codigo))
    raise Http404
