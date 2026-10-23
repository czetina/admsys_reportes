"""Tablero CFO: la página, sus datos (JSON) y la descarga a Excel."""
import time

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils.http import content_disposition_header

from core import menu

from . import tablero_cfo, tablero_cfo_excel
from .catalogo import TABLERO_REQUIERE
from .contexto import ErrorReporte
from .forms import MESES
from .views import XLSX


def tiene_acceso(usuario):
    permitidos = {op["comando"] for op in menu.opciones_todas(usuario)}
    return all(c in permitidos for c in TABLERO_REQUIERE)


def _cierre(request):
    anio, mes = tablero_cfo.cierre_por_defecto()
    try:
        anio = int(request.GET.get("anio", anio))
        mes = int(request.GET.get("mes", mes))
    except ValueError:
        pass
    return anio, max(1, min(mes, 12))


def pagina(request):
    if not tiene_acceso(request.usuario):
        return HttpResponseForbidden("No tiene acceso al tablero: requiere el balance general y el estado de resultados.")
    anio, mes = _cierre(request)
    return render(request, "financieros/tablero_cfo.html", {"anio": anio, "mes": mes, "meses": MESES})


def datos(request):
    if not tiene_acceso(request.usuario):
        return JsonResponse({"error": "No tiene acceso al tablero."}, status=403)
    anio, mes = _cierre(request)
    inicio = time.perf_counter()
    try:
        tablero = tablero_cfo.calcula(request.usuario["codempresa"], anio, mes)
    except ErrorReporte as e:
        return JsonResponse({"error": str(e)}, status=400)
    resp = JsonResponse(tablero.json())
    resp["X-Tiempo"] = f"{time.perf_counter() - inicio:.2f}"
    return resp


def excel(request):
    if not tiene_acceso(request.usuario):
        return JsonResponse({"error": "No tiene acceso al tablero."}, status=403)
    anio, mes = _cierre(request)
    try:
        tablero = tablero_cfo.calcula(request.usuario["codempresa"], anio, mes)
    except ErrorReporte as e:
        return JsonResponse({"error": str(e)}, status=400)
    resp = HttpResponse(tablero_cfo_excel.genera(tablero), content_type=XLSX)
    resp["Content-Disposition"] = content_disposition_header(
        True, f"Tablero CFO {tablero.empresa.codempresa} {anio}-{mes:02d}.xlsx")
    return resp
