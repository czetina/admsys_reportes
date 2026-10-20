"""
Movimientos contables por cuenta, centro de costo y mes.

Reemplaza a la vista v_saldos_cuenta. La vista agrupa TODAS las empresas y meses y
calcula yearmes con CONCAT(YEAR(fecmov), ...), por lo que el filtro por empresa y
mes no puede usar índices. Aquí se filtra por t.codempresa y por rango de t.fecmov,
lo que aprovecha los índices `v_saldos_cuenta` que ya existen en transacciones y
dettransacciones. Mismos filtros que la vista: actualizadocnt = 1, anulado = 0,
codcuenta <> '' y la cuenta debe existir en la nomenclatura de su periodo.
"""
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from core.db import consulta

from .contexto import meses_entre

log = logging.getLogger(__name__)

SQL_MOVIMIENTOS = """
SELECT d.codcuenta AS cuenta,
       {cc} AS cc,
       YEAR(t.fecmov) * 100 + MONTH(t.fecmov) AS ym,
       SUM(ROUND(d.cargo, 2)) AS cargo,
       SUM(ROUND(d.abono, 2)) AS abono
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra
  JOIN empnomenclatura n
    ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
 WHERE t.codempresa = %s
   AND t.actualizadocnt = 1
   AND t.anulado = 0
   AND t.fecmov BETWEEN %s AND %s
   AND d.codcuenta <> ''
   {prefijos}
   {filtros}
 GROUP BY d.codcuenta, {cc_group} ym
"""


@dataclass
class FiltroMovs:
    """Filtros opcionales de centro de costo y cuenta (formas por C.C. de VFP)."""
    por_cc: bool = False
    separa_apertura: bool = False   # cc = 'AP' para pólizas de apertura (codtipo AP/PA), '' el resto
    centros: list = field(default_factory=list)   # codcc exactos (lista de selección)
    cc_desde: str = ""
    cc_hasta: str = ""
    cuenta_desde: str = ""
    cuenta_hasta: str = ""
    dim: str = "cc"                 # dimensión del centro: cc, ci o ca (menús 22, 24, 26)

    @property
    def columna(self):
        return {"cc": "d.codcc", "ci": "d.codci", "ca": "d.codca"}[self.dim]

    def sql(self):
        partes, params = [], []
        if self.centros:
            partes.append(f"AND {self.columna} IN ({','.join(['%s'] * len(self.centros))})")
            params += list(self.centros)
        for valor, op, campo in ((self.cc_desde, ">=", self.columna), (self.cc_hasta, "<=", self.columna),
                                 (self.cuenta_desde, ">=", "d.codcuenta"),
                                 (self.cuenta_hasta, "<=", "d.codcuenta")):
            if valor:
                partes.append(f"AND {campo} {op} %s")
                params.append(valor)
        return "\n   ".join(partes), params


class Movimientos:
    """Cargos y abonos por (cuenta, centro, AAAAMM) de una empresa en un rango de fechas."""

    def __init__(self, codempresa, desde, hasta, prefijos, logn, filtro=None):
        """`prefijos` = grupos de cuentas a leer (LEFT(cuenta, logn)); None = todas las cuentas."""
        # datos[cuenta][cc][ym] = (cargo, abono); cc = "" cuando no se separa por centro
        self.datos = defaultdict(lambda: defaultdict(dict))
        params_pref = []
        if prefijos is None:
            sql_pref = ""
        else:
            prefijos = sorted(set(p for p in prefijos if p))
            if not prefijos:
                return
            sql_pref = f"AND LEFT(d.codcuenta, %s) IN ({','.join(['%s'] * len(prefijos))})"
            params_pref = [logn, *prefijos]
        filtro = filtro or FiltroMovs()
        filtros_sql, filtros_params = filtro.sql()
        if filtro.por_cc:
            cc_expr, cc_group = filtro.columna, filtro.columna + ","
        elif filtro.separa_apertura:
            cc_expr = "IF(t.codtipo IN ('AP', 'PA'), 'AP', '')"
            cc_group = cc_expr + ","
        else:
            cc_expr, cc_group = "''", ""
        sql = SQL_MOVIMIENTOS.format(prefijos=sql_pref, cc=cc_expr, cc_group=cc_group, filtros=filtros_sql)
        inicio = time.perf_counter()
        for f in consulta(sql, (codempresa, desde, hasta, *params_pref, *filtros_params)):
            self.datos[f["cuenta"]][f["cc"] or ""][int(f["ym"])] = (float(f["cargo"] or 0), float(f["abono"] or 0))
        log.info("movimientos %s %s..%s: %d cuentas en %.3fs", codempresa, desde, hasta,
                 len(self.datos), time.perf_counter() - inicio)

    def __bool__(self):
        return bool(self.datos)

    def cuentas(self, desde_ym, hasta_ym):
        """Cuentas con al menos un movimiento en el rango (el _detalle de VFP)."""
        return {c for c, por_cc in self.datos.items()
                if any(desde_ym <= m <= hasta_ym for meses in por_cc.values() for m in meses)}

    def centros(self, desde_ym, hasta_ym):
        return {cc for por_cc in self.datos.values() for cc, meses in por_cc.items()
                if any(desde_ym <= m <= hasta_ym for m in meses)}

    def tiene(self, cuenta, desde_ym, hasta_ym, cc=None):
        """¿La cuenta (y centro) tiene algún movimiento en el rango?"""
        por_cc = self.datos.get(cuenta, {})
        grupos = [por_cc.get(cc, {})] if cc is not None else por_cc.values()
        return any(desde_ym <= m <= hasta_ym for meses in grupos for m in meses)

    def sumas(self, cuenta, desde_ym, hasta_ym, cc=None):
        cargo = abono = 0.0
        for centro, meses in self.datos.get(cuenta, {}).items():
            if cc is not None and centro != cc:
                continue
            for m, (c, a) in meses.items():
                if desde_ym <= m <= hasta_ym:
                    cargo += c
                    abono += a
        return round(cargo, 2), round(abono, 2)

    def neto(self, cuenta, desde_ym, hasta_ym, deudora, cc=None):
        cargo, abono = self.sumas(cuenta, desde_ym, hasta_ym, cc)
        return round(cargo - abono if deudora else abono - cargo, 2)

    def neto_grupo(self, prefijos, logn, desde_ym, hasta_ym, deudora):
        return round(sum(
            self.neto(c, desde_ym, hasta_ym, deudora) for c in self.datos if c[:logn] in prefijos
        ), 2)

    def agrupa_centros(self, mapa):
        """Reasigna centros según `mapa` (centro -> centro mayor), sumando sus movimientos."""
        for cuenta, por_cc in self.datos.items():
            nuevo = defaultdict(dict)
            for cc, meses in por_cc.items():
                destino = nuevo[mapa.get(cc, cc)]
                for m, (c, a) in meses.items():
                    c0, a0 = destino.get(m, (0.0, 0.0))
                    destino[m] = (c0 + c, a0 + a)
            self.datos[cuenta] = nuevo


def utilidad(movs, nom, desde_ym, hasta_ym):
    """
    Resultado del ejercicio (ingresos - egresos), como en los BG de VFP:
    (abono - cargo) de ingresos/otros ingresos menos (cargo - abono) de
    costos/gastos/egresos/otros egresos.
    """
    ingresos = movs.neto_grupo(nom.prefijos("ingresos", "otrosingresos"), nom.logn, desde_ym, hasta_ym, False)
    egresos = movs.neto_grupo(nom.prefijos("costos", "gastos", "egresos", "otrosegresos"),
                              nom.logn, desde_ym, hasta_ym, True)
    return round(ingresos - egresos, 2)


def rango_ym(periodo, hasta):
    """(primer AAAAMM del ejercicio, AAAAMM de corte)."""
    meses = meses_entre(periodo.fecini, hasta)
    return meses[0], meses[-1]
