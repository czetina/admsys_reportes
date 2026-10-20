"""Estructura de un reporte, independiente de la salida (Excel o pantalla)."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Columna:
    titulo: str
    subtitulo: str = ""
    # None: dato (valor, SUBTOTAL o suma de referencias)
    # ("resta", a, b): col a - col b
    # ("pct", num, den): IF(den=0, IF(num=0,0,1), num/den)   (fórmula usada en VFP)
    # ("suma", a, b): suma horizontal de las columnas a..b
    # ("ratio", num, den): num/den, vacío si den = 0 (presupuesto VFP)
    formula: tuple | None = None
    ancho: int = 18
    texto: bool = False             # columna de texto (fecha, tipo, concepto...): sin formato numérico
    entero: bool = False            # cantidades (documentos, facturas): sin decimales
    porcentaje: bool = False        # dato ya calculado que se muestra como porcentaje

    @property
    def es_pct(self):
        return self.porcentaje or (bool(self.formula) and self.formula[0] in ("pct", "ratio"))


@dataclass(eq=False)
class Fila:
    tipo: str = "blanco"            # cuenta | blanco | total | grupo (encabezado de bloque)
    cuenta: str = ""                # código formateado con la máscara
    nombre: str = ""
    valores: list = field(default_factory=list)
    nivel: int = 0
    negrita: bool = False
    oculta: bool = False
    hasta: "Fila | None" = None     # padre: último descendiente (rango del SUBTOTAL)
    refs: list = field(default_factory=list)  # total: [(signo, Fila)]
    rango: tuple | None = None      # total: (primera Fila, última Fila) -> SUBTOTAL del bloque
    codigo: str = ""
    negrita_celdas: set = field(default_factory=set)
    # Fórmulas por celda (solo Excel; el valor ya viene calculado en `valores`):
    #   col -> ("resta", a, b) | ("refs", [(signo, Fila, col)]) | ("factor", Fila, col, factor)
    #          ("subtotal", Fila desde, Fila hasta)  SUBTOTAL de la misma columna
    #          ("lineal", [(signo, col)])            suma de columnas de la misma fila
    #          ("ratio_filas", Fila, col, col_total) cociente de dos celdas de otra fila (porcentajes)
    #          ("producto", [(signo, col)], col_pct)  (suma de columnas de la fila) * col_pct / 100 (IVA)
    #          ("corrido", Fila previa, col_saldo, col_cargo, col_abono)  saldo previo + cargo - abono
    # Las Filas referidas pueden estar en otra hoja del mismo libro.
    formulas: dict = field(default_factory=dict)
    vinculo: str = ""               # hoja destino de un hipervínculo interno en la cuenta/nombre
    alerta: bool = False            # texto en rojo y negrita (p. ej. salto en el correlativo de facturas)


@dataclass
class Reporte:
    titulo: str
    subtitulo: str
    empresa: object
    columnas: list
    filas: list
    nombre_archivo: str
    usar_formulas: bool = True
    fecha_hora: datetime | None = None
    hoja: str = ""                                      # nombre de la hoja en un Libro
    grupos_columnas: list = field(default_factory=list)  # [(col_ini, col_fin, texto)] sobre los títulos
    columnas_ocultas: set = field(default_factory=set)
    vinculo_regreso: str = ""                           # hoja a la que lleva "Regresar al Resumen"
    encabezado_a: tuple = ("CUENTA", 17)                # (título, ancho) de las columnas A y B
    encabezado_b: tuple = ("NOMBRE", 60)
    tabla_plana: bool = False   # hoja de datos (título en fila 1, encabezados en 3, Tabla de Excel con filtros)
    folio: int = 0              # número de la primera página impresa (libros foliados); 0 = sin folio
    resolucion: str = ""        # libros fiscales: número de resolución de la SAT en el encabezado
    moneda_titulo: str = ""     # moneda de la leyenda, si no es la de la empresa (reportes en moneda extranjera)
    porcentaje_filas: set = field(default_factory=set)   # id(Fila) cuyas celdas se muestran como porcentaje

    @property
    def leyenda_moneda(self):
        moneda = (self.moneda_titulo or self.empresa.moneda or "").strip()
        return f"(Cifras Expresadas en {moneda.title()})" if moneda else "(Cifras Expresadas en Moneda Local)"


@dataclass
class Libro:
    """Varios reportes en un mismo archivo, una hoja cada uno (presupuesto por C.C.)."""
    titulo: str
    hojas: list
    nombre_archivo: str

    @property
    def filas(self):
        return [f for h in self.hojas for f in h.filas]


@dataclass
class ArchivoTexto:
    """Archivo plano para auditores (opción «Archivo de Texto» de VFP); se descarga tal cual."""
    nombre_archivo: str
    lineas: list
    filas: list = field(default_factory=list)

    def contenido(self):
        return ("\r\n".join(self.lineas) + "\r\n").encode("cp1252", errors="replace")


def evalua(formula, valores):
    tipo, a, b = formula
    if tipo == "resta":
        return round((valores[a] or 0) - (valores[b] or 0), 2)
    if tipo == "suma":
        return round(sum(v or 0 for v in valores[a:b + 1]), 2)
    if tipo == "pct":
        num, den = valores[a] or 0, valores[b] or 0
        if den == 0:
            return 0.0 if num == 0 else 1.0
        return num / den
    if tipo == "ratio":
        num, den = valores[a] or 0, valores[b] or 0
        return None if den == 0 else num / den
    raise ValueError(tipo)


def completa_derivadas(columnas, valores):
    """Calcula en Python las columnas con fórmula (para pantalla y valor en caché del Excel)."""
    for i, col in enumerate(columnas):
        if col.formula:
            valores[i] = evalua(col.formula, valores)
    return valores
