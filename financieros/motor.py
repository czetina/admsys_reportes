"""
Armado de filas de los estados financieros.

Para conservar exactamente el orden y la posición de totales de los reportes VFP,
se replica su algoritmo: recorre las cuentas en orden (orden+cuenta) DESCENDENTE e
inserta cada fila en la parte superior (en VFP: entirerow.INSERT en la fila 10).
La diferencia es que aquí se arma una lista en memoria y Excel se escribe una sola
vez al final, en lugar de miles de llamadas COM.
"""
from .modelo import Fila, completa_derivadas

# Orden de las secciones (campo `orden` de los cursores _cuentas de VFP).
# Si un prefijo cae en varios grupos gana el último, igual que los REPLACE ALL.
ORDEN_BG = (("1", ("activo",)), ("2", ("regularizacionactivo",)), ("3", ("pasivo",)),
            ("4", ("regularizacionpasivo",)), ("5", ("capital",)))
ORDEN_ER = (("1", ("ingresos",)), ("2", ("costos",)), ("3", ("gastos", "egresos")),
            ("4", ("otrosingresos",)), ("5", ("otrosegresos",)))
ORDEN_CTAS_ORDEN = (("1", ("ordendeudoras",)), ("2", ("ordenacreedoras",)))

DEUDORAS_BG = ("activo", "regularizacionactivo")
DEUDORAS_ER = ("egresos", "costos", "gastos", "otrosegresos")
ACREEDORAS_ER = ("ingresos", "otrosingresos")


def prefijos_orden(orden_def, nom):
    return {p for _, grupos in orden_def for p in nom.prefijos(*grupos)}


def orden_de(codigo, orden_def, nom):
    orden = " "
    for valor, grupos in orden_def:
        if nom.grupo(codigo, *grupos):
            orden = valor
    return orden


def selecciona(nomencla, orden_def, nom, activas=None):
    """
    Cuentas a mostrar, ordenadas por (orden, código).
    `activas` = cuentas con movimiento (el _detalle de VFP). Si se indica, solo se
    dejan las cuentas que son prefijo de alguna activa (SEEK parcial de VFP).
    """
    prefijos = prefijos_orden(orden_def, nom)
    lineas = [c for c in nomencla if c.codigo[: nom.logn] in prefijos]
    if activas is not None:
        con_hijos = {a[:i] for a in activas for i in range(1, len(a) + 1)}
        lineas = [c for c in lineas if c.codigo in con_hijos]
    lineas = [(c, orden_de(c.codigo, orden_def, nom)) for c in lineas]
    lineas.sort(key=lambda x: (x[1], x[0].codigo))
    return lineas


def es_nivel1(c, nom):
    return len(c.codigo) == nom.logn


def fila_cuenta(c, nom, nivel_max, ncols, valores_hoja):
    fila = Fila(
        tipo="cuenta",
        codigo=c.codigo,
        cuenta=nom.formatea(c.codigo),
        nombre=" " * len(c.codigo) + c.nombre,
        nivel=c.nivel,
        oculta=c.nivel > nivel_max,
    )
    if c.ultimo:
        fila.valores = list(valores_hoja) if valores_hoja is not None else [None] * ncols
    else:
        fila.hasta = fila  # se ajusta en finaliza(); marca de cuenta padre
        fila.negrita = c.nivel < nivel_max
    return fila


def finaliza(filas, columnas):
    """Calcula subtotales de cuentas padre, totales y columnas derivadas."""
    ncols = len(columnas)
    datos = [i for i, col in enumerate(columnas) if not col.formula]
    cuentas = [f for f in filas if f.tipo == "cuenta"]

    for i, f in enumerate(cuentas):
        if f.hasta is None:
            if any(v is not None for v in f.valores):
                completa_derivadas(columnas, f.valores)
            continue
        j = i
        while j + 1 < len(cuentas) and cuentas[j + 1].codigo.startswith(f.codigo):
            j += 1
        f.hasta = cuentas[j] if j > i else None
        f.valores = [None] * ncols
        for k in datos:
            f.valores[k] = round(sum(
                (h.valores[k] or 0) for h in cuentas[i + 1: j + 1] if h.hasta is None and h.valores
            ), 2)
        completa_derivadas(columnas, f.valores)

    for f in filas:
        if f.tipo == "total" and f.rango:
            i, j = filas.index(f.rango[0]), filas.index(f.rango[1])
            bloque = [h for h in filas[i:j + 1] if h.tipo == "cuenta" and h.hasta is None and h.valores]
            f.valores = [None] * ncols
            for k in datos:
                f.valores[k] = round(sum(h.valores[k] or 0 for h in bloque), 2)
            completa_derivadas(columnas, f.valores)
        elif f.tipo == "total" and f.refs:
            f.valores = [None] * ncols
            for k in datos:
                f.valores[k] = round(sum(s * (r.valores[k] or 0) for s, r in f.refs), 2)
            completa_derivadas(columnas, f.valores)
        elif f.tipo == "total" and f.valores:
            completa_derivadas(columnas, f.valores)
    return filas


def _total(nombre, refs, ncols):
    return Fila(tipo="total", nombre=nombre, negrita=True, refs=refs, valores=[None] * ncols)


def arma_bg(lineas, nom, nivel_max, columnas, valores_de, total_activo=True):
    """Balance General (con_bg_xls y sus comparativos), una columna por dato."""
    ncols = len(columnas)
    filas, creadas, hecho = [], [], False
    for c, orden in reversed(lineas):
        if not hecho and orden < "3":
            nivel1 = [(o, f, cc) for o, f, cc in creadas if o in "345" and es_nivel1(cc, nom)]
            nombres = [cc.nombre.upper() for o, f, cc in reversed(nivel1) if o in "35"]
            total = _total("TOTAL: " + " + ".join(nombres), [(1, f) for o, f, cc in reversed(nivel1)], ncols)
            filas.insert(0, total)
            filas.insert(0, Fila())
            hecho = True
        f = fila_cuenta(c, nom, nivel_max, ncols, valores_de(c))
        creadas.append((orden, f, c))
        filas.insert(0, f)
        if not c.mayor:
            filas.insert(0, Fila())

    if total_activo and nom.grupos.get("regularizacionactivo"):
        nivel1 = [(o, f, cc) for o, f, cc in reversed(creadas) if o in "12" and es_nivel1(cc, nom)]
        nombres = [cc.nombre.upper() for o, f, cc in nivel1 if o == "1"]
        filas.insert(0, _total("TOTAL: " + " + ".join(nombres), [(1, f) for o, f, cc in nivel1], ncols))
        filas.insert(0, Fila())
    return finaliza(filas, columnas)


def arma_er(lineas, nom, nivel_max, columnas, valores_de, signo_resultado,
            utilidad_bruta=None, etiqueta="RESULTADO DEL PERIODO"):
    """Estado de Resultados (con_er_xls, comparativos, mensual, cuentas de orden)."""
    ncols = len(columnas)
    filas, nivel1, ub_hecha = [], [], False
    for c, orden in reversed(lineas):
        if utilidad_bruta is not None and orden == "2" and not ub_hecha:
            ub = Fila(tipo="total", nombre="UTILIDAD BRUTA ", negrita=True, valores=list(utilidad_bruta))
            filas[0:0] = [Fila(), Fila(), ub, Fila()]
            ub_hecha = True
        f = fila_cuenta(c, nom, nivel_max, ncols, valores_de(c))
        filas.insert(0, f)
        if es_nivel1(c, nom):
            nivel1.insert(0, (c, f))
        if not c.mayor:
            filas.insert(0, Fila())

    filas.append(Fila())
    filas.append(_total(etiqueta, [(signo_resultado(c), f) for c, f in nivel1], ncols))
    return finaliza(filas, columnas)


# ---------------------------------------------------------------- versiones "-01"

def acumula_por_mayor(valores, nomencla):
    """Suma cada saldo en su cuenta y en toda la cadena de cuentas mayores (_saldos de VFP)."""
    mayor = {c.codigo: c.mayor for c in nomencla}
    total = {}
    for cuenta, v in valores.items():
        cta, vistos = cuenta, set()
        while cta in mayor and cta not in vistos:
            vistos.add(cta)
            total[cta] = round(total.get(cta, 0) + v, 2)
            cta = mayor[cta]
    return total


def _fila_nivel(c, nom, nivel_max, valor):
    valores = [None] * nivel_max
    valores[nivel_max - c.nivel] = valor
    f = Fila(tipo="cuenta", codigo=c.codigo, cuenta=nom.formatea(c.codigo),
             nombre=" " * len(c.codigo) + c.nombre, nivel=c.nivel, valores=valores)
    return f


def arma_bg_columnas(lineas, nom, nivel_max, saldos):
    """Balance General en columnas por nivel (con_bg_xls-01)."""
    filas, hecho = [], False
    for c, orden in reversed(lineas):
        if orden < "3" and not hecho:
            nivel1 = [(cc, o) for cc, o in lineas if o in "35" and es_nivel1(cc, nom)]
            valor = round(sum(saldos.get(cc.codigo, 0) for cc, o in nivel1), 2)
            total = Fila(tipo="total", negrita=True, valores=[None] * nivel_max,
                         nombre="TOTAL: " + " + ".join(cc.nombre.upper() for cc, o in nivel1))
            total.valores[-1] = valor
            filas[0:0] = [Fila(), total, Fila()]
            hecho = True
        if c.nivel <= nivel_max:
            filas.insert(0, _fila_nivel(c, nom, nivel_max, saldos.get(c.codigo, 0)))
    return filas


def arma_er_columnas(lineas, nom, nivel_max, saldos, utilidad_bruta, signo_resultado):
    """Estado de Resultados en columnas por nivel (con_er_xls-01)."""
    filas, ub_hecha = [], False
    for c, orden in reversed(lineas):
        if orden == "2" and utilidad_bruta and not ub_hecha:
            ub = Fila(tipo="total", nombre="UTILIDAD BRUTA ", negrita=True, valores=[None] * nivel_max)
            ub.valores[-1] = utilidad_bruta
            filas[0:0] = [Fila(), Fila(), ub, Fila()]
            ub_hecha = True
        if c.nivel <= nivel_max:
            f = _fila_nivel(c, nom, nivel_max, saldos.get(c.codigo, 0))
            if c.nivel == 1:
                f.negrita_celdas.add(nivel_max - 1)
            filas.insert(0, f)

    resultado = sum(signo_resultado(c) * saldos.get(c.codigo, 0) for c, o in lineas if es_nivel1(c, nom))
    total = Fila(tipo="total", nombre="RESULTADO DEL PERIODO", negrita=True, valores=[None] * nivel_max)
    total.valores[-1] = round(resultado, 2)
    filas += [Fila(), total]
    return filas
