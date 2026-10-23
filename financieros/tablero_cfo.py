"""
Tablero CFO: indicadores financieros y de arrendamiento de los 12 meses que terminan en el cierre
elegido, calculados de la contabilidad (saldos y movimientos) y de los documentos de CXC.

Las cuentas de cada variable están en CUENTAS (prefijos de codcuenta, sin puntos). La cartera de
arrendamiento es todo lo que lleva CXC (clientes CP / LP y contratos en arrendamiento CP / LP, sin
sus estimaciones) menos los ingresos por devengar; la morosidad son las cuotas facturadas vencidas
(facturas y notas de débito), sin notas de reclasificación ni pagarés, cuyo vencimiento no es el
de las cuotas. Otra empresa con otro catálogo agrega su entrada en CUENTAS.

Rentabilidad, márgenes, coberturas y rotaciones usan los flujos de los últimos 12 meses sobre el
saldo promedio de 13 cierres (el mes y los 12 anteriores): un mes con una venta de bienes o un
diferencial cambiario grande no hace saltar el indicador. Los saldos solo suman cuentas de último
nivel, como el balance general.
"""
import time
from dataclasses import dataclass, field
from datetime import date

from core.db import consulta

from . import contexto as ctx
from . import ctacte
from .contexto import ErrorReporte, fin_de_mes, mes_anterior, ym
from .saldos import Movimientos, utilidad

CUENTAS = {
    "default": {
        "activo": ("1",), "activo_circulante": ("101",), "gastos_anticipados": ("1010303",),
        "inventarios": ("101040101", "101040102"),            # contratos en inventario y equipo adquirido para arrendar
        "pasivo": ("2",), "pasivo_circulante": ("201",), "patrimonio": ("3",),
        "cartera": ("1010201", "1010202", "1020101", "1020102", "101040103", "1020301"),
        "cartera_excluye": ("101020109", "101020209", "102030109"),
        "por_devengar": ("20102", "20202"),
        "provisiones": ("101020109", "101020209", "1020103", "102030109"),
        "prestamos": ("2010108", "20201"),
        "bancos": ("1010102", "1010103"),                   # abono a capital = cargo a préstamos pagado desde bancos
        "ing_arrendamiento": ("40101",), "ing_servicios": ("40102", "40103"), "ing_venta_bienes": ("40201",),
        "costo_financiamiento": ("50103",), "gastos_operativos": ("6",), "gastos_excluye": ("621",),
        "depreciaciones": ("620",),
        "tipos_mora": ("FACT", "FCE", "ND", "ND$", "NCARGO"),
    },
}

# id, módulo, indicador, fórmula, fuente en ADMSys, frecuencia, meta, dirección, bueno, límite, mín, máx, unidad, visualización
MATRIZ = [
    ("roe", "Rentabilidad y margen", "ROE", "Utilidad 12 meses ÷ Patrimonio promedio × 100",
     "Resultado de los últimos 12 meses (antes de ISR: no se registra mensualmente); patrimonio 3 + utilidad del ejercicio",
     "Mensual", "15 % a 20 %", "mayor", 15, 12, None, None, "pct", "Tarjeta + línea con banda meta"),
    ("roa", "Rentabilidad y margen", "ROA", "Utilidad 12 meses ÷ Activo total promedio × 100",
     "Resultado de 12 meses; activo total 1", "Mensual", "2 % a 4 %", "mayor", 2, 1.5, None, None, "pct", "Tarjeta + línea"),
    ("spread", "Rentabilidad y margen", "Spread de arrendamiento", "Tasa activa − Costo de fondos",
     "Tasa activa = ingresos por arrendamiento financiero 4.01.01 (12 meses) ÷ cartera neta promedio; costo de fondos = "
     "costo de financiamiento 5.01.03 (12 meses) ÷ préstamos 2.01.01.08 + 2.02.01 promedio", "Mensual", "≥ 5.0 p.p.",
     "mayor", 5, 4, None, None, "pp", "Dos líneas con el área del diferencial"),
    ("nim", "Rentabilidad y margen", "NIM (margen neto de interés)",
     "(Ingresos por arrendamiento − Costo de financiamiento) 12 meses ÷ Cartera neta promedio",
     "4.01.01; 5.01.03; cartera neta = clientes y contratos CP / LP − ingresos por devengar 2.01.02 / 2.02.02",
     "Mensual", "5 % a 8 %", "mayor", 5, 4, None, None, "pct", "Línea"),
    ("margen_neto", "Rentabilidad y margen", "Margen neto de utilidad", "Utilidad 12 meses ÷ Ingresos totales 12 meses × 100",
     "Resultado; ingresos 4 + ingresos financieros 7", "Mensual", "≥ 18 %", "mayor", 18, 14, None, None, "pct", "Barras"),
    ("npl_30", "Calidad de cartera y riesgo", "Morosidad > 30 días", "Cuotas facturadas vencidas > 30 días ÷ Cartera neta × 100",
     "Facturas y notas de débito de CXC con saldo, por días desde su vencimiento", "Semanal", "≤ 5 %", "menor", 5, 7,
     None, None, "pct", "Líneas por tramo"),
    ("npl_60", "Calidad de cartera y riesgo", "Morosidad > 60 días", "Cuotas facturadas vencidas > 60 días ÷ Cartera neta × 100",
     "Igual, tramo 60+", "Semanal", "≤ 4 %", "menor", 4, 5, None, None, "pct", "Líneas por tramo"),
    ("npl_90", "Calidad de cartera y riesgo", "Morosidad > 90 días (NPL)", "Cuotas facturadas vencidas > 90 días ÷ Cartera neta × 100",
     "Igual, tramo 90+", "Semanal", "≤ 3 %", "menor", 3, 4, None, None, "pct", "Tarjeta + líneas por tramo"),
    ("cobertura", "Calidad de cartera y riesgo", "Cobertura de provisiones",
     "Estimaciones y reservas para incobrables ÷ Cuotas vencidas > 90 días × 100",
     "1.01.02.01.09, 1.01.02.02.09, 1.02.01.03, 1.02.03.01.09; cuotas 90+", "Mensual", "≥ 100 %", "mayor", 100, 90,
     None, None, "pct", "Medidor"),
    ("dso", "Calidad de cartera y riesgo", "Días promedio de cobro (DSO)",
     "Cuotas facturadas por cobrar ÷ Ingresos facturados 12 meses × 365",
     "Saldo de facturas y notas de débito de CXC; arrendamiento 4.01.01 + servicios y factoraje 4.01.02 / 4.01.03", "Semanal", "≤ 30 días", "menor", 30, 40, None, None, "dias", "Barras"),
    ("desv_vr", "Calidad de cartera y riesgo", "Desviación del valor residual",
     "(Precio real de venta − Valor residual en libros) ÷ Valor residual en libros × 100",
     "Solo arrendamiento operativo: esta empresa no tiene flota en arrendamiento operativo", "Trimestral", "−5 % a +5 %",
     "rango", None, None, -5, 5, "pct", "Barras divergentes por contrato"),
    ("razon_corriente", "Solvencia, apalancamiento y liquidez", "Razón corriente", "Activo corriente ÷ Pasivo corriente",
     "1.01 ÷ 2.01", "Semanal", "1.0x a 1.5x", "rango", None, None, 1.0, 1.5, "x", "Medidor"),
    ("prueba_acida", "Solvencia, apalancamiento y liquidez", "Prueba ácida",
     "(Activo corriente − Inventarios − Gastos anticipados) ÷ Pasivo corriente",
     "1.01 − equipo para arrendar 1.01.04.01.01 / .02 − gastos anticipados 1.01.03.03 ÷ 2.01", "Semanal", "≥ 0.9x",
     "mayor", 0.9, 0.8, None, None, "x", "Tarjeta"),
    ("deuda_patrimonio", "Solvencia, apalancamiento y liquidez", "Apalancamiento (deuda / patrimonio)", "Pasivo total ÷ Patrimonio",
     "2 ÷ (3 + utilidad del ejercicio)", "Mensual", "≤ 5.0x", "menor", 5, 6, None, None, "x", "Línea con límite"),
    ("endeudamiento", "Solvencia, apalancamiento y liquidez", "Endeudamiento total (deuda / activo)", "Pasivo total ÷ Activo total × 100",
     "2 ÷ 1", "Mensual", "70 % a 85 %", "rango", None, None, 70, 85, "pct", "Medidor"),
    ("dscr", "Solvencia, apalancamiento y liquidez", "Cobertura del servicio de la deuda (DSCR)",
     "EBITDA aproximado ÷ (Intereses + Abonos a capital), 12 meses",
     "EBITDA = resultado + costo de financiamiento 5.01.03 + depreciaciones 6.20; abonos = cargos a préstamos 2.01.01.08 / 2.02.01 "
     "en pólizas pagadas desde bancos (sin reclasificaciones LP / CP ni diferencial cambiario)",
     "Mensual", "≥ 1.25x (ideal 1.40x)", "mayor", 1.4, 1.25, None, None, "x", "Línea con umbrales"),
    ("tie", "Solvencia, apalancamiento y liquidez", "Cobertura de intereses (TIE)", "UAII ÷ Gastos por intereses, 12 meses",
     "(Resultado + 5.01.03) ÷ 5.01.03", "Trimestral", "≥ 1.5x", "mayor", 1.5, 1.3, None, None, "x", "Barras"),
    ("eficiencia", "Eficiencia y segregación NIIF 15 / 16", "Ratio de eficiencia",
     "Gastos operativos ÷ Margen financiero bruto × 100, 12 meses",
     "Gastos 6 sin cuentas incobrables 6.21 ÷ (4.01.01 − 5.01.03)", "Mensual", "≤ 45 %", "menor", 45, 50, None, None, "pct",
     "Línea con límite"),
    ("part_niif16", "Eficiencia y segregación NIIF 15 / 16", "Ingresos bajo NIIF 16",
     "Ingresos por arrendamiento ÷ Ingresos totales × 100, 12 meses",
     "4.01.01 ÷ (4 + 7); NIIF 15: servicios 4.01.02 / 4.01.03 y venta de bienes 4.02.01", "Mensual", "≥ 85 %", "mayor", 85, 80,
     None, None, "pct", "Barras apiladas"),
    ("rotacion_activos", "Eficiencia y segregación NIIF 15 / 16", "Rotación de activos",
     "Ingresos totales 12 meses ÷ Activo total promedio", "(4 + 7) ÷ 1", "Trimestral", "≥ 0.13x", "mayor", 0.13, 0.12,
     None, None, "x", "Tarjeta"),
]
CAMPOS_MATRIZ = ("id", "modulo", "indicador", "formula", "fuente", "frecuencia", "meta", "direccion", "bueno", "limite",
                 "min", "max", "unidad", "visualizacion")

VENTANA = 12        # meses del flujo; los saldos promedian VENTANA + 1 cierres
MOSTRADOS = 12      # meses del tablero
_CACHE, _CACHE_MORA = {}, {}
_TTL = 30 * 60


@dataclass
class Tablero:
    empresa: object
    meses: list                       # "AAAA-MM" de los meses mostrados
    base: list                        # variables de cada mes, con los 12 previos (Excel «Datos base»)
    kpis: list                        # indicadores de cada mes mostrado
    ultimo: list                      # cierre contra meta
    alertas: list
    ingresos: list                    # ingresos por componente y mes
    matriz: list = field(default_factory=lambda: [dict(zip(CAMPOS_MATRIZ, m)) for m in MATRIZ])

    def json(self):
        return {"empresa": self.empresa.razonsocial, "meses": self.meses, "kpis": self.kpis, "ultimo": self.ultimo,
                "alertas": self.alertas, "ingresos": self.ingresos, "matriz": self.matriz}


def _morosidad(codempresa, fecha, tipos):
    """Cuotas facturadas con saldo al `fecha`: total por cobrar y vencido > 30 / 60 / 90 días."""
    clave = (codempresa, fecha)
    guardado = _CACHE_MORA.get(clave)
    if guardado and time.monotonic() - guardado[0] < _TTL:
        return guardado[1]
    docs = ctacte.documentos(codempresa, "CXC", ctacte.FiltroCtaCte(fecha_fin=fecha), ("documentos",), solo_pendientes=True)
    total = v30 = v60 = v90 = 0.0
    for d in ctacte.solo_saldos(docs, codempresa, fecha_pago=fecha):
        if d["codtipo"].strip().upper() not in tipos:
            continue
        saldo = float(d["diferencia"] or 0)
        dias = (fecha - d["fechavenc"]).days if d["fechavenc"] else 0
        total += saldo
        v30 += saldo if dias > 30 else 0
        v60 += saldo if dias > 60 else 0
        v90 += saldo if dias > 90 else 0
    salida = {"cuotas_por_cobrar": round(total, 2), "cartera_mora_30": round(v30, 2),
              "cartera_mora_60": round(v60, 2), "cartera_mora_90": round(v90, 2)}
    _CACHE_MORA[clave] = (time.monotonic(), salida)
    return salida


SQL_ABONOS_CAPITAL = """
SELECT YEAR(t.fecmov) * 100 + MONTH(t.fecmov) AS ym, SUM(ROUND(d.cargo, 2)) AS pagado
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra
 WHERE t.codempresa = %s AND t.actualizadocnt = 1 AND t.anulado = 0
   AND t.fecmov BETWEEN %s AND %s
   AND ({prestamos})
   AND d.cargo <> 0
   AND EXISTS (SELECT 1 FROM dettransacciones b
                WHERE b.keytra = d.keytra AND b.abono > 0 AND ({bancos}))
 GROUP BY ym
"""


def _abonos_capital(codempresa, desde, hasta, cfg):
    """{AAAAMM: capital pagado}: cargos a préstamos en pólizas que acreditan bancos."""
    prestamos, bancos = cfg["prestamos"], cfg["bancos"]
    if not prestamos or not bancos:
        return {}
    sql = SQL_ABONOS_CAPITAL.format(prestamos=" OR ".join(["d.codcuenta LIKE %s"] * len(prestamos)),
                                    bancos=" OR ".join(["b.codcuenta LIKE %s"] * len(bancos)))
    filas = consulta(sql, (codempresa, desde, hasta, *[p + "%" for p in prestamos], *[b + "%" for b in bancos]))
    return {int(f["ym"]): float(f["pagado"] or 0) for f in filas}


def _meses(anio, mes, n):
    salida = [(anio, mes)]
    for _ in range(n - 1):
        salida.insert(0, mes_anterior(*salida[0]))
    return salida


def calcula(codempresa, anio, mes):
    clave = (codempresa, anio, mes)
    guardado = _CACHE.get(clave)
    if guardado and time.monotonic() - guardado[0] < _TTL:
        return guardado[1]
    tablero = _calcula(codempresa, anio, mes)
    _CACHE[clave] = (time.monotonic(), tablero)
    return tablero


def _calcula(codempresa, anio, mes):
    emp = ctx.empresa(codempresa)
    cfg = CUENTAS.get(codempresa, CUENTAS["default"])
    periodos = {}
    for m in _meses(anio, mes, MOSTRADOS + VENTANA):        # sin periodo fiscal: el historial empieza después
        try:
            periodos[m] = ctx.periodo(codempresa, *m)
        except ErrorReporte:
            periodos.clear()
    if (anio, mes) not in periodos:
        raise ErrorReporte(f"No hay periodo fiscal definido para {mes:02d}/{anio}.")
    meses = sorted(periodos)
    nom = ctx.defnomencla(periodos[(anio, mes)].codnomencla)
    movs = Movimientos(codempresa, min(p.fecini for p in periodos.values()), fin_de_mes(anio, mes), None, nom.logn)
    if not movs:
        raise ErrorReporte("No hay movimientos contables para el periodo.")
    ultimas = {c.codigo for c in ctx.nomenclatura(codempresa, *{p.codperiodo for p in periodos.values()}) if c.ultimo}
    detalle = [c for c in movs.datos if c in ultimas]           # saldos: solo cuentas de último nivel
    todas = list(movs.datos)

    def suma(clave, desde_ym, hasta_ym, deudora, excluye="", cuentas=None):
        pref = tuple(cfg[clave])
        excl = tuple(cfg.get(excluye, ())) if excluye else ()
        if not pref:
            return 0.0
        return round(sum(movs.neto(c, desde_ym, hasta_ym, deudora) for c in (detalle if cuentas is None else cuentas)
                         if c.startswith(pref) and not (excl and c.startswith(excl))), 2)

    pagos = _abonos_capital(codempresa, date(*meses[0], 1), fin_de_mes(anio, mes), cfg)
    filas = []
    for a, m in meses:
        ini, corte = ym(periodos[(a, m)].fecini), a * 100 + m

        def del_mes(clave, deudora, excluye=""):
            return suma(clave, corte, corte, deudora, excluye, todas)

        resultado = utilidad(movs, nom, corte, corte)
        ia, cfin = del_mes("ing_arrendamiento", False), del_mes("costo_financiamiento", True)
        servicios, venta = del_mes("ing_servicios", False), del_mes("ing_venta_bienes", False)
        ingresos = movs.neto_grupo(nom.prefijos("ingresos", "otrosingresos"), nom.logn, corte, corte, False)
        deprec = del_mes("depreciaciones", True)
        abonos = pagos.get(corte, 0.0)
        filas.append({
            "mes": f"{a}-{m:02d}",
            "activo_total": suma("activo", ini, corte, True),
            "activo_circulante": suma("activo_circulante", ini, corte, True),
            "inventarios": suma("inventarios", ini, corte, True),
            "gastos_anticipados": suma("gastos_anticipados", ini, corte, True),
            "pasivo_circulante": suma("pasivo_circulante", ini, corte, False),
            "pasivo_total": suma("pasivo", ini, corte, False),
            "patrimonio": round(suma("patrimonio", ini, corte, False) + utilidad(movs, nom, ini, corte), 2),
            "cartera_neta": round(suma("cartera", ini, corte, True, "cartera_excluye")
                                  - suma("por_devengar", ini, corte, False), 2),
            "prestamos": suma("prestamos", ini, corte, False),
            "provisiones_acumuladas": round(-suma("provisiones", ini, corte, True), 2),
            "cuotas_por_cobrar": None, "cartera_mora_30": None, "cartera_mora_60": None, "cartera_mora_90": None,
            "ingresos_arrendamiento": ia, "ingresos_servicios": servicios, "venta_bienes": venta,
            "otros_ingresos": round(ingresos - ia - servicios - venta, 2), "ingresos_totales": ingresos,
            "costo_financiamiento": cfin,
            "gastos_operativos": del_mes("gastos_operativos", True, "gastos_excluye"),
            "depreciaciones": deprec,
            "utilidad_neta": resultado,
            "uaii": round(resultado + cfin, 2),
            "flujo_operativo_disponible": round(resultado + cfin + deprec, 2),
            "abonos_capital": abonos,
            "servicio_deuda": round(cfin + abonos, 2),
        })
    for f in filas[-MOSTRADOS:]:
        f.update(_morosidad(codempresa, fin_de_mes(int(f["mes"][:4]), int(f["mes"][5:])), cfg["tipos_mora"]))

    n = len(filas)
    kpis = [_indicadores(filas, i) for i in range(max(0, n - MOSTRADOS), n)]
    if len(kpis) < 4:
        raise ErrorReporte("Hacen falta al menos 4 meses con periodo fiscal para el tablero.")
    return Tablero(emp, [k["mes"] for k in kpis], filas, kpis, _contra_meta(kpis), _alertas(kpis),
                   _ingresos(filas[-MOSTRADOS:]))


def _indicadores(filas, i):
    """Las fórmulas de MATRIZ: flujos de los últimos 12 meses sobre el promedio de 13 cierres. Sin
    historia completa (los primeros meses con periodo fiscal) esos indicadores quedan sin dato."""
    b = filas[i]
    completo = i >= VENTANA

    def flujo(c):
        return sum(filas[j][c] for j in range(i - VENTANA + 1, i + 1)) if completo else None

    def promedio(c):
        return sum(filas[j][c] for j in range(i - VENTANA, i + 1)) / (VENTANA + 1) if completo else None

    def div(a, d, k=1.0):
        return None if a is None or not d else a / d * k

    ia, cfin = flujo("ingresos_arrendamiento"), flujo("costo_financiamiento")
    margen_fin = None if ia is None else ia - cfin
    tasa_activa = div(ia, promedio("cartera_neta"), 100)
    costo_fondos = div(cfin, promedio("prestamos"), 100)
    return {
        "mes": b["mes"],
        "roe": div(flujo("utilidad_neta"), promedio("patrimonio"), 100),
        "roa": div(flujo("utilidad_neta"), promedio("activo_total"), 100),
        "tasa_activa": tasa_activa, "costo_fondos": costo_fondos,
        "spread": None if tasa_activa is None or costo_fondos is None else tasa_activa - costo_fondos,
        "nim": div(margen_fin, promedio("cartera_neta"), 100),
        "margen_neto": div(flujo("utilidad_neta"), flujo("ingresos_totales"), 100),
        "npl_30": div(b["cartera_mora_30"], b["cartera_neta"], 100),
        "npl_60": div(b["cartera_mora_60"], b["cartera_neta"], 100),
        "npl_90": div(b["cartera_mora_90"], b["cartera_neta"], 100),
        "cobertura": div(b["provisiones_acumuladas"], b["cartera_mora_90"], 100),
        "dso": div(b["cuotas_por_cobrar"], None if ia is None else ia + flujo("ingresos_servicios"), 365),
        "razon_corriente": div(b["activo_circulante"], b["pasivo_circulante"]),
        "prueba_acida": div(b["activo_circulante"] - b["inventarios"] - b["gastos_anticipados"], b["pasivo_circulante"]),
        "deuda_patrimonio": div(b["pasivo_total"], b["patrimonio"]),
        "endeudamiento": div(b["pasivo_total"], b["activo_total"], 100),
        "dscr": div(flujo("flujo_operativo_disponible"), flujo("servicio_deuda")),
        "tie": div(flujo("uaii"), cfin),
        "eficiencia": div(flujo("gastos_operativos"), margen_fin, 100),
        "part_niif16": div(ia, flujo("ingresos_totales"), 100),
        "rotacion_activos": div(flujo("ingresos_totales"), promedio("activo_total")),
    }


def estado(m, valor):
    if valor is None:
        return "Sin dato"
    if m["direccion"] == "rango":
        lo, hi = m["min"], m["max"]
        tol = (hi - lo) * 0.25
        return "En meta" if lo <= valor <= hi else "Vigilar" if lo - tol <= valor <= hi + tol else "Fuera de rango"
    if m["direccion"] == "mayor":
        return "En meta" if valor >= m["bueno"] else "Vigilar" if valor >= m["limite"] else "Fuera de rango"
    return "En meta" if valor <= m["bueno"] else "Vigilar" if valor <= m["limite"] else "Fuera de rango"


def _contra_meta(kpis):
    ult, ant = kpis[-1], kpis[-2]
    salida = []
    for fila in MATRIZ:
        m = dict(zip(CAMPOS_MATRIZ, fila))
        valor, anterior = ult.get(m["id"]), ant.get(m["id"])
        salida.append({"id": m["id"], "modulo": m["modulo"], "indicador": m["indicador"], "mes": ult["mes"],
                       "valor": valor, "anterior": anterior,
                       "cambio": None if valor is None or anterior is None else valor - anterior,
                       "meta": m["meta"], "unidad": m["unidad"], "direccion": m["direccion"],
                       "estado": "No aplica" if m["id"] == "desv_vr" else estado(m, valor)})
    return salida


def _alertas(kpis):
    """Reglas al CFO con el último cierre y el de hace 3 meses."""
    u, h = kpis[-1], kpis[-4]
    n = lambda x: x if x is not None else 0.0
    nivel = lambda a, b, critica: "Crítica" if critica else "Advertencia" if a or b else "Sin alerta"
    npl_alto, npl_sube = n(u["npl_90"]) > 3, n(u["npl_90"]) - n(h["npl_90"]) >= 0.5
    sp_bajo = u["spread"] is not None and u["spread"] < 5
    sp_cae = u["spread"] is not None and h["spread"] is not None and h["spread"] - u["spread"] >= 0.75
    cob, dscr = u["cobertura"], u["dscr"]
    cob_baja, dscr_bajo = cob is not None and cob < 100, dscr is not None and dscr < 1.4
    critica3 = (dscr is not None and dscr < 1.25) or (cob is not None and cob < 90)
    f2 = lambda x, suf="": "—" if x is None else f"{x:,.2f}{suf}"
    return [
        {"regla": "Deterioro de cartera", "nivel": nivel(npl_alto, npl_sube, npl_alto and npl_sube),
         "motivo": f"Morosidad 90+ {f2(u['npl_90'], ' %')} (hace 3 meses {f2(h['npl_90'], ' %')})"},
        {"regla": "Descalce de tasa", "nivel": nivel(sp_bajo, sp_cae, sp_bajo and sp_cae),
         "motivo": f"Spread {f2(u['spread'], ' p.p.')} (hace 3 meses {f2(h['spread'], ' p.p.')}); "
                   f"costo de fondos {f2(u['costo_fondos'], ' %')}"},
        {"regla": "Cobertura y servicio de deuda", "nivel": nivel(cob_baja, dscr_bajo, critica3),
         "motivo": f"Cobertura {'sin cuotas 90+' if cob is None else f'{cob:,.0f} %'}, DSCR {f2(dscr, 'x')}"},
    ]


def _ingresos(filas):
    comps = (("Arrendamiento financiero", "NIIF 16", "ingresos_arrendamiento"),
             ("Servicios y factoraje", "NIIF 15", "ingresos_servicios"),
             ("Venta de bienes", "NIIF 15", "venta_bienes"),
             ("Otros e ingresos financieros", "Otros", "otros_ingresos"))
    return [{"mes": f["mes"], "componente": c, "norma": n, "monto": f[k]} for f in filas for c, n, k in comps]


def cierre_por_defecto():
    """Último mes cerrado: el anterior al actual."""
    hoy = date.today()
    return mes_anterior(hoy.year, hoy.month)
