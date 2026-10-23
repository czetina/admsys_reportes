"""Tablero CFO en Excel: semáforo, indicadores con fórmulas vivas sobre «Datos base», matriz, alertas y gráficas."""
import io

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

from .tablero_cfo import VENTANA

AZUL, GRIS = "#1F3A5F", "#6B7280"

# Variables de «Datos base», en orden, con su etiqueta
VARIABLES = (
    ("activo_total", "Activo total"), ("activo_circulante", "Activo corriente"), ("inventarios", "Equipo para arrendar"),
    ("gastos_anticipados", "Gastos anticipados"), ("pasivo_circulante", "Pasivo corriente"), ("pasivo_total", "Pasivo total"),
    ("patrimonio", "Patrimonio (incluye utilidad del ejercicio)"), ("cartera_neta", "Cartera neta de arrendamiento"),
    ("prestamos", "Préstamos CP + LP"), ("provisiones_acumuladas", "Estimaciones y reservas para incobrables"),
    ("cuotas_por_cobrar", "Cuotas facturadas por cobrar"), ("cartera_mora_30", "Cuotas vencidas > 30 días"),
    ("cartera_mora_60", "Cuotas vencidas > 60 días"), ("cartera_mora_90", "Cuotas vencidas > 90 días"),
    ("ingresos_arrendamiento", "Ingresos por arrendamiento financiero (mes)"), ("ingresos_servicios", "Servicios y factoraje (mes)"),
    ("venta_bienes", "Venta de bienes (mes)"), ("otros_ingresos", "Otros e ingresos financieros (mes)"),
    ("ingresos_totales", "Ingresos totales (mes)"), ("costo_financiamiento", "Costo de financiamiento (mes)"),
    ("gastos_operativos", "Gastos operativos sin incobrables (mes)"), ("depreciaciones", "Depreciaciones (mes)"),
    ("utilidad_neta", "Resultado del mes"), ("uaii", "UAII (mes)"), ("flujo_operativo_disponible", "EBITDA aproximado (mes)"),
    ("abonos_capital", "Abonos a capital pagados (mes)"), ("servicio_deuda", "Servicio de la deuda (mes)"),
)


def genera(tablero):
    salida = io.BytesIO()
    wb = xlsxwriter.Workbook(salida, {"in_memory": True})
    F = {
        "tit": wb.add_format({"bold": True, "font_size": 16, "font_color": AZUL}),
        "sub": wb.add_format({"italic": True, "font_color": GRIS}),
        "th": wb.add_format({"bold": True, "bg_color": AZUL, "font_color": "white", "border": 1, "text_wrap": True, "valign": "vcenter"}),
        "txt": wb.add_format({"border": 1, "text_wrap": True, "valign": "top"}),
        "q": wb.add_format({"border": 1, "num_format": "#,##0.00"}),
        "pct": wb.add_format({"border": 1, "num_format": "0.0\" %\""}),
        "pp": wb.add_format({"border": 1, "num_format": "0.00\" p.p.\""}),
        "x": wb.add_format({"border": 1, "num_format": "0.00\"x\""}),
        "dias": wb.add_format({"border": 1, "num_format": "0.0\" días\""}),
        "ok": wb.add_format({"bg_color": "#DCFCE7", "font_color": "#166534"}),
        "warn": wb.add_format({"bg_color": "#FEF3C7", "font_color": "#92400E"}),
        "bad": wb.add_format({"bg_color": "#FEE2E2", "font_color": "#991B1B"}),
    }
    emp, base, kpis = tablero.empresa, tablero.base, tablero.kpis
    cierre = kpis[-1]["mes"]

    wt = wb.add_worksheet("Tablero")
    wi = wb.add_worksheet("Indicadores")
    wd = wb.add_worksheet("Datos base")
    wg = wb.add_worksheet("Ingresos NIIF")
    wm = wb.add_worksheet("Matriz")
    wa = wb.add_worksheet("Alertas")

    # ------------------------------------------------------------ Datos base
    wd.write(0, 0, f"{emp.razonsocial} · variables por mes", F["tit"])
    wd.write(1, 0, "Saldos al cierre de cada mes y flujos del mes, de la contabilidad y de CXC. "
                   "Los 12 meses previos sirven para los flujos de 12 meses y los saldos promedio.", F["sub"])
    wd.write(3, 0, "Variable", F["th"])
    for j, b in enumerate(base):
        wd.write(3, j + 1, b["mes"], F["th"])
    fila = {}
    for i, (clave, etiqueta) in enumerate(VARIABLES):
        r = 4 + i
        fila[clave] = r
        wd.write(r, 0, etiqueta, F["txt"])
        for j, b in enumerate(base):
            if b[clave] is None:
                wd.write_blank(r, j + 1, None, F["q"])
            else:
                wd.write_number(r, j + 1, b[clave], F["q"])
    wd.set_column(0, 0, 44)
    wd.set_column(1, len(base), 15)
    wd.freeze_panes(4, 1)

    def celda(clave, j):
        return "'Datos base'!" + xl_rowcol_to_cell(fila[clave], j + 1)

    def flujo(clave, j):           # suma de los 12 meses que terminan en j
        return f"SUM('Datos base'!{xl_rowcol_to_cell(fila[clave], j - VENTANA + 2)}:{xl_rowcol_to_cell(fila[clave], j + 1)})"

    def promedio(clave, j):        # promedio de los 13 cierres
        return f"AVERAGE('Datos base'!{xl_rowcol_to_cell(fila[clave], j - VENTANA + 1)}:{xl_rowcol_to_cell(fila[clave], j + 1)})"

    def seguro(expr):
        return f'=IFERROR({expr},"")'

    formulas = {
        "roe": lambda j: f"{flujo('utilidad_neta', j)}/{promedio('patrimonio', j)}*100",
        "roa": lambda j: f"{flujo('utilidad_neta', j)}/{promedio('activo_total', j)}*100",
        "tasa_activa": lambda j: f"{flujo('ingresos_arrendamiento', j)}/{promedio('cartera_neta', j)}*100",
        "costo_fondos": lambda j: f"{flujo('costo_financiamiento', j)}/{promedio('prestamos', j)}*100",
        "spread": lambda j: f"{flujo('ingresos_arrendamiento', j)}/{promedio('cartera_neta', j)}*100"
                            f"-{flujo('costo_financiamiento', j)}/{promedio('prestamos', j)}*100",
        "nim": lambda j: f"({flujo('ingresos_arrendamiento', j)}-{flujo('costo_financiamiento', j)})/{promedio('cartera_neta', j)}*100",
        "margen_neto": lambda j: f"{flujo('utilidad_neta', j)}/{flujo('ingresos_totales', j)}*100",
        "npl_30": lambda j: f"{celda('cartera_mora_30', j)}/{celda('cartera_neta', j)}*100",
        "npl_60": lambda j: f"{celda('cartera_mora_60', j)}/{celda('cartera_neta', j)}*100",
        "npl_90": lambda j: f"{celda('cartera_mora_90', j)}/{celda('cartera_neta', j)}*100",
        "cobertura": lambda j: f"{celda('provisiones_acumuladas', j)}/{celda('cartera_mora_90', j)}*100",
        "dso": lambda j: f"{celda('cuotas_por_cobrar', j)}/({flujo('ingresos_arrendamiento', j)}+{flujo('ingresos_servicios', j)})*365",
        "razon_corriente": lambda j: f"{celda('activo_circulante', j)}/{celda('pasivo_circulante', j)}",
        "prueba_acida": lambda j: f"({celda('activo_circulante', j)}-{celda('inventarios', j)}-{celda('gastos_anticipados', j)})"
                                  f"/{celda('pasivo_circulante', j)}",
        "deuda_patrimonio": lambda j: f"{celda('pasivo_total', j)}/{celda('patrimonio', j)}",
        "endeudamiento": lambda j: f"{celda('pasivo_total', j)}/{celda('activo_total', j)}*100",
        "dscr": lambda j: f"{flujo('flujo_operativo_disponible', j)}/{flujo('servicio_deuda', j)}",
        "tie": lambda j: f"{flujo('uaii', j)}/{flujo('costo_financiamiento', j)}",
        "eficiencia": lambda j: f"{flujo('gastos_operativos', j)}/({flujo('ingresos_arrendamiento', j)}-{flujo('costo_financiamiento', j)})*100",
        "part_niif16": lambda j: f"{flujo('ingresos_arrendamiento', j)}/{flujo('ingresos_totales', j)}*100",
        "rotacion_activos": lambda j: f"{flujo('ingresos_totales', j)}/{promedio('activo_total', j)}",
    }

    # ------------------------------------------------------------ Indicadores
    wi.write(0, 0, "Indicadores por mes (fórmulas sobre «Datos base»)", F["tit"])
    wi.write(1, 0, "Flujos de los últimos 12 meses sobre el saldo promedio de 13 cierres. Cambie «Datos base» y todo se recalcula.", F["sub"])
    wi.write(3, 0, "Indicador", F["th"])
    wi.write(3, 1, "Unidad", F["th"])
    primero = len(base) - len(kpis)                       # columna de «Datos base» del primer mes mostrado
    for k, km in enumerate(kpis):
        wi.write(3, k + 2, km["mes"], F["th"])
    unidad = {m["id"]: m["unidad"] for m in tablero.matriz}
    unidad.update(tasa_activa="pct", costo_fondos="pct")
    nombres = {m["id"]: m["indicador"] for m in tablero.matriz}
    nombres.update(tasa_activa="Tasa activa promedio", costo_fondos="Costo promedio de fondos")
    orden = [m["id"] for m in tablero.matriz if m["id"] in formulas] + ["tasa_activa", "costo_fondos"]
    fila_ind = {}
    for i, ind in enumerate(orden):
        r = 4 + i
        fila_ind[ind] = r
        wi.write(r, 0, nombres[ind], F["txt"])
        wi.write(r, 1, unidad[ind], F["txt"])
        for k in range(len(kpis)):
            j = primero + k
            if j - VENTANA < 0 and ind in ("roe", "roa", "tasa_activa", "costo_fondos", "spread", "nim", "margen_neto", "dso",
                                           "dscr", "tie", "eficiencia", "part_niif16", "rotacion_activos"):
                wi.write(r, k + 2, "", F[unidad[ind]])
                continue
            wi.write_formula(r, k + 2, seguro(formulas[ind](j)), F[unidad[ind]], kpis[k].get(ind) or "")
    wi.set_column(0, 0, 40)
    wi.set_column(1, 1, 7)
    wi.set_column(2, len(kpis) + 1, 11)
    wi.freeze_panes(4, 2)
    ult, ant = xl_col_to_name(len(kpis) + 1), xl_col_to_name(len(kpis))

    # ------------------------------------------------------------ Tablero (semáforo)
    wt.activate()
    wt.write(0, 0, f"Tablero CFO · {emp.razonsocial}", F["tit"])
    wt.write(1, 0, f"Cierre {cierre} · verde = en meta, amarillo = vigilar, rojo = fuera de rango", F["sub"])
    for j, h in enumerate(["Módulo", "Indicador", "Último", "Mes anterior", "Cambio", "Meta", "Bueno / mín.",
                           "Límite / máx.", "Dirección", "Estado"]):
        wt.write(3, j, h, F["th"])
    for i, m in enumerate(tablero.matriz):
        r = 4 + i
        u = m["unidad"]
        wt.write(r, 0, m["modulo"], F["txt"])
        wt.write(r, 1, m["indicador"], F["txt"])
        if m["id"] in fila_ind:
            fr = fila_ind[m["id"]] + 1
            wt.write_formula(r, 2, f"=Indicadores!{ult}{fr}", F[u])
            wt.write_formula(r, 3, f"=Indicadores!{ant}{fr}", F[u])
            wt.write_formula(r, 4, f'=IFERROR(C{r + 1}-D{r + 1},"")', F["pp" if u in ("pct", "pp") else u])
        else:
            for c in (2, 3, 4):
                wt.write(r, c, "No aplica" if c == 2 else "", F["txt"])
        wt.write(r, 5, m["meta"], F["txt"])
        lo = m["min"] if m["direccion"] == "rango" else m["bueno"]
        hi = m["max"] if m["direccion"] == "rango" else m["limite"]
        wt.write_number(r, 6, lo, F[u])
        wt.write_number(r, 7, hi, F[u])
        wt.write(r, 8, m["direccion"], F["txt"])
        c, g, h = f"C{r + 1}", f"G{r + 1}", f"H{r + 1}"
        if m["id"] not in fila_ind:
            wt.write(r, 9, "No aplica", F["txt"])
            continue
        regla = {
            "mayor": f'IF({c}>={g},"En meta",IF({c}>={h},"Vigilar","Fuera de rango"))',
            "menor": f'IF({c}<={g},"En meta",IF({c}<={h},"Vigilar","Fuera de rango"))',
            "rango": f'IF(AND({c}>={g},{c}<={h}),"En meta",IF(AND({c}>={g}-({h}-{g})*0.25,{c}<={h}+({h}-{g})*0.25),'
                     f'"Vigilar","Fuera de rango"))',
        }[m["direccion"]]
        wt.write_formula(r, 9, f'=IF(ISNUMBER({c}),{regla},"Sin dato")', F["txt"])
    fin = 4 + len(tablero.matriz)
    for texto, fmt in (("En meta", F["ok"]), ("Vigilar", F["warn"]), ("Fuera de rango", F["bad"])):
        wt.conditional_format(f"J5:J{fin}", {"type": "cell", "criteria": "==", "value": f'"{texto}"', "format": fmt})
    wt.write(fin + 1, 1, "Indicadores fuera de rango", F["txt"])
    wt.write_formula(fin + 1, 2, f'=COUNTIF(J5:J{fin},"Fuera de rango")', F["txt"])
    wt.write(fin + 2, 1, "Indicadores en vigilancia", F["txt"])
    wt.write_formula(fin + 2, 2, f'=COUNTIF(J5:J{fin},"Vigilar")', F["txt"])
    wt.set_column(0, 0, 30)
    wt.set_column(1, 1, 38)
    wt.set_column(2, 4, 12)
    wt.set_column(5, 5, 20)
    wt.set_column(6, 8, 12)
    wt.set_column(9, 9, 15)

    def grafica(titulo, series, celda_destino, eje):
        ch = wb.add_chart({"type": "line"})
        for nombre, ind in series:
            ch.add_series({"name": nombre, "categories": ["Indicadores", 3, 2, 3, len(kpis) + 1],
                           "values": ["Indicadores", fila_ind[ind], 2, fila_ind[ind], len(kpis) + 1],
                           "marker": {"type": "circle", "size": 4}})
        ch.set_title({"name": titulo, "name_font": {"size": 11}})
        ch.set_y_axis({"name": eje, "major_gridlines": {"visible": True, "line": {"color": "#E5E7EB"}}})
        ch.set_legend({"position": "bottom"})
        ch.set_size({"width": 520, "height": 280})
        wt.insert_chart(celda_destino, ch)

    grafica("Spread: tasa activa vs costo de fondos", [("Tasa activa", "tasa_activa"), ("Costo de fondos", "costo_fondos")], "L4", "%")
    grafica("Morosidad por tramo", [("> 30 días", "npl_30"), ("> 60 días", "npl_60"), ("> 90 días", "npl_90")], "L19", "%")
    grafica("ROE y margen neto", [("ROE", "roe"), ("Margen neto", "margen_neto")], "T4", "%")
    grafica("DSCR y TIE", [("DSCR", "dscr"), ("TIE", "tie")], "T19", "veces")

    # ------------------------------------------------------------ Ingresos por componente
    wg.write(0, 0, "Segregación de ingresos (NIIF 16 / NIIF 15)", F["tit"])
    comps = list(dict.fromkeys(x["componente"] for x in tablero.ingresos))
    meses = list(dict.fromkeys(x["mes"] for x in tablero.ingresos))
    wg.write(2, 0, "Componente", F["th"])
    wg.write(2, 1, "Norma", F["th"])
    for j, mes in enumerate(meses):
        wg.write(2, j + 2, mes, F["th"])
    monto = {(x["componente"], x["mes"]): x for x in tablero.ingresos}
    for i, c in enumerate(comps):
        wg.write(3 + i, 0, c, F["txt"])
        wg.write(3 + i, 1, monto[(c, meses[0])]["norma"], F["txt"])
        for j, mes in enumerate(meses):
            wg.write_number(3 + i, j + 2, monto[(c, mes)]["monto"], F["q"])
    wg.set_column(0, 0, 30)
    wg.set_column(2, len(meses) + 1, 13)
    ch = wb.add_chart({"type": "column", "subtype": "stacked"})
    for i in range(len(comps)):
        ch.add_series({"name": ["Ingresos NIIF", 3 + i, 0], "categories": ["Ingresos NIIF", 2, 2, 2, len(meses) + 1],
                       "values": ["Ingresos NIIF", 3 + i, 2, 3 + i, len(meses) + 1]})
    ch.set_title({"name": "Ingresos por componente", "name_font": {"size": 11}})
    ch.set_legend({"position": "bottom"})
    ch.set_size({"width": 520, "height": 280})
    wt.insert_chart("L34", ch)

    # ------------------------------------------------------------ Matriz y alertas
    wm.write(0, 0, "Matriz de indicadores", F["tit"])
    columnas = [("modulo", "Módulo", 26), ("indicador", "Indicador", 30), ("formula", "Fórmula", 46),
                ("fuente", "Fuente en ADMSys", 52), ("frecuencia", "Frecuencia", 12), ("meta", "Meta / benchmark", 20),
                ("visualizacion", "Visualización", 28)]
    for j, (_, h, w) in enumerate(columnas):
        wm.write(2, j, h, F["th"])
        wm.set_column(j, j, w)
    for i, m in enumerate(tablero.matriz):
        for j, (k, _, _) in enumerate(columnas):
            wm.write(3 + i, j, m[k], F["txt"])
    wm.freeze_panes(3, 2)

    wa.write(0, 0, f"Alertas al CFO · cierre {cierre}", F["tit"])
    for j, h in enumerate(["Regla", "Nivel", "Motivo", "Condición"]):
        wa.write(2, j, h, F["th"])
    condicion = {
        "Deterioro de cartera": "Morosidad 90+ > 3 % o alza ≥ 0.50 p.p. en 3 meses (crítica: ambas)",
        "Descalce de tasa": "Spread < 5 p.p. o compresión ≥ 0.75 p.p. en 3 meses (crítica: ambas)",
        "Cobertura y servicio de deuda": "Cobertura < 100 % o DSCR < 1.40x (crítica: DSCR < 1.25x o cobertura < 90 %)",
    }
    for i, a in enumerate(tablero.alertas):
        for j, v in enumerate((a["regla"], a["nivel"], a["motivo"], condicion[a["regla"]])):
            wa.write(3 + i, j, v, F["txt"])
    for texto, fmt in (("Sin alerta", F["ok"]), ("Advertencia", F["warn"]), ("Crítica", F["bad"])):
        wa.conditional_format("B4:B6", {"type": "cell", "criteria": "==", "value": f'"{texto}"', "format": fmt})
    wa.set_column(0, 0, 30)
    wa.set_column(1, 1, 13)
    wa.set_column(2, 3, 60)

    wb.close()
    return salida.getvalue()
