"""
Datos de contexto que en VFP vivían en cursores globales:

  _empresas        -> Empresa
  _validperiodos   -> Periodo      (empperiodos que contiene el año-mes)
  _cia             -> DefNomencla  (comdefnomenclatura del periodo)
  _empnomenclatura -> lista de Cuenta
"""
import calendar
import threading
import time
from dataclasses import dataclass, field
from datetime import date

from core.db import consulta, consulta_uno


CACHE_SEGUNDOS = 300
_cache, _cache_lock = {}, threading.Lock()


def _fila_cacheada(sql, params):
    """
    consulta_uno con memoria de 5 minutos para datos que casi no cambian (empresa, periodo,
    definición de nomenclatura): contra un servidor remoto cada consulta es una ida y vuelta.
    """
    clave = (sql, params)
    ahora = time.monotonic()
    with _cache_lock:
        guardada = _cache.get(clave)
    if guardada and ahora - guardada[0] < CACHE_SEGUNDOS:
        return dict(guardada[1]) if guardada[1] is not None else None
    fila = consulta_uno(sql, params)
    with _cache_lock:
        _cache[clave] = (ahora, fila)
    return dict(fila) if fila is not None else None


class ErrorReporte(Exception):
    """Error de negocio que se muestra al usuario (equivale a los MESSAGEBOX)."""


def fin_de_mes(anio, mes):
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def ym(d):
    return d.year * 100 + d.month


def meses_entre(desde, hasta):
    """Lista de enteros AAAAMM de `desde` a `hasta` (fechas), inclusive."""
    out, a, m = [], desde.year, desde.month
    while a * 100 + m <= ym(hasta):
        out.append(a * 100 + m)
        m += 1
        if m > 12:
            a, m = a + 1, 1
    return out


def mes_anterior(anio, mes):
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


@dataclass
class Empresa:
    codempresa: str
    razonsocial: str
    nit: str
    moneda: str


@dataclass
class Periodo:
    codperiodo: str
    fecini: date
    fecfin: date
    codnomencla: str
    gananciaejercicio: str


@dataclass
class DefNomencla:
    """Fila de comdefnomenclatura: prefijos de cada grupo de cuentas."""
    niveles: int
    logn: int
    mascara: str
    grupos: dict = field(default_factory=dict)

    def prefijos(self, *nombres):
        return [self.grupos[n] for n in nombres if self.grupos.get(n)]

    def grupo(self, codcuenta, *nombres):
        return codcuenta[: self.logn] in self.prefijos(*nombres)

    def formatea(self, codcuenta):
        """TRANSFORM(cuenta, '@R ' + mascaranomencla), sin el relleno final."""
        if not self.mascara:
            return codcuenta
        out, i = [], 0
        for ch in self.mascara:
            if i >= len(codcuenta):
                break
            if ch in "9XN#A!":
                out.append(codcuenta[i])
                i += 1
            else:
                out.append(ch)
        return "".join(out) + codcuenta[i:]


@dataclass
class Cuenta:
    codigo: str
    nombre: str
    mayor: str
    nivel: int
    ultimo: bool


GRUPOS = (
    "activo", "pasivo", "capital", "ingresos", "egresos", "costos", "gastos",
    "otrosingresos", "otrosegresos", "regularizacionactivo", "regularizacionpasivo",
    "ordendeudoras", "ordenacreedoras",
)


def empresa(codempresa):
    f = _fila_cacheada(
        """
        SELECT e.codempresa, e.razonsocial, e.nitemp, COALESCE(m.nommoneda, '') AS nommoneda
          FROM empresas e
          LEFT JOIN commonedas m ON m.codmoneda = e.codmoneda
         WHERE e.codempresa = %s
        """,
        (codempresa,),
    )
    if f is None:
        raise ErrorReporte("No existe la empresa.")
    return Empresa(f["codempresa"], f["razonsocial"], f["nitemp"] or "", f["nommoneda"])


def periodo(codempresa, anio, mes):
    f = _fila_cacheada(
        """
        SELECT codperiodo, feciniperiodo, fecfinperiodo, codnomencla, gananciaejercicio
          FROM empperiodos
         WHERE codempresa = %s AND %s BETWEEN feciniperiodo AND fecfinperiodo
         ORDER BY periodoextraordinario, codperiodo
         LIMIT 1
        """,
        (codempresa, date(anio, mes, 1)),
    )
    if f is None:
        raise ErrorReporte(f"No hay periodo fiscal definido para {mes:02d}/{anio}.")
    return Periodo(f["codperiodo"], f["feciniperiodo"], f["fecfinperiodo"], f["codnomencla"], f["gananciaejercicio"])


def defnomencla(codnomencla):
    f = _fila_cacheada("SELECT * FROM comdefnomenclatura WHERE codnomencla = %s", (codnomencla,))
    if f is None:
        raise ErrorReporte(f"No existe la definición de nomenclatura {codnomencla}.")
    digitos = (f["digitosnivelesnomencla"] or "1").strip()
    return DefNomencla(
        niveles=int(f["nivelesnomencla"] or 1),
        logn=int(digitos[0]) if digitos[:1].isdigit() else 1,
        mascara=f["mascaranomencla"] or "",
        grupos={g: (f.get(g) or "").strip() for g in GRUPOS},
    )


@dataclass
class Centro:
    codigo: str
    nombre: str
    mayor: str
    nivel: int
    ultimo: bool
    acumulacion: str

    @property
    def etiqueta(self):
        return f"({self.codigo}) {self.nombre}" if self.codigo else "(SIN CENTRO)"


# Dimensiones de centro: costo (menú 22), integración (24) y actividad (26). Mismas columnas en
# dettransacciones / empnomenclaturadet (codcc, codci, codca) y catálogos (empcc, empci, empca).
DIMENSIONES = {
    "cc": ("codcc", "empcc", "CENTRO DE COSTO", "C.C."),
    "ci": ("codci", "empci", "CENTRO DE INTEGRACIÓN", "C.I."),
    "ca": ("codca", "empca", "CENTRO DE ACTIVIDAD", "C.A."),
}


def columna_centro(dim="cc"):
    return DIMENSIONES.get(dim, DIMENSIONES["cc"])[0]


def rotula(texto, dim="cc"):
    """Cambia «CENTRO DE COSTO» / «C.C.» de los títulos de los reportes por los de otra dimensión."""
    if not texto or dim == "cc" or dim not in DIMENSIONES:
        return texto
    _, _, nombre, sigla = DIMENSIONES[dim]
    for a, b in (("CENTROS DE COSTO", nombre.replace("CENTRO", "CENTROS")), ("CENTRO DE COSTO", nombre),
                 ("Centros de costo", nombre.capitalize().replace("Centro", "Centros")),
                 ("Centro de costo", nombre.capitalize()), ("C. COSTO", sigla), ("C.C.", sigla)):
        texto = texto.replace(a, b)
    return texto


def centros(codempresa, codperiodo=None, dim="cc"):
    """Centros (empcc / empci / empca) del periodo; sin periodo, los de todos los periodos (nombre más reciente)."""
    col, tabla = DIMENSIONES[dim][:2]
    filtro, params = ("AND codperiodo = %s", (codempresa, codperiodo)) if codperiodo else ("", (codempresa,))
    filas = consulta(
        f"""
        SELECT {col} AS codigo, descripcion, centro_mayor, centro_nivel, ultimonivel, cuentaacumulacion
          FROM {tabla}
         WHERE codempresa = %s {filtro}
         ORDER BY codperiodo
        """,
        params,
    )
    out = {}
    for f in filas:   # ORDER BY codperiodo: el periodo más reciente sobrescribe
        out[f["codigo"]] = Centro(f["codigo"], f["descripcion"] or "", f["centro_mayor"] or "",
                                 int(f["centro_nivel"] or 0), f["ultimonivel"] == 1, f["cuentaacumulacion"] or "")
    return dict(sorted(out.items()))


def presupuesto(codempresa, codperiodo, centros_sel=None, dim="cc"):
    """Presupuesto mensual por cuenta (empnomenclaturadet.pre01..pre12), sumando los centros indicados."""
    filtro, params = "", [codempresa, codperiodo]
    if centros_sel:
        filtro = f"AND {columna_centro(dim)} IN ({','.join(['%s'] * len(centros_sel))})"
        params += list(centros_sel)
    meses = ", ".join(f"SUM(pre{m:02d}) AS pre{m:02d}" for m in range(1, 13))
    filas = consulta(
        f"""
        SELECT codcuenta, {meses}
          FROM empnomenclaturadet
         WHERE codempresa = %s AND codperiodo = %s {filtro}
         GROUP BY codcuenta
        """,
        params,
    )
    return {f["codcuenta"]: [float(f[f"pre{m:02d}"] or 0) for m in range(1, 13)] for f in filas}


def presupuesto_por_centro(codempresa, codperiodo, centros_sel=None, dim="cc"):
    """{(codcuenta, centro): [pre01..pre12]} del periodo (empnomenclaturadet)."""
    col = columna_centro(dim)
    filtro, params = "", [codempresa, codperiodo]
    if centros_sel:
        filtro = f"AND {col} IN ({','.join(['%s'] * len(centros_sel))})"
        params += list(centros_sel)
    meses = ", ".join(f"SUM(pre{m:02d}) AS pre{m:02d}" for m in range(1, 13))
    filas = consulta(
        f"""
        SELECT codcuenta, {col} AS centro, {meses}
          FROM empnomenclaturadet
         WHERE codempresa = %s AND codperiodo = %s {filtro}
         GROUP BY codcuenta, {col}
        """,
        params,
    )
    return {(f["codcuenta"], f["centro"]): [float(f[f"pre{m:02d}"] or 0) for m in range(1, 13)] for f in filas}


def isr_empresa(codempresa):
    f = consulta_uno("SELECT regimen_isr_porcentaje FROM empresas WHERE codempresa = %s", (codempresa,))
    return float((f or {}).get("regimen_isr_porcentaje") or 0)


def nomenclatura(codempresa, *codperiodos):
    """
    Catálogo de cuentas. Con varios periodos se unen las cuentas (como hacen los
    comparativos de VFP) tomando nombre y estructura del primer periodo que la tenga.
    """
    marcas = ",".join(["%s"] * len(codperiodos))
    filas = consulta(
        f"""
        SELECT codperiodo, codcuenta, nomcta, codctamayor, nivelcta, ultimonivelcta
          FROM empnomenclatura
         WHERE codempresa = %s AND codperiodo IN ({marcas})
        """,
        (codempresa, *codperiodos),
    )
    prioridad = {p: i for i, p in enumerate(codperiodos)}
    filas.sort(key=lambda f: prioridad[f["codperiodo"]])
    cuentas = {}
    for f in filas:
        if f["codcuenta"] and f["codcuenta"] not in cuentas:
            cuentas[f["codcuenta"]] = Cuenta(
                f["codcuenta"], f["nomcta"], f["codctamayor"], int(f["nivelcta"]), f["ultimonivelcta"] == 1
            )
    return [cuentas[c] for c in sorted(cuentas)]
