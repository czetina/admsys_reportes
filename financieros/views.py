import logging
import time
from datetime import date

from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils.http import content_disposition_header

from core import menu
from core.views import RECIENTES

from . import catalogo
from . import contexto as ctx
from .contexto import ErrorReporte
from .excel import genera_excel
from .forms import ParametrosForm
from .modelo import ArchivoTexto, Libro
from .registro import REPORTES
from .reportes import Parametros

log = logging.getLogger(__name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CAMPOS_EXTRA = ("centro", "centros", "cc_desde", "cc_hasta", "cuenta_desde", "cuenta_hasta", "meses",
                "anio_ini", "mes_ini", "directorio_desde", "directorio_hasta", "orden", "presentacion", "folio",
                "fecha_ini", "fecha_fin", "cuenta_aux", "no_contrato", "codmoneda", "codaca", "clasificaciones",
                "tipo_saldos", "fecha_base", "orden_cc", "intervalo", "columnas_antiguedad", "codtipo", "docref",
                "estado_doc", "sucursal", "codsistema_ref", "codctachq", "tipos_ref", "tipo_desde", "tipo_hasta",
                "estado_fac", "orden_fac", "codcc", "codci", "codca", "codtipocta", "estado_chq", "usuario_chq",
                "numero_desde", "numero_hasta", "tasa_cambio", "meses_caducado", "serie", "serie_desde", "serie_hasta",
                "estado_reg", "valor_doc", "proyectar_por", "primer_dia", "mostrar_por", "resolucion",
                "activo_desde", "activo_hasta", "ubica_desde", "ubica_hasta", "tipoact_desde", "tipoact_hasta",
                "depto_desde", "depto_hasta", "responsable_desde", "responsable_hasta", "orden_activos",
                "codresponsable")


def _rotula(rep, dim):
    """Reportes por centro de integración / actividad: los títulos dicen C.I. / C.A. en vez de C.C."""
    if dim == "cc" or isinstance(rep, ArchivoTexto):
        return
    for h in (rep.hojas if isinstance(rep, Libro) else [rep]):
        h.titulo, h.subtitulo, h.hoja = ctx.rotula(h.titulo, dim), ctx.rotula(h.subtitulo, dim), ctx.rotula(h.hoja, dim)
        h.encabezado_a = (ctx.rotula(h.encabezado_a[0], dim), h.encabezado_a[1])
        for f in h.filas:
            f.nombre, f.cuenta = ctx.rotula(f.nombre, dim), ctx.rotula(f.cuenta, dim)
    rep.titulo = ctx.rotula(rep.titulo, dim)
    rep.nombre_archivo = ctx.rotula(rep.nombre_archivo, dim)


def _registra_reciente(request, comando, contexto, p):
    """Guarda los últimos 3 reportes generados (sin repetir comando) para el menú principal."""
    nuevo = {"comando": comando, "nombre": contexto["titulo"], "variante": contexto["variante"],
             "anio": p.anio, "mes": p.mes}
    previos = [r for r in request.session.get(RECIENTES, []) if r["comando"] != comando]
    request.session[RECIENTES] = [nuevo] + previos[:2]


def _es_fetch(request):
    return request.headers.get("X-Requested-With") == "fetch"


def reporte(request, comando):
    definicion = REPORTES.get(comando)
    if definicion is None:
        raise Http404("Reporte no migrado")
    opciones = menu.opciones_todas(request.usuario)
    opcion = next((op for op in opciones if op["comando"] == comando), None)
    if opcion is None:
        if _es_fetch(request):
            return JsonResponse({"error": "No tiene acceso a este reporte."}, status=403)
        return HttpResponseForbidden("No tiene acceso a este reporte.")
    permitidos = {op["comando"] for op in opciones if op["comando"] in REPORTES}
    rep_cat, variantes = catalogo.hermanas(comando, permitidos)
    lugar = catalogo.ubicar(comando)
    contexto = {
        "titulo": rep_cat.nombre if rep_cat else opcion["descri"], "comando": comando, "definicion": definicion,
        "descripcion": rep_cat.descripcion if rep_cat else "",
        "categoria": lugar[0] if lugar else None,
        "variantes": variantes if len(variantes) > 1 else [],
        "variante": lugar[2] if lugar and len(variantes) > 1 else "",
    }

    codempresa = request.usuario["codempresa"]
    if request.method != "POST":
        inicial = {}
        for campo in ("anio", "mes"):          # desde «Recientes» del menú
            if request.GET.get(campo, "").isdigit():
                inicial[campo] = int(request.GET[campo])
        form = ParametrosForm(definicion=definicion, codempresa=codempresa, coduser=request.usuario["coduser"],
                              initial=inicial)
        return render(request, "financieros/parametros.html", {**contexto, "form": form})

    form = ParametrosForm(request.POST, definicion=definicion, codempresa=codempresa,
                          coduser=request.usuario["coduser"])
    error = None
    if form.is_valid():
        d = form.cleaned_data
        corte = d.get("fecha_fin")                       # cuentas corrientes: el año / mes salen de la fecha
        p = Parametros(
            codempresa=codempresa, sistema=definicion.sistema,
            anio=corte.year if corte else d.get("anio") or date.today().year,
            mes=corte.month if corte else d.get("mes") or date.today().month,
            nombre=d["nombre"], nivel=d.get("nivel"),
            anio2=d.get("anio2"), mes2=d.get("mes2"),
            **{k: d.get(k, v) for k, v in definicion.opciones.items()},
            **{k: d[k] for k in CAMPOS_EXTRA if d.get(k) not in (None, "")},
        )
        p.coduser = request.usuario["coduser"]
        p.dim = definicion.dimension
        inicio = time.perf_counter()
        try:
            rep = definicion.funcion(p)
        except ErrorReporte as e:
            error = ctx.rotula(str(e), p.dim)
        else:
            _rotula(rep, p.dim)
            calculo = time.perf_counter() - inicio
            _registra_reciente(request, comando, contexto, p)
            if p.folio and getattr(rep, "folio", None) == 0:     # libros foliados (Crystal)
                rep.folio = p.folio
            if isinstance(rep, ArchivoTexto):
                resp = HttpResponse(rep.contenido(), content_type="text/plain; charset=windows-1252")
                resp["Content-Disposition"] = content_disposition_header(True, rep.nombre_archivo)
                resp["X-Tiempo"] = f"{calculo:.2f}"
                return resp
            if d["salida"] == "excel":
                contenido = genera_excel(rep)
                total = time.perf_counter() - inicio
                log.info("%s %s %s-%02d: cálculo %.2fs, total %.2fs, %d filas", comando, p.codempresa,
                         p.anio, p.mes, calculo, total, len(rep.filas))
                resp = HttpResponse(contenido, content_type=XLSX)
                resp["Content-Disposition"] = content_disposition_header(True, rep.nombre_archivo)
                resp["X-Tiempo"] = f"{total:.2f}"
                return resp
            hojas = rep.hojas if isinstance(rep, Libro) else [rep]
            return render(request, "financieros/pantalla.html", {
                **contexto, "rep": hojas[0], "titulo_libro": rep.titulo, "segundos": calculo,
                "hojas": [_hoja_pantalla(h, n) for n, h in enumerate(hojas)],
                "total_filas": len(rep.filas),
                "niveles": range(1, max((f.nivel for f in rep.filas), default=1) + 1),
                "nivel_inicial": p.nivel or 0,
                "datos_post": [(k, v) for k, valores in request.POST.lists() for v in valores
                               if k not in ("csrfmiddlewaretoken", "salida")],
            })
    else:
        error = form.primer_error()

    if _es_fetch(request):
        return JsonResponse({"error": error}, status=400)
    return render(request, "financieros/parametros.html", {**contexto, "form": form, "error": error})


LIMITE_PANTALLA = 3000   # filas por hoja en pantalla; el Excel siempre lleva todo


def _formato(valor, pct, entero=False):
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    if pct:
        return f"{valor * 100:,.2f}%"
    if entero:
        return f"{valor:,.0f}"
    if abs(valor) < 0.005:
        return "-"
    return f"({abs(valor):,.2f})" if valor < 0 else f"{valor:,.2f}"


def _hoja_pantalla(rep, n):
    visibles = [i for i in range(len(rep.columnas)) if i not in rep.columnas_ocultas]
    grupos = []
    if rep.grupos_columnas:
        pos = 0
        for ini, fin, texto in sorted(rep.grupos_columnas):
            cols = [i for i in visibles if ini <= i <= fin]
            if not cols:
                continue
            antes = len([i for i in visibles if pos <= i < ini])
            if antes:
                grupos.append(("", antes))
            grupos.append((texto, len(cols)))
            pos = fin + 1
    filas = _filas_pantalla(rep, visibles)
    return {
        "id": f"hoja-{n}", "rep": rep, "nombre": rep.hoja or rep.titulo,
        "columnas": [rep.columnas[i] for i in visibles],
        "grupos": grupos,
        "filas": filas[:LIMITE_PANTALLA],
        "recortada": len(filas) - LIMITE_PANTALLA if len(filas) > LIMITE_PANTALLA else 0,
        "encabezado_a": rep.encabezado_a[0], "encabezado_b": rep.encabezado_b[0],
    }


def _filas_pantalla(rep, visibles):
    out = []
    for f in rep.filas:
        out.append({
            "blanco": f.tipo == "blanco",
            "total": f.tipo == "total",
            "grupo": f.tipo == "grupo",
            "vacia": not any(v not in (None, "") for v in f.valores),   # encabezado sin valores: ocupa el ancho
            "cuenta": f.cuenta,
            "nombre": f.nombre.strip(),
            "nivel": f.nivel,
            "oculta": f.oculta,
            "vinculo": f.vinculo,
            "negrita": f.negrita,
            "alerta": f.alerta,
            "celdas": [
                (_formato(f.valores[i] if i < len(f.valores) else None, col.es_pct or id(f) in rep.porcentaje_filas,
                          col.entero),
                 f.negrita or i in f.negrita_celdas,
                 isinstance(f.valores[i], (int, float)) and f.valores[i] < 0 if i < len(f.valores) else False,
                 col.texto)
                for i, col in enumerate(rep.columnas) if i in visibles
            ],
        })
    return out
