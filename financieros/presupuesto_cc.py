"""
Ejecución presupuestaria por centro de costo (menú 2270 de vfmenuc).

  con_presupuesto_cc_xls       Libro: hoja RESUMEN + una hoja por centro (o una hoja combinada)
  con_presupuesto_cc_cons_xls  Una hoja con los centros seleccionados consolidados

Hoja = secciones Ingresos / Egresos / Inversión con presupuesto, real y diferencia
del mes y acumulados, como la plantilla "Ejecucion Presupuestaria Detalle". Las
cuentas de Inversión se toman de esa plantilla (son fijas por cliente).

Respecto a VFP: una sola consulta de movimientos y una de presupuesto para todo el
libro (VFP hacía 2 consultas por cuenta de inversión y por centro), se excluyen
pólizas anuladas, y el real de Inversión se calcula (en con_presupuesto_cc_xls VFP
comparaba contra _detalle.cuenta ya en EOF y siempre daba 0).
"""
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from . import contexto as ctx
from .contexto import ErrorReporte, meses_entre
from .excel import nombre_hoja
from .modelo import Columna, Fila, Libro
from .motor import ACREEDORAS_ER, DEUDORAS_ER, prefijos_orden, ORDEN_ER
from .reportes import Base
from .saldos import FiltroMovs, Movimientos

log = logging.getLogger(__name__)

HOJA_RESUMEN = "RESUMEN"
MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE",
         "OCTUBRE", "NOVIEMBRE", "DICIEMBRE")


@lru_cache(maxsize=1)
def cuentas_inversion():
    """[(cuenta, nombre)] de la sección 'Inversión' de la plantilla VFP."""
    plantilla = Path(settings.VFP_PLANTILLAS_DIR) / "Ejecucion Presupuestaria Detalle.xls"
    try:
        import xlrd
        hoja = xlrd.open_workbook(str(plantilla)).sheet_by_name("PRESUPUESTO")
        cuentas, dentro = [], False
        for r in range(hoja.nrows):
            a, b = str(hoja.cell_value(r, 0)).strip(), str(hoja.cell_value(r, 1)).strip()
            if a.lower().startswith("inversi"):
                dentro = True
            elif dentro and a:
                cuentas.append((a.split(".")[0] if re.fullmatch(r"\d+\.0", a) else a, b))
            elif dentro and b.lower().startswith("total"):
                break
        return tuple(cuentas)
    except Exception as e:   # sin plantilla: respaldo desde .env ("cuenta:nombre;cuenta:nombre")
        log.warning("No se pudo leer %s (%s); se usa PRESUPUESTO_CUENTAS_INVERSION", plantilla, e)
        cuentas = []
        for par in settings.PRESUPUESTO_CUENTAS_INVERSION.split(";"):
            if par.strip():
                cuenta, _, nombre = par.partition(":")
                cuentas.append((cuenta.strip(), nombre.strip()))
        return tuple(cuentas)


class _Presupuesto(Base):
    """Carga una vez movimientos y presupuesto por (cuenta, centro); luego arma hojas por conjunto de centros."""

    def __init__(self, p):
        super().__init__(p)
        c = self.corte
        self.inversion = cuentas_inversion()
        prefijos = prefijos_orden(ORDEN_ER, self.nom) | {cta[: self.logn] for cta, _ in self.inversion}
        self.movs = Movimientos(self.emp.codempresa, c.periodo.fecini, c.fecha, prefijos, self.logn,
                                FiltroMovs(por_cc=True, centros=list(p.centros), dim=p.dim))
        self.pres = ctx.presupuesto_por_centro(self.emp.codempresa, c.periodo.codperiodo, p.centros, p.dim)
        self.n_mes = len(meses_entre(c.periodo.fecini, c.fecha))      # xmes de VFP
        self.deudoras = set(self.nom.prefijos(*DEUDORAS_ER, "activo"))
        self.nomencla = {cta.codigo: cta for cta in self.nomenclatura()}

    # --- valores ---------------------------------------------------------------------------
    def _real(self, cuenta, desde, hasta, centros):
        deud = cuenta[: self.logn] in self.deudoras
        if centros is None:
            return self.movs.neto(cuenta, desde, hasta, deud)
        return round(sum(self.movs.neto(cuenta, desde, hasta, deud, cc) for cc in centros), 2)

    def _presupuesto(self, cuenta, centros):
        mensual = [0.0] * 12
        for (cta, cc), valores in self.pres.items():
            if cta == cuenta and (centros is None or cc in centros):
                mensual = [a + b for a, b in zip(mensual, valores)]
        return round(mensual[self.n_mes - 1], 2), round(sum(mensual[: self.n_mes]), 2)

    def _linea(self, cuenta, centros):
        c = self.corte
        presm, presa = self._presupuesto(cuenta, centros)
        return presm, self._real(cuenta, c.fin, c.fin, centros), presa, self._real(cuenta, c.ini, c.fin, centros)

    def secciones(self, centros):
        """(ingresos, egresos, inversion): [(cuenta, nombre, presm, realm, presa, reala)]."""
        candidatas = {cta for cta in self.movs.datos} | {cta for cta, _ in self.pres}
        ingresos, egresos = [], []
        for cuenta in sorted(candidatas):
            cta = self.nomencla.get(cuenta)
            if not cta or not cta.ultimo:
                continue
            v = self._linea(cuenta, centros)
            if not any(v):
                continue
            if self.nom.grupo(cuenta, *ACREEDORAS_ER):
                ingresos.append((cuenta, cta.nombre, *v))
            elif self.nom.grupo(cuenta, *DEUDORAS_ER):
                egresos.append((cuenta, cta.nombre, *v))
        inversion = [(cta, nombre, *self._linea(cta, centros)) for cta, nombre in self.inversion]
        return ingresos, egresos, inversion

    # --- armado de hojas -------------------------------------------------------------------
    def columnas(self):
        cols = [Columna("PRESUPUESTO"), Columna("REAL"), Columna("DIFERENCIA"),
                Columna("PRESUPUESTO"), Columna("REAL"), Columna("DIFERENCIA")]
        return cols, [(0, 2, "MES"), (3, 5, "ACUMULADO")]

    def hoja(self, titulo, subtitulo, centros, nombre_hoja_="", vinculo_regreso=""):
        ingresos, egresos, inversion = self.secciones(centros)
        filas, totales = [], {}

        def seccion(nombre, lineas, real_menos_pres):
            filas.append(Fila(tipo="grupo", nombre=nombre))
            bloque = []
            for cuenta, nom, pm, rm, pa, ra in lineas:
                dif_m, dif_a = (rm - pm, ra - pa) if real_menos_pres else (pm - rm, pa - ra)
                f = Fila(tipo="cuenta", codigo=cuenta, cuenta=self.nom.formatea(cuenta), nombre=nom, nivel=1,
                         valores=[pm, rm, round(dif_m, 2), pa, ra, round(dif_a, 2)],
                         formulas={2: ("resta", 1, 0), 5: ("resta", 4, 3)} if real_menos_pres
                         else {2: ("resta", 0, 1), 5: ("resta", 3, 4)})
                bloque.append(f)
            filas.extend(bloque)
            total = Fila(tipo="total", nombre=f"Total {nombre}", negrita=True,
                         rango=(bloque[0], bloque[-1]) if bloque else None,
                         valores=[round(sum(b.valores[k] for b in bloque), 2) for k in range(6)])
            filas.append(total)
            return total

        totales["ing"] = seccion("Ingresos", ingresos, True)
        filas.append(Fila())
        totales["egr"] = seccion("Egresos", egresos, False)
        filas.append(Fila())
        totales["inv"] = seccion("Inversión", inversion, False)

        egr, inv, ing = totales["egr"], totales["inv"], totales["ing"]
        tei = Fila(tipo="total", nombre="Total Egresos + Inversión", negrita=True, refs=[(1, egr), (1, inv)],
                   valores=[round(a + b, 2) for a, b in zip(egr.valores, inv.valores)])
        # Diferencia: presupuesto y real = Ingresos - (Egresos + Inversión); la columna
        # diferencia ya es "favorable" en ambas secciones, por eso se suma (plantilla VFP).
        dif = Fila(tipo="total", nombre="Diferencia Ingresos - Egresos", negrita=True, refs=[(1, ing), (-1, tei)],
                   valores=[round(ing.valores[k] + (1 if k in (2, 5) else -1) * tei.valores[k], 2) for k in range(6)],
                   formulas={k: ("refs", [(1, ing, k), (1, tei, k)]) for k in (2, 5)})
        filas += [tei, dif]
        totales["tei"] = tei

        cols, grupos = self.columnas()
        rep = self.reporte(titulo, subtitulo, cols, filas)
        rep.hoja = nombre_hoja_ or rep.hoja
        rep.grupos_columnas = grupos
        rep.columnas_ocultas = set() if self.p.acumulado else {3, 4, 5}
        rep.vinculo_regreso = vinculo_regreso
        rep.fecha_hora = None
        return rep, totales, bool(ingresos or egresos or any(any(x[2:]) for x in inversion))

    def subtitulo(self):
        c = self.corte
        if self.p.acumulado:
            return f"AL {c.fecha.strftime('%d/%m/%Y')}"
        return f"DEL MES [{c.fecha.month:02d}/{c.fecha.year}]"

    def resumen(self, centros_hojas):
        """Hoja RESUMEN: una fila por centro con referencias a su hoja, totales e ISR."""
        cols = []
        for periodo in ("MES", "ACUMULADO"):
            for _ in ("PRESUPUESTADO", "EJECUTADO", "DIFERENCIA"):
                cols += [Columna("Ingresos", ancho=14), Columna("Egresos", ancho=14), Columna("Resultado", ancho=14)]
        mes_ini, mes_fin = MESES[self.corte.periodo.fecini.month - 1], MESES[self.corte.fecha.month - 1]
        grupos = []
        for base, etiqueta in ((0, mes_fin), (9, f"{mes_ini} - {mes_fin}")):
            for k, texto in enumerate(("PRESUPUESTADO", "EJECUTADO", "DIFERENCIA")):
                grupos.append((base + 3 * k, base + 3 * k + 2, f"{texto} · {etiqueta}"))

        filas = []
        for cc, nombre, hoja, tot in centros_hojas:
            ing, tei = tot["ing"], tot["tei"]
            v, fm = [None] * 18, {}
            for base, col_p, col_r in ((0, 0, 1), (9, 3, 4)):      # mes: C/D de la hoja, acumulado: F/G
                v[base + 0], fm[base + 0] = ing.valores[col_p], ("refs", [(1, ing, col_p)])
                v[base + 1], fm[base + 1] = tei.valores[col_p], ("refs", [(1, tei, col_p)])
                v[base + 3], fm[base + 3] = ing.valores[col_r], ("refs", [(1, ing, col_r)])
                v[base + 4], fm[base + 4] = tei.valores[col_r], ("refs", [(1, tei, col_r)])
                v[base + 2], fm[base + 2] = round(v[base] - v[base + 1], 2), ("resta", base, base + 1)
                v[base + 5], fm[base + 5] = round(v[base + 3] - v[base + 4], 2), ("resta", base + 3, base + 4)
                v[base + 6], fm[base + 6] = round(v[base + 3] - v[base], 2), ("resta", base + 3, base)
                v[base + 7], fm[base + 7] = round(v[base + 1] - v[base + 4], 2), ("resta", base + 1, base + 4)
                v[base + 8], fm[base + 8] = round(v[base + 2] - v[base + 5], 2), ("resta", base + 2, base + 5)
            filas.append(Fila(tipo="cuenta", codigo=cc, cuenta=cc, nombre=nombre, nivel=1, valores=v,
                              formulas=fm, vinculo=hoja))

        total = Fila(tipo="total", nombre="TOTALES ANTES DEL IMPUESTO", negrita=True,
                     rango=(filas[0], filas[-1]) if filas else None,
                     valores=[round(sum(f.valores[k] for f in filas), 2) for k in range(18)])
        isr_pct = ctx.isr_empresa(self.emp.codempresa)
        factor = isr_pct / 100
        isr = Fila(tipo="total", nombre=f"ISR {isr_pct:.2f}%   (IMPUESTO)", valores=[None] * 18)
        for k in (1, 4, 7, 10, 13, 16):                     # C, F, I, L, O, R = resultado * tasa
            isr.valores[k] = round(total.valores[k + 1] * factor, 2)
            isr.formulas[k] = ("factor", total, k + 1, factor)
        despues = Fila(tipo="total", nombre="RESULTADO DESPUES DEL IMPUESTO", negrita=True, valores=[None] * 18)
        for k in range(18):
            if k % 3 == 2:                                  # columna Resultado = Ingresos - Egresos
                despues.valores[k] = round(despues.valores[k - 2] - despues.valores[k - 1], 2)
                despues.formulas[k] = ("resta", k - 2, k - 1)
            else:
                despues.valores[k] = round(total.valores[k] + (isr.valores[k] or 0), 2)
                despues.formulas[k] = ("refs", [(1, total, k), (1, isr, k)])
        filas += [Fila(), total, Fila(), isr, Fila(), despues]

        rep = self.reporte("CONSOLIDADO EJECUCION PRESUPUESTARIA", self.subtitulo(), cols, filas)
        rep.hoja = HOJA_RESUMEN
        rep.grupos_columnas = grupos
        rep.columnas_ocultas = set() if self.p.acumulado else set(range(9, 18))
        rep.fecha_hora = None
        return rep


def _nombre_unico(base, usados):
    nombre, n = nombre_hoja(base), 2
    while nombre.lower() in usados:
        nombre = nombre_hoja(f"{base[:27]}_{n}")
        n += 1
    usados.add(nombre.lower())
    return nombre


def presupuesto_por_centro(p):
    """con_presupuesto_cc_xls."""
    b = _Presupuesto(p)
    titulo = (p.nombre or "EJECUCION PRESUPUESTARIA POR CENTRO DE COSTO").strip()
    archivo = re.sub(r"[^\w\- ]", "", f"{titulo} {b.emp.codempresa} {p.anio}-{p.mes:02d}").strip() + ".xlsx"
    catalogo = ctx.centros(b.emp.codempresa, b.corte.periodo.codperiodo, p.dim)

    if not p.detallar:
        centros = set(p.centros) if p.centros else None
        sub = b.subtitulo() + (f" · Centro(s): {', '.join(p.centros)}" if p.centros else "")
        hoja, _, con_datos = b.hoja(titulo, sub, centros, "PRESUPUESTO")
        if not con_datos:
            raise ErrorReporte("No existen datos para los parámetros indicados.")
        return Libro(titulo, [hoja], archivo)

    # Un centro por hoja: los seleccionados, o todos los de último nivel del periodo (como VFP).
    seleccion = list(p.centros) or [c.codigo for c in catalogo.values() if c.ultimo]
    usados, hojas, centros_hojas = {HOJA_RESUMEN.lower()}, [], []
    for cc in seleccion:
        nombre = catalogo[cc].nombre if cc in catalogo else cc
        hoja_nombre = _nombre_unico(f"PRESUP_{cc}", usados)
        hoja, totales, con_datos = b.hoja(titulo, f"{b.subtitulo()} · {cc} - {nombre}", {cc}, hoja_nombre,
                                          HOJA_RESUMEN)
        if not con_datos:      # VFP creaba hoja para todos los centros; se omiten los vacíos
            usados.discard(hoja_nombre.lower())
            continue
        hojas.append(hoja)
        centros_hojas.append((cc, nombre, hoja_nombre, totales))
    if not hojas:
        raise ErrorReporte("No existen datos de presupuesto ni movimientos para los centros indicados.")
    return Libro(titulo, [b.resumen(centros_hojas)] + hojas, archivo)


def presupuesto_consolidado(p):
    """con_presupuesto_cc_cons_xls: una hoja con los centros seleccionados sumados."""
    b = _Presupuesto(p)
    centros = set(p.centros) if p.centros else None
    catalogo = ctx.centros(b.emp.codempresa, b.corte.periodo.codperiodo, p.dim)
    if not p.centros:
        alcance = "TODOS LOS CENTROS DE COSTO"
    elif len(p.centros) == 1:
        alcance = f"MOVIMIENTO DEL CENTRO DE COSTO {catalogo.get(p.centros[0], p.centros[0]).nombre if p.centros[0] in catalogo else p.centros[0]}"
    else:
        alcance = "MOVIMIENTO DE LOS CENTROS DE COSTO: " + ", ".join(p.centros)
    titulo = (p.nombre or "CONSOLIDADO DE EJECUCION PRESUPUESTARIA POR CENTRO DE COSTO").strip()
    hoja, _, con_datos = b.hoja(titulo, f"{alcance} · {b.subtitulo()}", centros, "PRESUPUESTO")
    if not con_datos:
        raise ErrorReporte("No existen datos para los parámetros indicados.")
    hoja.fecha_hora = None
    archivo = re.sub(r"[^\w\- ]", "", f"{titulo} {b.emp.codempresa} {p.anio}-{p.mes:02d}").strip() + ".xlsx"
    return Libro(titulo, [hoja], archivo)
