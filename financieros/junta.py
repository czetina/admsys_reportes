"""
Paquete de la junta directiva: los reportes que la gerencia general presenta cada mes, generados
a Excel de una vez: en un .zip desde la web (/financieros/junta/) o en una carpeta local con el
comando `reportes_junta`.

Cada reporte se arma igual que desde la web: el formulario con sus valores iniciales, solo con el
año / mes del cierre cambiados, así el resultado es el mismo que si se pidiera a mano.
"""
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .catalogo import REPORTES_JUNTA
from .contexto import ErrorReporte, fin_de_mes, mes_anterior
from .excel import genera_excel
from .forms import ParametrosForm
from .modelo import ArchivoTexto
from .registro import REPORTES
from .views import arma_parametros, rotula


@dataclass
class Resultado:
    comando: str
    nombre: str
    archivo: str = ""          # nombre del .xlsx
    contenido: bytes = b""
    error: str = ""


def _datos_formulario(form, definicion, anio, mes):
    """Los valores iniciales del formulario, con el cierre pedido (y el periodo comparativo que le toca)."""
    if definicion.periodo2 == "mes_anterior":
        anio2, mes2 = mes_anterior(anio, mes)
    else:
        anio2, mes2 = anio - 1, mes
    fijos = {"anio": anio, "mes": mes, "anio2": anio2, "mes2": mes2, "salida": "excel",
             "fecha_ini": fin_de_mes(anio, mes).replace(day=1), "fecha_fin": fin_de_mes(anio, mes)}
    datos = {}
    for nombre, campo in form.fields.items():
        valor = fijos[nombre] if nombre in fijos else (campo.initial() if callable(campo.initial) else campo.initial)
        if valor is None and campo.required and getattr(campo, "choices", None):
            valor = next((v for v, _ in campo.choices if v not in ("", None)), None)
        if isinstance(valor, bool):
            if valor:
                datos[nombre] = "on"
        elif valor is not None:
            datos[nombre] = valor.isoformat() if hasattr(valor, "isoformat") else str(valor)
    return datos


def _genera_uno(comando, nombre, codempresa, coduser, anio, mes):
    res = Resultado(comando, nombre)
    definicion = REPORTES.get(comando)
    if definicion is None:
        res.error = "reporte no migrado"
        return res
    vacio = ParametrosForm(definicion=definicion, codempresa=codempresa, coduser=coduser)
    form = ParametrosForm(_datos_formulario(vacio, definicion, anio, mes), definicion=definicion,
                          codempresa=codempresa, coduser=coduser)
    if not form.is_valid():
        res.error = form.primer_error()
        return res
    p = arma_parametros(form.cleaned_data, definicion, codempresa, coduser)
    try:
        rep = definicion.funcion(p)
    except ErrorReporte as e:
        res.error = str(e)
        return res
    if isinstance(rep, ArchivoTexto):
        res.error = "el reporte no sale a Excel"
        return res
    rotula(rep, p.dim)
    res.archivo, res.contenido = rep.nombre_archivo, genera_excel(rep)
    return res


def genera(codempresa, coduser, anio, mes, comandos=REPORTES_JUNTA):
    """Genera cada reporte en memoria; uno que falle no detiene a los demás (queda con `error`)."""
    return [_genera_uno(comando, nombre, codempresa, coduser, anio, mes) for comando, nombre in comandos]


def guarda(resultados, carpeta):
    """Escribe en `carpeta` los reportes generados; devuelve la carpeta."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    for r in resultados:
        if r.contenido:
            (carpeta / r.archivo).write_bytes(r.contenido)
    return carpeta


def comprime(resultados):
    """Un .zip con los reportes generados y, si alguno falló, NO GENERADOS.txt con el motivo."""
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        for r in resultados:
            if r.contenido:
                z.writestr(r.archivo, r.contenido)
        fallidos = [f"{r.nombre}: {r.error}" for r in resultados if r.error]
        if fallidos:
            z.writestr("NO GENERADOS.txt", "\r\n".join(fallidos).encode("utf-8-sig"))
    return salida.getvalue()
