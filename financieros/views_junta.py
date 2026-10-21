"""Paquete de la junta directiva desde la web: los reportes de financieros/junta.py en un .zip."""
import time

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils.http import content_disposition_header

from core import menu

from . import junta
from .forms import JuntaForm
from .views import _es_fetch


def reportes_permitidos(usuario):
    """Los reportes del paquete que el usuario tiene en su menú (vfaccess)."""
    permitidos = {op["comando"] for op in menu.opciones_todas(usuario)}
    return [(c, n) for c, n in junta.REPORTES_JUNTA if c in permitidos]


def paquete(request):
    incluidos = reportes_permitidos(request.usuario)
    if not incluidos:
        if _es_fetch(request):
            return JsonResponse({"error": "No tiene acceso a los reportes de la junta."}, status=403)
        return HttpResponseForbidden("No tiene acceso a los reportes de la junta.")
    con_acceso = {c for c, _ in incluidos}
    contexto = {"reportes": [{"nombre": n, "comando": c, "acceso": c in con_acceso} for c, n in junta.REPORTES_JUNTA]}

    if request.method != "POST":
        return render(request, "financieros/junta.html", {**contexto, "form": JuntaForm()})
    form = JuntaForm(request.POST)
    if not form.is_valid():
        error = next((e[0] for e in form.errors.values() if e), "Revise los parámetros.")
        if _es_fetch(request):
            return JsonResponse({"error": error}, status=400)
        return render(request, "financieros/junta.html", {**contexto, "form": form, "error": error})

    anio, mes = form.cleaned_data["anio"], form.cleaned_data["mes"]
    codempresa = request.usuario["codempresa"]
    inicio = time.perf_counter()
    resultados = junta.genera(codempresa, request.usuario["coduser"], anio, mes, incluidos)
    if not any(r.contenido for r in resultados):
        error = "No se generó ningún reporte: " + "; ".join(f"{r.nombre}: {r.error}" for r in resultados)
        return JsonResponse({"error": error}, status=400)
    resp = HttpResponse(junta.comprime(resultados), content_type="application/zip")
    resp["Content-Disposition"] = content_disposition_header(True, f"Junta {codempresa} {anio}-{mes:02d}.zip")
    resp["X-Tiempo"] = f"{time.perf_counter() - inicio:.2f}"
    resp["X-No-Generados"] = str(sum(1 for r in resultados if not r.contenido))
    return resp
