"""
Activos Fijos (sistema ACF de VFP, menú 50 de ACFIJOS).

  con_calculo_dep_xdia_acfijos   Cálculo de depreciaciones por día     depreciacion

`calcula` es el puerto línea por línea de sistema\\prgs\\calculo_depreciacion.prg: días depreciados,
depreciación mensual, del mes y acumulada, con mes parcial al inicio y al final de la vida útil,
fecha de baja, año comercial (360 días) y valor residual según acf_parametros. Se replica también la
llamada recursiva que VFP hace para el mes anterior (ajuste del último mes). Una sola consulta de
activos; en VFP la hoja de póliza hacía 6 consultas por renglón para decidir si mostrar los centros.
"""
import math
from calendar import monthrange
from collections import OrderedDict, defaultdict
from datetime import date, timedelta

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import ErrorReporte
from .modelo import Columna, Fila, Libro
from .reportes import Base


# ------------------------------------------------------------------ fechas y redondeo como VFP

def _ini_mes(d):
    return d.replace(day=1)


def _fin_mes(d):
    return d.replace(day=monthrange(d.year, d.month)[1])


def _gomonth(d, n):
    """GOMONTH(): suma meses; si el día no existe en el mes destino, queda en el último día."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def _r2(x):
    """ROUND(x, 2) de VFP: mitad hacia afuera del cero."""
    s = -1 if x < 0 else 1
    return s * math.floor(abs(x) * 100 + 0.5 + 1e-9) / 100


def _mismo_mes(a, b):
    return a is not None and b is not None and (a.year, a.month) == (b.year, b.month)


def _ultimo_dia(d):
    """DAY(d + 1) = 1"""
    return d is not None and (d + timedelta(days=1)).day == 1


# ------------------------------------------------------------------ calculo_depreciacion.prg

def calcula(pct, rescate, valor, f_dep, f_baja, f_fin, anio_comercial=False, valor_residual=False, antes=False):
    """
    Devuelve dict(dias, actual, mensual, este_mes, fecha_fin, acumulado) como _resultado(1, 1..6).
    pct: % anual; rescate: valor de rescate; valor: valorml; f_dep: inicio de depreciación;
    f_baja: fecha de baja o None; f_fin: fecha de corte.
    """
    factor_dia = 30 / f_fin.day
    factor_dia_ini = 30 / monthrange(f_dep.year, f_dep.month)[1]
    fin_ant = _ini_mes(f_fin) - timedelta(days=1)
    if not antes:
        previo = calcula(pct, rescate, valor, f_dep, f_baja, fin_ant, anio_comercial, valor_residual, antes=True)
        actual_ant, acumulado_ant = previo["actual"], previo["acumulado"]
    else:
        actual_ant = acumulado_ant = 0.0

    este_mes = 0.0
    dep_anual = int(12 / (pct / 100))                      # meses de vida útil
    f_final_dep = _gomonth(f_dep, dep_anual)
    if valor - rescate > 0:
        monto_a_dep = valor
        base = monto_a_dep - (rescate if valor_residual else 0)
        dep_mensual = _r2(base * pct / 100 / 12)
        dep_diaria = base * pct / 100 / (360 if anio_comercial else 365)
    else:
        monto_a_dep = dep_mensual = dep_diaria = 0.0

    x_fin = f_fin
    if f_baja and x_fin > f_baja:
        x_fin = f_baja
    if f_final_dep < x_fin:
        x_fin = f_final_dep
    ini_dep_mensual = max(_ini_mes(x_fin), f_dep)
    dias = (x_fin - f_dep).days + 1
    if not _ultimo_dia(x_fin):
        dias_mes = 0 if _fin_mes(x_fin).day == x_fin.day else (x_fin - ini_dep_mensual).days + 1
    else:
        dias_mes = 0
    meses_dep = (x_fin.year - f_dep.year) * 12 + (x_fin.month - f_dep.month) + (1 if f_dep.day == 1 else 0)
    dias_mes_ini = 1
    com = (lambda f: f) if anio_comercial else (lambda f: 1)
    ultimo_mes = _mismo_mes(f_final_dep, x_fin)
    if f_dep.day < 2:
        if _ultimo_dia(x_fin) or (f_baja is not None and f_baja.day == 1):
            acumulado = dep_mensual * meses_dep
        else:
            parcial1 = _r2(dep_diaria * dias_mes * com(factor_dia if ultimo_mes else factor_dia_ini))
            acumulado = dep_mensual * (meses_dep - 1 if dias_mes > 0 else meses_dep) + parcial1
    else:
        sig_mes = _gomonth(_ini_mes(f_dep), 1)
        dias_mes_ini = ((x_fin + timedelta(days=1)) if _mismo_mes(x_fin, f_dep) else sig_mes) - f_dep
        dias_mes_ini = dias_mes_ini.days
        f = factor_dia if ultimo_mes else factor_dia_ini
        parcial1 = _r2(dep_diaria * dias_mes * com(f))
        parcial2 = _r2(dep_diaria * dias_mes_ini * com(f))
        acumulado = dep_mensual * (meses_dep - 1 if dias_mes > 0 else meses_dep) + parcial1 + parcial2

    dias_fin_mes = monthrange(x_fin.year, x_fin.month)[1]
    no_completo = lambda f: not _ultimo_dia(x_fin) or (f is None or f.day != 1)     # noqa: E731
    if x_fin < f_fin:
        if _mismo_mes(x_fin, f_fin):
            if acumulado > monto_a_dep:
                este_mes = monto_a_dep - (acumulado - dep_mensual) - rescate
            elif not f_baja and ultimo_mes and no_completo(f_dep) and dias < 31:
                este_mes = _r2(dep_diaria * (dias_mes + dias_mes_ini) * com(factor_dia))
            elif _mismo_mes(f_baja, x_fin) and no_completo(f_baja):
                este_mes = _r2(dep_diaria * dias_mes * com(factor_dia))
            elif _mismo_mes(f_dep, x_fin) and no_completo(f_dep) and dias < 31:
                este_mes = _r2(dep_diaria * (dias_mes + dias_mes_ini) * com(factor_dia_ini))
            else:
                este_mes = dep_mensual
        else:
            este_mes = 0.0
    else:
        if not f_baja and ultimo_mes and no_completo(f_dep) and dias < dias_fin_mes:
            este_mes = _r2(dep_diaria * (dias_mes + dias_mes_ini) * com(factor_dia))
        elif _mismo_mes(f_baja, x_fin) and no_completo(f_baja):
            este_mes = _r2(dep_diaria * dias_mes * com(factor_dia))
        elif _mismo_mes(f_dep, x_fin) and no_completo(f_dep) and dias < dias_fin_mes:
            este_mes = _r2(dep_diaria * (dias_mes + dias_mes_ini) * com(factor_dia_ini))
        else:
            este_mes = dep_mensual

    if (valor - rescate) < acumulado:
        este_mes = dep_mensual - (rescate - (valor - acumulado))
        acumulado = valor - rescate
    monto_actual = valor - acumulado
    if monto_actual < rescate:
        monto_actual = rescate
    if ultimo_mes:
        este_mes = abs(acumulado - acumulado_ant)
    if este_mes < 0:
        este_mes = 0.0
    if f_baja is not None and f_dep == f_baja:
        dias = 0
        dep_mensual = acumulado = este_mes = 0.0
        actual = valor
    else:
        actual = monto_actual
    if este_mes > actual_ant and actual_ant != 0 and fin_ant > f_dep and dias > 60:
        este_mes = abs(actual_ant)
    return {"dias": dias, "actual": round(actual, 2), "mensual": round(dep_mensual, 2),
            "este_mes": round(este_mes, 2), "fecha_fin": x_fin, "acumulado": round(acumulado, 2)}


# ------------------------------------------------------------------ reporte

def _fecha_valida(v):
    return v if isinstance(v, date) and v.year > 1900 else None


def _t(v):
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def _parametros(codempresa):
    par = consulta_uno("SELECT * FROM acf_parametros WHERE codempresa = %s", (codempresa,))
    if not par:
        raise ErrorReporte("Debe definir los parámetros de depreciación de la empresa (acf_parametros).")
    return par


SQL_ACTIVOS = """
SELECT a.*, a.nombre_responsable AS nombredirectorio,
       IFNULL(t.nombre, '') AS nombretipoact, IFNULL(dp.nombre, '') AS nombredepto,
       IFNULL(u.nombre, '') AS nombreubica, t.porcentaje_deprecia,
       {centros},
       {cta_dep} AS codcta_depreciacion,
       IFNULL(tc.codcta_gasto, '') AS codcta_gasto
  FROM acf_activos a
  LEFT JOIN acf_deptos dp ON dp.codempresa = a.codempresa AND dp.coddepto = a.coddepto
  LEFT JOIN acf_deptos_ctas dc ON dc.codempresa = dp.codempresa AND dc.coddepto = dp.coddepto AND dc.codperiodo = %s
  LEFT JOIN acf_tipoact t ON t.codempresa = a.codempresa AND t.codtipoact = a.codtipoact
  LEFT JOIN acf_tipoact_ctas tc ON tc.codempresa = t.codempresa AND tc.codtipoact = t.codtipoact AND tc.codperiodo = %s
  LEFT JOIN acf_ubica u ON u.codempresa = a.codempresa AND u.codubica = a.codubica
  LEFT JOIN acf_ubica_cuenta uc ON uc.codempresa = a.codempresa AND uc.codubica = a.codubica AND uc.codperiodo = %s
 WHERE a.codempresa = %s AND t.porcentaje_deprecia > 0 AND a.fecha_depreciacion <= %s
   {filtros}
"""


def depreciacion(p):
    """
    con_calculo_dep_xdia_acfijos: hoja DEPTOS (cada activo con su depreciación, agrupado por
    departamento y tipo de activo o al revés) y hoja POLIZA (gasto al debe y depreciación acumulada
    al haber, por departamento, cuenta y centros) a la fecha de corte.
    """
    b = Base(p)
    fecha = p.fecha_fin or date(p.anio, p.mes, monthrange(p.anio, p.mes)[1])
    par = _parametros(p.codempresa)
    per = ctx.periodo(p.codempresa, fecha.year, fecha.month)
    tabla_centros = {1: "uc", 2: "tc"}.get(int(par["opt_cc_ca_ci"] or 3), "dc")
    centros = ", ".join(f"IFNULL({tabla_centros}.{c}, '') AS {c}" for c in ("codcc", "codci", "codca"))
    cta_dep = ("IFNULL(tc.codcta_depreciacion, 'NDef.')" if par["dep_por_tipoact"] == 1
               else "IFNULL(dp.codcta_depreciacion, '')")
    partes, params = [], []
    for valor, sql in ((p.activo_desde, "a.codactivo >= %s"), (p.activo_hasta, "a.codactivo <= %s"),
                       (p.ubica_desde, "a.codubica >= %s"), (p.ubica_hasta, "a.codubica <= %s"),
                       (p.tipoact_desde, "a.codtipoact >= %s"), (p.tipoact_hasta, "a.codtipoact <= %s"),
                       (p.depto_desde, "a.coddepto >= %s"), (p.depto_hasta, "a.coddepto <= %s"),
                       (p.responsable_desde, "a.codresponsable >= %s"),
                       (p.responsable_hasta, "a.codresponsable <= %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    if p.solo_electronico:
        partes.append("AND a.es_equipo_electronico = 1")
    cod = per.codperiodo
    filas_bd = consulta(SQL_ACTIVOS.format(centros=centros, cta_dep=cta_dep, filtros=" ".join(partes)),
                        (cod, cod, cod, p.codempresa, fecha, *params))
    ini_mes = _ini_mes(fecha)
    activos = []
    for a in filas_bd:
        baja = _fecha_valida(a["fecha_baja"])
        if baja and (baja <= fecha if p.ocultar_bajas else baja < ini_mes):
            continue
        a["fecha_baja"] = baja
        a["fecha_compra"] = _fecha_valida(a["fecha_compra"])
        a["fecha_depreciacion"] = _fecha_valida(a["fecha_depreciacion"]) or a["fecha_compra"]
        if not a["fecha_depreciacion"]:
            continue
        a["calc"] = calcula(float(a["porcentaje_deprecia"]), float(a["valor_rescate"] or 0), float(a["valorml"] or 0),
                            a["fecha_depreciacion"], baja, fecha, par["usa_anio_comercial"] == 1,
                            par["usa_valor_residual_al_depreciar"] == 1)
        activos.append(a)
    if not activos:
        raise ErrorReporte("No existe detalle de activos depreciables con los parámetros indicados.")
    por_depto = p.orden_activos != 2
    externo, interno = (("coddepto", "nombredepto"), ("codtipoact", "nombretipoact")) if por_depto else \
        (("codtipoact", "nombretipoact"), ("coddepto", "nombredepto"))
    activos.sort(key=lambda a: (_t(a[externo[0]]), _t(a[interno[0]]), _t(a["codactivo"])))

    cols = [Columna("FECHA INICIAL", ancho=11, texto=True), Columna("FECHA COMPRA", ancho=11, texto=True),
            Columna("FECHA BAJA", ancho=11, texto=True), Columna("VALOR"), Columna("% A DEPRECIAR", ancho=10),
            Columna("DÍAS", ancho=8, entero=True), Columna("DEPRECIACIÓN MENSUAL"), Columna("MONTO DEPRECIADO", "ESTE MES"),
            Columna("DEPRECIACIÓN ACUMULADA"), Columna("VALOR ACTUAL"), Columna("ÚLTIMA DEPRECIACIÓN", ancho=11, texto=True),
            Columna("UBICACIÓN", ancho=24, texto=True), Columna("RESPONSABLE", ancho=30, texto=True),
            Columna("VALOR RESIDUAL")]
    n = len(cols)
    sumas = (3, 6, 7, 8, 9, 13)
    fx = lambda d: d.strftime("%d/%m/%Y") if d else ""      # noqa: E731
    filas, totales_ext = [], []
    grupos = OrderedDict()
    for a in activos:
        grupos.setdefault(_t(a[externo[0]]), []).append(a)
    for cod_ext, lista in grupos.items():
        cab_ext = Fila(tipo="grupo", cuenta=cod_ext, nombre=_t(lista[0][externo[1]]))
        filas.append(cab_ext)
        subt = []
        internos = OrderedDict()
        for a in lista:
            internos.setdefault(_t(a[interno[0]]), []).append(a)
        for cod_int, items in internos.items():
            filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=cod_int, nombre=_t(items[0][interno[1]]),
                              valores=[None] * n))
            bloque = []
            for a in items:
                c = a["calc"]
                valor = float(a["valortotal"] or 0) if a["es_inmueble"] == 1 else float(a["valorml"] or 0)
                v = [fx(a["fecha_depreciacion"]), fx(a["fecha_compra"]), fx(a["fecha_baja"]), round(valor, 2),
                     float(a["porcentaje_deprecia"]), c["dias"], c["mensual"], c["este_mes"], c["acumulado"],
                     c["actual"], fx(c["fecha_fin"]), _t(a["nombreubica"]),
                     _t(a["nombredirectorio"]) or _t(a["codresponsable"]) or "N/E", float(a["valor_rescate"] or 0)]
                bloque.append(Fila(tipo="cuenta", nivel=2, cuenta=_t(a["codactivo"]), nombre=_t(a["nombre"]), valores=v))
            filas += bloque
            t = Fila(tipo="total", nombre="TOTAL " + _t(items[0][interno[1]]), rango=(bloque[0], bloque[-1]),
                     valores=[None] * n)
            for k in sumas:
                t.valores[k] = round(sum(x.valores[k] or 0 for x in bloque), 2)
            filas.append(t)
            subt += bloque
        te = Fila(tipo="total", negrita=True, nombre="TOTAL " + _t(lista[0][externo[1]]), rango=(subt[0], subt[-1]),
                  valores=[None] * n)
        for k in sumas:
            te.valores[k] = round(sum(x.valores[k] or 0 for x in subt), 2)
        filas += [te, Fila()]
        totales_ext.append((subt[0], subt[-1]))
    todos = [f for f in filas if f.tipo == "cuenta" and f.nivel == 2]
    gran = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(todos[0], todos[-1]), valores=[None] * n)
    for k in sumas:
        gran.valores[k] = round(sum(x.valores[k] or 0 for x in todos), 2)
    filas.append(gran)
    titulo = "LISTADO DE DEPRECIACIÓN DE ACTIVOS FIJOS"
    hoja1 = b.reporte(titulo, f"AL {fx(fecha)}", cols, filas)
    hoja1.hoja = "Deptos"
    hoja1.encabezado_a, hoja1.encabezado_b = ("CÓDIGO", 14), ("DESCRIPCIÓN", 40)
    hoja2 = _poliza(b, p, activos, fecha, per)
    return Libro(titulo=hoja1.titulo, hojas=[hoja1, hoja2], nombre_archivo=hoja1.nombre_archivo)


def _poliza(b, p, activos, fecha, per):
    """
    Póliza de depreciación del mes: gasto (codcta_gasto) al debe y depreciación acumulada
    (codcta_depreciacion) al haber, por departamento, cuenta y centros. VFP agrupaba el debe por la
    cuenta de depreciación aunque mostraba la de gasto (dos tipos con distinto gasto y la misma
    depreciación quedaban en un renglón); aquí cada lado se agrupa por su propia cuenta.
    """
    lineas = defaultdict(float)
    for a in activos:
        c = a["calc"]
        if c["este_mes"] <= 0 or c["fecha_fin"] > fecha or c["actual"] < float(a["valor_rescate"] or 0):
            continue
        clave = (_t(a["coddepto"]), _t(a["nombredepto"]), _t(a["codcc"]), _t(a["codci"]), _t(a["codca"]))
        lineas[("D", _t(a["codcta_gasto"])) + clave] += c["este_mes"]
        lineas[("H", _t(a["codcta_depreciacion"])) + clave] += c["este_mes"]
    nom = {f["codcuenta"]: f for f in consulta(
        """SELECT codcuenta, nomcta, usacibgcta, usacabgcta FROM empnomenclatura
            WHERE codempresa = %s AND codperiodo = %s""", (p.codempresa, per.codperiodo))}
    usa = consulta_uno("""SELECT usaccepg, usaciepg, usacibg, usacabg FROM empperiodos
                           WHERE codempresa = %s AND codperiodo = %s""", (p.codempresa, per.codperiodo)) or {}
    resultado = set(b.nom.prefijos("ingresos", "otrosingresos", "costos", "gastos", "egresos", "otrosegresos",
                                   "ordendeudoras", "ordenacreedoras"))

    def muestra(cuenta, centro):
        """_UsaCc / _UsaCi / _UsaCa de VFP: el centro solo va si la cuenta y el periodo lo usan."""
        de_resultado = cuenta[: b.logn] in resultado
        cta = nom.get(cuenta) or {}
        if centro == "cc":
            return usa.get("usaccepg") == 1 and de_resultado
        if centro == "ci":
            return (cta.get("usacibgcta") == 1 and usa.get("usacibg") == 1 and not de_resultado) or                 (de_resultado and usa.get("usaciepg") == 1)
        return cta.get("usacabgcta") == 1 and usa.get("usacabg") == 1 and not de_resultado

    def centro(cuenta, valor, tipo):
        return (valor or "S/N") if muestra(cuenta, tipo) else ""

    cols = [Columna("C. COSTO", ancho=10, texto=True), Columna("C. INTEGRACIÓN", ancho=10, texto=True),
            Columna("C. ACTIVIDAD", ancho=10, texto=True), Columna("CARGO"), Columna("ABONO"),
            Columna("DEPTO.", ancho=8, texto=True), Columna("DESCRIPCIÓN", ancho=30, texto=True)]
    filas = []
    for (deha, cuenta, depto, nomdepto, cc, ci, ca), monto in sorted(lineas.items()):
        v = [centro(cuenta, cc, "cc"), centro(cuenta, ci, "ci"), centro(cuenta, ca, "ca"),
             round(monto, 2) if deha == "D" else None, round(monto, 2) if deha == "H" else None, depto, nomdepto]
        filas.append(Fila(tipo="cuenta", nivel=1, cuenta=b.nom.formatea(cuenta) if cuenta else depto,
                          nombre=_t((nom.get(cuenta) or {}).get("nomcta")) or "(cuenta no definida)", valores=v))
    if not filas:
        filas.append(Fila(tipo="grupo", nombre="Sin depreciación en el mes"))
        rango = None
    else:
        rango = (filas[0], filas[-1])
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=rango, valores=[None] * len(cols))
    tot.valores[3] = round(sum(f.valores[3] or 0 for f in filas if f.tipo == "cuenta"), 2)
    tot.valores[4] = round(sum(f.valores[4] or 0 for f in filas if f.tipo == "cuenta"), 2)
    filas.append(tot)
    rep = b.reporte("PÓLIZA DE ACTIVOS FIJOS", f"DEPRECIACIÓN DEL MES · AL {fecha:%d/%m/%Y}", cols, filas)
    rep.hoja = "Poliza"
    rep.encabezado_a, rep.encabezado_b = ("CUENTA", 16), ("DESCRIPCIÓN", 36)
    return rep
