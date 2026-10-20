"""
Escritura del reporte a .xlsx con XlsxWriter.

Mismo diseño que las plantillas de conta\\plantillas (encabezado en filas 1-6,
títulos en 7-8, datos desde la fila 10), pero generado en un solo paso. Las
fórmulas (SUBTOTAL, totales, variaciones) se escriben junto con su valor ya
calculado para que el archivo se vea correcto aun sin recalcular.

Acepta un Reporte (una hoja) o un Libro (varias hojas con fórmulas entre ellas).
"""
import io
import re

import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

from .modelo import Libro

FILA_DATOS = 9        # fila 10 de Excel (base 0), igual que xcurrow = 10 en VFP
COL_DATOS = 2         # columna C
FMT_NUM = '_(* #,##0.00_);_(* (#,##0.00);_(* "-"??_);_(@_)'
FMT_PCT = "0.00%;[Red]-0.00%"


def nombre_hoja(texto):
    """Nombre válido de hoja de Excel (máx. 31 caracteres, sin []:*?/\\)."""
    return re.sub(r"[\[\]:*?/\\]", "", texto)[:31] or "Hoja"


def genera_excel(rep):
    hojas = rep.hojas if isinstance(rep, Libro) else [rep]
    salida = io.BytesIO()
    wb = xlsxwriter.Workbook(salida, {"in_memory": True})
    formatos = _formatos(wb)

    # Primera pasada: posición (hoja, fila) de cada Fila, para fórmulas entre hojas
    posicion = {}
    for h in hojas:
        h.hoja = h.hoja or nombre_hoja(h.titulo)
        for n, fila in enumerate(h.filas):
            posicion[id(fila)] = (h.hoja, FILA_DATOS + n)

    for h in hojas:
        _escribe_hoja(wb, h, formatos, posicion)
    wb.close()
    return salida.getvalue()


def _formatos(wb):
    return {
        "titulo": wb.add_format({"bold": True, "font_size": 12}),
        "texto": wb.add_format({}),
        "texto_b": wb.add_format({"bold": True}),
        "grupo": wb.add_format({"bold": True, "font_size": 12, "bottom": 1}),
        "enc": wb.add_format({"bold": True, "align": "center", "valign": "vcenter", "text_wrap": True,
                              "bottom": 1}),
        "enc_grupo": wb.add_format({"bold": True, "align": "center", "valign": "vcenter", "bottom": 1,
                                    "bg_color": "#EEF2FF"}),
        "fecha": wb.add_format({"num_format": "dd/mm/yyyy hh:mm:ss", "align": "left"}),
        "num": wb.add_format({"num_format": FMT_NUM}),
        "num_b": wb.add_format({"num_format": FMT_NUM, "bold": True}),
        "pct": wb.add_format({"num_format": FMT_PCT}),
        "pct_b": wb.add_format({"num_format": FMT_PCT, "bold": True}),
        "int": wb.add_format({"num_format": "#,##0"}),
        "int_b": wb.add_format({"num_format": "#,##0", "bold": True}),
        "texto_alerta": wb.add_format({"bold": True, "font_color": "#C00000"}),
        "num_alerta": wb.add_format({"num_format": FMT_NUM, "bold": True, "font_color": "#C00000"}),
        "link": wb.add_format({"font_color": "#2563EB", "underline": 1}),
    }


def _escribe_tabla_plana(wb, rep, f):
    """Hoja de datos: título, Tabla de Excel con filtros (lista para insertar una tabla dinámica)."""
    ws = wb.add_worksheet(rep.hoja)
    ws.write(0, 0, f"{rep.titulo} · {rep.subtitulo}", f["titulo"])
    columnas = [rep.encabezado_a[0], rep.encabezado_b[0]] + [c.titulo for c in rep.columnas]
    anchos = [rep.encabezado_a[1], rep.encabezado_b[1]] + [c.ancho for c in rep.columnas]
    datos = []
    for fila in rep.filas:
        if fila.tipo == "blanco":
            continue
        datos.append([fila.cuenta, fila.nombre] + [("" if v is None else v) for v in fila.valores])
    definicion = []
    for i, (titulo, ancho) in enumerate(zip(columnas, anchos)):
        ws.set_column(i, i, ancho)
        es_num = i >= 2 and not rep.columnas[i - 2].texto
        definicion.append({"header": titulo, **({"format": f["num"]} if es_num else {})})
    if datos:
        ws.add_table(2, 0, 2 + len(datos), len(columnas) - 1, {
            "data": datos, "name": re.sub(r"\W", "_", rep.hoja)[:30] or "Datos",
            "style": "Table Style Light 9", "columns": definicion,
        })
    ws.freeze_panes(3, 0)


def _escribe_hoja(wb, rep, f, posicion):
    if rep.tabla_plana:
        return _escribe_tabla_plana(wb, rep, f)
    ws = wb.add_worksheet(rep.hoja)
    ws.hide_gridlines(2)
    ws.set_column(0, 0, rep.encabezado_a[1])
    ws.set_column(1, 1, rep.encabezado_b[1])
    for i, col in enumerate(rep.columnas):
        ws.set_column(COL_DATOS + i, COL_DATOS + i, col.ancho, None,
                      {"hidden": True} if i in rep.columnas_ocultas else {})

    ws.write(0, 0, rep.empresa.razonsocial, f["texto"])
    ws.write(1, 0, f"NIT: {rep.empresa.nit}", f["texto"])
    ws.write(2, 0, rep.titulo, f["titulo"])
    ws.write(3, 0, rep.subtitulo, f["texto"])
    ws.write(4, 0, rep.leyenda_moneda, f["texto"])
    if rep.fecha_hora:
        ws.write_datetime(5, 0, rep.fecha_hora, f["fecha"])
    if rep.vinculo_regreso:
        ws.write_url(4, COL_DATOS + max(len(rep.columnas) - 2, 0), f"internal:'{rep.vinculo_regreso}'!A1",
                     f["link"], "Regresar al Resumen")

    fila_titulos = 6
    if rep.grupos_columnas:
        for ini, fin, texto in rep.grupos_columnas:
            if fin > ini:
                ws.merge_range(6, COL_DATOS + ini, 6, COL_DATOS + fin, texto, f["enc_grupo"])
            else:
                ws.write(6, COL_DATOS + ini, texto, f["enc_grupo"])
        fila_titulos = 7
    ws.write(fila_titulos, 0, rep.encabezado_a[0], f["enc"])
    ws.write(fila_titulos, 1, rep.encabezado_b[0], f["enc"])
    for i, col in enumerate(rep.columnas):
        ws.write(fila_titulos, COL_DATOS + i, col.titulo, f["enc"])
        if col.subtitulo:
            ws.write(fila_titulos + 1, COL_DATOS + i, col.subtitulo, f["enc"])
    ws.set_row(fila_titulos, 30)
    ws.freeze_panes(FILA_DATOS, COL_DATOS)
    # Impresión: carta, ancho de una página, títulos repetidos y número de página / folio
    ws.set_paper(1)
    ws.fit_to_pages(1, 0)
    ws.repeat_rows(0, FILA_DATOS - 1)
    derecha = []
    if rep.folio:
        ws.set_start_page(rep.folio)
        derecha.append("Folio: &P")
    if rep.resolucion:
        derecha.append(f"RESOLUCIÓN: {rep.resolucion.replace('&', '&&')}")
    if derecha:
        ws.set_header("&R" + "\n".join(derecha))
    ws.set_footer("&RPág. &P")
    ws.outline_settings(True, False, True, False)   # botones de grupo arriba (el padre está arriba)

    for n, fila in enumerate(rep.filas):
        r = FILA_DATOS + n
        opciones = {}
        if fila.tipo == "cuenta" and fila.nivel > 1:
            opciones["level"] = min(fila.nivel - 1, 7)
        if fila.oculta:
            opciones["hidden"] = True
        if opciones:
            ws.set_row(r, None, None, opciones)
        if fila.tipo == "blanco":
            continue

        txt = f["grupo"] if fila.tipo == "grupo" else (f["texto_b"] if fila.negrita else f["texto"])
        if fila.alerta:
            txt = f["texto_alerta"]
        if fila.vinculo:
            ws.write_url(r, 0, f"internal:'{fila.vinculo}'!A1", f["link"], fila.cuenta or fila.nombre)
        elif fila.cuenta:
            ws.write_string(r, 0, fila.cuenta, txt)
        ws.write_string(r, 1, fila.nombre, txt)

        for i, col in enumerate(rep.columnas):
            valor = fila.valores[i] if i < len(fila.valores) else None
            if valor is None:
                continue
            negrita = fila.negrita or i in fila.negrita_celdas
            c = COL_DATOS + i
            if isinstance(valor, str):
                ws.write_string(r, c, valor, txt)
                continue
            pct = col.es_pct or id(fila) in rep.porcentaje_filas
            if pct:
                fmt = f["pct_b" if negrita else "pct"]
            else:
                fmt = f[("int" if col.entero else "num") + ("_b" if negrita else "")]
                if fila.alerta and not col.entero:
                    fmt = f["num_alerta"]
            formula = _formula(rep, fila, col, i, r, posicion) if rep.usar_formulas else None
            if formula:
                ws.write_formula(r, c, formula, fmt, valor)
            else:
                ws.write_number(r, c, valor, fmt)


def _celda(rep, posicion, fila, col):
    hoja, r = posicion[id(fila)]
    celda = xl_rowcol_to_cell(r, COL_DATOS + col)
    return celda if hoja == rep.hoja else f"'{hoja}'!{celda}"


def _formula(rep, fila, col, i, r, posicion):
    c = COL_DATOS + i
    spec = fila.formulas.get(i) or col.formula
    if spec:
        tipo = spec[0]
        if tipo in ("resta", "suma", "pct", "ratio"):
            _, a, b = spec
            ca = xl_rowcol_to_cell(r, COL_DATOS + a)
            cb = xl_rowcol_to_cell(r, COL_DATOS + b)
            return {
                "resta": f"={ca}-{cb}",
                "suma": f"=SUM({ca}:{cb})",
                "pct": f"=IF({cb}=0,IF({ca}=0,0,1),{ca}/{cb})",
                "ratio": f'=IF({cb}=0,"",{ca}/{cb})',
            }[tipo]
        if tipo == "refs":
            partes = "".join(("+" if s > 0 else "-") + _celda(rep, posicion, ref, k) for s, ref, k in spec[1])
            return "=" + partes.lstrip("+")
        if tipo == "factor":
            _, ref, k, factor = spec
            return f"={_celda(rep, posicion, ref, k)}*{factor}"
        if tipo == "subtotal":
            _, desde, hasta = spec
            return f"=SUBTOTAL(9,{_celda(rep, posicion, desde, i)}:{_celda(rep, posicion, hasta, i)})"
        if tipo == "ratio_filas":
            _, ref, k, k_total = spec
            num, den = _celda(rep, posicion, ref, k), _celda(rep, posicion, ref, k_total)
            return f"=IF({den}=0,0,{num}/{den})"
        if tipo == "suma_cols":
            _, a, b = spec
            return f"=SUM({xl_rowcol_to_cell(r, COL_DATOS + a)}:{xl_rowcol_to_cell(r, COL_DATOS + b)})"
        if tipo == "lineal":
            partes = "".join(("+" if s > 0 else "-") + xl_rowcol_to_cell(r, COL_DATOS + k) for s, k in spec[1])
            return "=" + partes.lstrip("+")
        if tipo == "corrido":               # saldo corrido: saldo de la fila anterior + cargo - abono
            _, previa, k_saldo, k_cargo, k_abono = spec
            return (f"={_celda(rep, posicion, previa, k_saldo)}+{xl_rowcol_to_cell(r, COL_DATOS + k_cargo)}"
                    f"-{xl_rowcol_to_cell(r, COL_DATOS + k_abono)}")
        if tipo == "producto":
            partes = "".join(("+" if s > 0 else "-") + xl_rowcol_to_cell(r, COL_DATOS + k) for s, k in spec[1])
            return f"=({partes.lstrip('+')})*{xl_rowcol_to_cell(r, COL_DATOS + spec[2])}/100"
    if fila.tipo == "total" and fila.rango:
        desde = _celda(rep, posicion, fila.rango[0], i)
        hasta = _celda(rep, posicion, fila.rango[1], i)
        return f"=SUBTOTAL(9,{desde}:{hasta})"
    if fila.tipo == "cuenta" and fila.hasta is not None:
        desde = xl_rowcol_to_cell(r + 1, c)
        hasta = _celda(rep, posicion, fila.hasta, i)
        return f"=SUBTOTAL(9,{desde}:{hasta})"
    if fila.tipo == "total" and fila.refs:
        partes = "".join(("+" if s > 0 else "-") + _celda(rep, posicion, ref, i) for s, ref in fila.refs)
        return "=" + partes.lstrip("+")
    return None
