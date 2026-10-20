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


# ------------------------------------------------------------------ listados (menú 50)

def _fx(d):
    d = _fecha_valida(d)
    return d.strftime("%d/%m/%Y") if d else ""


def _rangos(p, campos=("activo", "ubica", "tipoact", "depto", "responsable"), alias="a"):
    columnas = {"activo": "codactivo", "ubica": "codubica", "tipoact": "codtipoact", "depto": "coddepto",
                "responsable": "codresponsable"}
    partes, params = [], []
    for clave in campos:
        for lado, op in (("desde", ">="), ("hasta", "<=")):
            valor = getattr(p, f"{clave}_{lado}", "")
            if valor:
                partes.append(f"AND {alias}.{columnas[clave]} {op} %s")
                params.append(valor)
    return partes, params


def _fechas(p, campo, partes, params):
    for valor, op in ((p.fecha_ini, ">="), (p.fecha_fin, "<=")):
        if valor:
            partes.append(f"AND {campo} {op} %s")
            params.append(valor)


def _periodo_txt(p, que):
    if p.fecha_ini and p.fecha_fin:
        return f"POR FECHA {que}: {p.fecha_ini:%d/%m/%Y} AL {p.fecha_fin:%d/%m/%Y}"
    if p.fecha_fin:
        return f"POR FECHA {que} HASTA EL {p.fecha_fin:%d/%m/%Y}"
    if p.fecha_ini:
        return f"POR FECHA {que} DESDE EL {p.fecha_ini:%d/%m/%Y}"
    return "TODAS LAS FECHAS"


def _sin_datos(filas, que="activos"):
    if not filas:
        raise ErrorReporte(f"No se encontraron {que} con los parámetros indicados.")


def _cierra(b, titulo, subtitulo, cols, filas, enc_a=("CÓDIGO", 14), enc_b=("DESCRIPCIÓN", 40)):
    rep = b.reporte(titulo, subtitulo, cols, filas)
    rep.encabezado_a, rep.encabezado_b = enc_a, enc_b
    return rep


def _agrupa(filas, campo):
    grupos = OrderedDict()
    for f in filas:
        grupos.setdefault(_t(f[campo]), []).append(f)
    return grupos.items()


def _fila_listado(a, valores, nivel=2, prefijo=""):
    return Fila(tipo="cuenta", nivel=nivel, cuenta=_t(a["codactivo"]), nombre=prefijo + _t(a["nombre"]), valores=valores)


def _total(nombre, bloque, n, sumas, negrita=False):
    cuentas = [x for x in bloque if x.tipo == "cuenta"]
    t = Fila(tipo="total", negrita=negrita, nombre=nombre, valores=[None] * n,
             rango=(cuentas[0], cuentas[-1]) if cuentas else None)
    for k in sumas:
        t.valores[k] = round(sum(x.valores[k] or 0 for x in cuentas), 2)
    return t


SQL_ACCESORIOS = """SELECT c.keytra_mae, a.*, IFNULL(u.nombre, '') AS nom_ubica, IFNULL(r.nombre, '') AS nom_responsable
  FROM acf_consolidado c
  JOIN acf_activos a ON a.keytra = c.keytra_add
  LEFT JOIN acf_ubica u ON u.codempresa = a.codempresa AND u.codubica = a.codubica
  LEFT JOIN acf_responsables r ON r.codempresa = a.codempresa AND r.codresponsable = a.codresponsable
 WHERE c.codempresa = %s ORDER BY a.codactivo"""


def _accesorios(codempresa):
    out = defaultdict(list)
    for f in consulta(SQL_ACCESORIOS, (codempresa,)):
        out[f["keytra_mae"]].append(f)
    return out


NO_ES_ACCESORIO = """AND NOT EXISTS (SELECT 1 FROM acf_consolidado c
                                 WHERE c.codempresa = a.codempresa AND c.keytra_add = a.keytra)"""


def por_responsable(p):
    """con_articulos_x_responsable: activos (sin los que son accesorio de otro) por responsable, con sus accesorios."""
    b = Base(p)
    partes, params = _rangos(p, ("responsable",))
    _fechas(p, "a.fecha_compra", partes, params)
    filas_bd = consulta(f"""
        SELECT a.*, r.nombre AS nom_responsable, IFNULL(u.nombre, '') AS nom_ubica
          FROM acf_activos a
          JOIN acf_responsables r ON r.codempresa = a.codempresa AND r.codresponsable = a.codresponsable
          LEFT JOIN acf_ubica u ON u.codempresa = a.codempresa AND u.codubica = a.codubica
         WHERE a.codempresa = %s {NO_ES_ACCESORIO} {' '.join(partes)}
         ORDER BY a.codresponsable, a.codactivo""", (p.codempresa, *params))
    _sin_datos(filas_bd)
    acc = _accesorios(p.codempresa)
    cols = [Columna("VALOR"), Columna("UBICACIÓN", ancho=24, texto=True), Columna("MARCA", ancho=16, texto=True),
            Columna("MODELO", ancho=16, texto=True)]

    def valores(a):
        return [float(a["valorml"] or 0), _t(a["nom_ubica"]), _t(a["marca"]), _t(a["modelo"])]
    filas, todos = [], []
    for cod, lista in _agrupa(filas_bd, "codresponsable"):
        filas.append(Fila(tipo="grupo", cuenta=cod, nombre=_t(lista[0]["nom_responsable"])))
        bloque = []
        for a in lista:
            bloque.append(_fila_listado(a, valores(a)))
            bloque += [_fila_listado(x, valores(x), nivel=3, prefijo="  ↳ ") for x in acc.get(a["keytra"], [])]
        filas += bloque + [_total(f"TOTAL {_t(lista[0]['nom_responsable'])}", bloque, len(cols), (0,)), Fila()]
        todos += bloque
    filas.append(_total("TOTALES", todos, len(cols), (0,), negrita=True))
    return _cierra(b, "ARTÍCULOS POR RESPONSABLE", _periodo_txt(p, "DE COMPRA"), cols, filas)


def ficha_responsabilidad(p):
    """
    con_inventario_tarjeta_responsa: tarjeta de un responsable con sus activos (una fila por unidad del
    detalle acf_activos_det que le corresponde), por código o por nombre, con la línea de firma.
    """
    b = Base(p)
    filas_bd = consulta("""
        SELECT a.codactivo, a.nombre, a.serie, a.marca, a.observaciones AS observa, a.codubica,
               IFNULL(r.nombre, '') AS nom_responsable, IFNULL(r.observaciones, '') AS dpi,
               IFNULL(dp.nombre, '') AS nombre_depto, d.linea
          FROM acf_activos a
          LEFT JOIN acf_activos_det d ON d.codempresa = a.codempresa AND d.codactivo = a.codactivo
               AND d.codresponsable = a.codresponsable
          LEFT JOIN acf_responsables r ON r.codempresa = a.codempresa AND r.codresponsable = a.codresponsable
          LEFT JOIN acf_deptos dp ON dp.codempresa = a.codempresa AND dp.coddepto = a.coddepto
         WHERE a.codempresa = %s AND a.codresponsable = %s""", (p.codempresa, p.codresponsable))
    _sin_datos(filas_bd)

    def numero(cod):           # VFP: VAL(SUBSTRING(codactivo, 4)) -> orden numérico del código
        try:
            return int(_t(cod)[3:] or 0)
        except ValueError:
            return 0
    if p.orden_activos == 2:
        filas_bd.sort(key=lambda f: _t(f["nombre"]))
    else:
        filas_bd.sort(key=lambda f: (numero(f["codactivo"]), _t(f["codactivo"])))
    cols = [Columna("MARCA / SERIE", ancho=30, texto=True), Columna("OBSERVACIONES", ancho=40, texto=True)]
    primero = filas_bd[0]
    filas = [Fila(tipo="grupo", nombre=f"Departamento: {_t(primero['nombre_depto'])}  ·  "
                                       f"Ubicación: {_t(primero['codubica'])}")]
    for a in filas_bd:
        serie = f"{_t(a['marca'])} {_t(a['serie'])}".strip() if _t(a["serie"]) else "~~~ No tiene serie ~~~"
        filas.append(_fila_listado(a, [serie, _t(a["observa"])], nivel=1))
    filas += [Fila(), Fila(tipo="grupo", nombre=f"YO: {_t(primero['nom_responsable'])}    DPI: {_t(primero['dpi'])}"),
              Fila(tipo="grupo", nombre="Firma: ______________________________")]
    return _cierra(b, "FICHA DE RESPONSABILIDAD",
                   f"RESPONSABLE: {_t(primero['nom_responsable'])} ({p.codresponsable})", cols, filas)


def por_tipo(p):
    """
    con_acfactivos_tipos: activos vigentes a la fecha final (sin baja o con baja posterior), por tipo
    de activo (y responsable), con proveedor, factura y valores; los accesorios van bajo su activo.
    """
    b = Base(p)
    partes, params = _rangos(p, ("activo", "ubica", "tipoact", "depto", "responsable"))
    _fechas(p, "a.fecha_compra", partes, params)
    if p.fecha_fin:
        partes.append("AND (a.fecha_baja IS NULL OR a.fecha_baja = '0000-00-00' OR a.fecha_baja > %s)")
        params.append(p.fecha_fin)
    if p.solo_electronico:
        partes.append("AND a.tipo_equipo = 1")
    con_resp = p.orden_activos == 2
    orden = ("a.codtipoact, a.codresponsable, a.numero, a.fecha_compra, a.nombre" if con_resp
             else "a.codtipoact, a.fecha_compra, a.codresponsable, a.numero, a.nombre")
    grupo = (consulta_uno("SELECT codgrupo FROM empresas WHERE codempresa = %s", (p.codempresa,)) or {}).get("codgrupo")
    filas_bd = consulta(f"""
        SELECT a.*, IFNULL(t.nombre, '') AS nomtipoact, IFNULL(g.razonsocial, '') AS nomproveedor,
               IFNULL(u.nombre, '') AS nom_ubica
          FROM acf_activos a
          LEFT JOIN acf_tipoact t ON t.codempresa = a.codempresa AND t.codtipoact = a.codtipoact
          LEFT JOIN grudirectorio g ON g.codgrupo = %s AND g.coddirectorio = a.codproveedor
          LEFT JOIN acf_ubica u ON u.codempresa = a.codempresa AND u.codubica = a.codubica
         WHERE a.codempresa = %s {NO_ES_ACCESORIO} {' '.join(partes)}
         ORDER BY {orden}""", (grupo, p.codempresa, *params))
    _sin_datos(filas_bd)
    acc = _accesorios(p.codempresa)
    cols = [Columna("FECHA COMPRA", ancho=11, texto=True), Columna("UBICACIÓN", ancho=22, texto=True),
            Columna("MARCA", ancho=14, texto=True), Columna("MODELO", ancho=14, texto=True),
            Columna("SERIE", ancho=16, texto=True), Columna("PROVEEDOR", ancho=30, texto=True),
            Columna("FACTURA", ancho=12, texto=True), Columna("VALOR M.E."), Columna("VALOR M.L."),
            Columna("OBSERVACIONES", ancho=36, texto=True)]
    n = len(cols)

    def renglon(a, nivel=2, prefijo=""):
        return _fila_listado(a, [_fx(a["fecha_compra"]), _t(a["nom_ubica"]), _t(a["marca"]), _t(a["modelo"]),
                                 _t(a["serie"]), _t(a.get("nomproveedor")), _t(a["factura"]),
                                 float(a["valorme"] or 0), float(a["valorml"] or 0), _t(a["observaciones"])],
                             nivel, prefijo)
    filas, todos = [], []
    for tipo, lista in _agrupa(filas_bd, "codtipoact"):
        filas.append(Fila(tipo="grupo", cuenta=tipo, nombre=_t(lista[0]["nomtipoact"])))
        bloque_tipo = []
        for resp, items in (_agrupa(lista, "codresponsable") if con_resp else [("", lista)]):
            if con_resp:
                filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=resp,
                                  nombre=_t(items[0]["nombre_responsable"]), valores=[None] * n))
            bloque = []
            for a in items:
                bloque.append(renglon(a))
                bloque += [renglon(x, 3, "  ↳ ") for x in acc.get(a["keytra"], [])]
            filas += bloque
            if con_resp:
                filas.append(_total(f"TOTAL {_t(items[0]['nombre_responsable'])}", bloque, n, (7, 8)))
            bloque_tipo += bloque
        filas += [_total(f"TOTAL {_t(lista[0]['nomtipoact'])}", bloque_tipo, n, (7, 8), negrita=True), Fila()]
        todos += bloque_tipo
    filas.append(_total("TOTALES", todos, n, (7, 8), negrita=True))
    return _cierra(b, "ACTIVOS FIJOS POR TIPO", _periodo_txt(p, "DE COMPRA"), cols, filas)


def no_depreciables(p):
    """con_articulos_no_depreciables: activos de tipos con 0 % de depreciación, por departamento y tipo."""
    b = Base(p)
    partes, params = _rangos(p, ("activo", "ubica", "tipoact", "depto"))
    if p.fecha_fin:
        partes.append("AND a.fecha_compra <= %s")
        params.append(p.fecha_fin)
    filas_bd = consulta(f"""
        SELECT a.*, IFNULL(t.nombre, '') AS nombretipoact, IFNULL(dp.nombre, '') AS nombredepto,
               IFNULL(u.nombre, '') AS nombreubica
          FROM acf_activos a
          LEFT JOIN acf_deptos dp ON dp.codempresa = a.codempresa AND dp.coddepto = a.coddepto
          LEFT JOIN acf_tipoact t ON t.codempresa = a.codempresa AND t.codtipoact = a.codtipoact
          LEFT JOIN acf_ubica u ON u.codempresa = a.codempresa AND u.codubica = a.codubica
         WHERE a.codempresa = %s AND t.porcentaje_deprecia = 0 {' '.join(partes)}""", (p.codempresa, *params))
    _sin_datos(filas_bd, "activos no depreciables")
    ext, inte = ((("coddepto", "nombredepto"), ("codtipoact", "nombretipoact")) if p.orden_activos != 2 else
                 (("codtipoact", "nombretipoact"), ("coddepto", "nombredepto")))
    filas_bd.sort(key=lambda a: (_t(a[ext[0]]), _t(a[inte[0]]), _t(a["codactivo"])))
    cols = [Columna("FECHA INICIAL", ancho=11, texto=True), Columna("FECHA BAJA", ancho=11, texto=True), Columna("VALOR"),
            Columna("UBICACIÓN", ancho=24, texto=True), Columna("RESPONSABLE", ancho=30, texto=True)]
    n = len(cols)
    filas, todos = [], []
    for cod, lista in _agrupa(filas_bd, ext[0]):
        filas.append(Fila(tipo="grupo", cuenta=cod, nombre=_t(lista[0][ext[1]])))
        sub = []
        for cod2, items in _agrupa(lista, inte[0]):
            filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=cod2, nombre=_t(items[0][inte[1]]),
                              valores=[None] * n))
            bloque = [_fila_listado(a, [_fx(a["fecha_depreciacion"]) or _fx(a["fecha_compra"]), _fx(a["fecha_baja"]),
                                        float(a["valorml"] or 0), _t(a["nombreubica"]),
                                        _t(a["nombre_responsable"]) or _t(a["codresponsable"]) or "N/E"])
                      for a in items]
            filas += bloque + [_total(f"TOTAL {_t(items[0][inte[1]])}", bloque, n, (2,))]
            sub += bloque
        filas += [_total(f"TOTAL {_t(lista[0][ext[1]])}", sub, n, (2,), negrita=True), Fila()]
        todos += sub
    filas.append(_total("TOTALES", todos, n, (2,), negrita=True))
    return _cierra(b, "ARTÍCULOS NO DEPRECIABLES", f"AL {p.fecha_fin:%d/%m/%Y}" if p.fecha_fin else "TODAS LAS FECHAS",
                   cols, filas)


def dados_de_baja(p):
    """con_articulos_dados_de_baja: activos con su registro en acf_bajas, por fecha de baja."""
    b = Base(p)
    partes, params = _rangos(p, ("activo",))
    _fechas(p, "a.fecha_baja", partes, params)
    filas_bd = consulta(f"""
        SELECT a.*, IFNULL(u.nombre, '') AS nom_ubica, IFNULL(bj.descripcion, '') AS motivo
          FROM acf_activos a
          JOIN acf_bajas bj ON bj.codempresa = a.codempresa AND bj.keytra = a.keytra
          LEFT JOIN acf_ubica u ON u.codempresa = a.codempresa AND u.codubica = a.codubica
         WHERE a.codempresa = %s {' '.join(partes)}
         ORDER BY a.codactivo""", (p.codempresa, *params))
    _sin_datos(filas_bd, "activos dados de baja")
    cols = [Columna("MOTIVO", ancho=36, texto=True), Columna("FECHA BAJA", ancho=11, texto=True),
            Columna("FECHA COMPRA", ancho=11, texto=True), Columna("VALOR"), Columna("UBICACIÓN", ancho=22, texto=True),
            Columna("MARCA", ancho=14, texto=True), Columna("MODELO", ancho=14, texto=True)]
    filas = [_fila_listado(a, [_t(a["motivo"]), _fx(a["fecha_baja"]), _fx(a["fecha_compra"]), float(a["valorml"] or 0),
                               _t(a["nom_ubica"]), _t(a["marca"]), _t(a["modelo"])], nivel=1) for a in filas_bd]
    filas.append(_total("TOTALES", filas, len(cols), (3,), negrita=True))
    return _cierra(b, "ARTÍCULOS DADOS DE BAJA", _periodo_txt(p, "DE BAJA"), cols, filas)


def con_accesorios(p):
    """con_articulos_con_accesorios: cada activo que tiene accesorios (acf_consolidado) y debajo sus accesorios."""
    b = Base(p)
    partes, params = _rangos(p, ("activo",))
    _fechas(p, "a.fecha_compra", partes, params)
    filas_bd = consulta(f"""
        SELECT a.*, IFNULL(u.nombre, '') AS nom_ubica, IFNULL(r.nombre, '') AS nom_responsable
          FROM acf_activos a
          LEFT JOIN acf_ubica u ON u.codempresa = a.codempresa AND u.codubica = a.codubica
          LEFT JOIN acf_responsables r ON r.codempresa = a.codempresa AND r.codresponsable = a.codresponsable
         WHERE a.codempresa = %s
           AND EXISTS (SELECT 1 FROM acf_consolidado c WHERE c.codempresa = a.codempresa AND c.keytra_mae = a.keytra)
           {' '.join(partes)}
         ORDER BY a.codresponsable, a.codactivo""", (p.codempresa, *params))
    _sin_datos(filas_bd, "activos con accesorios")
    acc = _accesorios(p.codempresa)
    cols = [Columna("VALOR"), Columna("VALOR RESCATE"), Columna("FECHA COMPRA", ancho=11, texto=True),
            Columna("UBICACIÓN", ancho=22, texto=True), Columna("MARCA", ancho=14, texto=True),
            Columna("MODELO", ancho=14, texto=True), Columna("FECHA BAJA", ancho=11, texto=True),
            Columna("RESPONSABLE", ancho=30, texto=True)]

    def valores(a):
        return [float(a["valorml"] or 0), float(a["valor_rescate"] or 0), _fx(a["fecha_compra"]), _t(a["nom_ubica"]),
                _t(a["marca"]), _t(a["modelo"]), _fx(a["fecha_baja"]), _t(a["nom_responsable"])]
    filas, todos = [], []
    for a in filas_bd:
        principal = _fila_listado(a, valores(a), nivel=1)
        accesorios = [_fila_listado(x, valores(x), nivel=2, prefijo="  ↳ ") for x in acc.get(a["keytra"], [])]
        filas += [principal, Fila(tipo="grupo", nombre="   Accesorio(s)")] + accesorios + [Fila()]
        todos += [principal] + accesorios
    filas.append(_total("TOTALES", todos, len(cols), (0, 1), negrita=True))
    return _cierra(b, "ARTÍCULOS CON ACCESORIOS", _periodo_txt(p, "DE COMPRA"), cols, filas)


def _responsables_det(codempresa, codactivos):
    """Responsables vigentes de cada activo según su detalle por unidad (acf_activos_det sin fecha de baja)."""
    out = defaultdict(list)
    for lote in (codactivos[i:i + 500] for i in range(0, len(codactivos), 500)):
        for f in consulta(f"""SELECT DISTINCT d.codactivo, d.codresponsable FROM acf_activos_det d
                               WHERE d.codempresa = %s AND d.codactivo IN ({','.join(['%s'] * len(lote))})
                                 AND IFNULL(d.fecha_baja, '0000-00-00') = '0000-00-00'
                               ORDER BY d.codresponsable""", (codempresa, *lote)):
            out[_t(f["codactivo"])].append(_t(f["codresponsable"]))
    return out


def inventario_aecid(p):
    """
    con_inventario_aecid: inventario de bienes muebles en buen estado (formato AECID): número, nombre y
    código, cantidad, estado, valor, fecha de compra, responsables vigentes del detalle y serie. VFP hacía
    una consulta de responsables por activo; aquí una para todos.
    """
    b = Base(p)
    partes, params = _rangos(p, ("activo",))
    _fechas(p, "a.fecha_compra", partes, params)
    if p.codresponsable:
        partes.append("""AND EXISTS (SELECT 1 FROM acf_activos_det d WHERE d.codempresa = a.codempresa
                         AND d.codactivo = a.codactivo AND d.codresponsable = %s)""")
        params.append(p.codresponsable)
    filas_bd = consulta(f"""SELECT a.* FROM acf_activos a WHERE a.codempresa = %s {' '.join(partes)}
                            ORDER BY a.codactivo""", (p.codempresa, *params))
    _sin_datos(filas_bd)
    resp = _responsables_det(p.codempresa, [_t(a["codactivo"]) for a in filas_bd])
    cols = [Columna("CANTIDAD", ancho=9, entero=True), Columna("ESTADO", ancho=7, texto=True), Columna("VALOR"),
            Columna("FECHA COMPRA", ancho=11, texto=True), Columna("RESPONSABLE", ancho=24, texto=True),
            Columna("SERIE", ancho=18, texto=True)]
    filas = []
    for i, a in enumerate(filas_bd, 1):
        filas.append(Fila(tipo="cuenta", nivel=1, cuenta=str(i), nombre=f"{_t(a['nombre'])} ({_t(a['codactivo'])})",
                          valores=[float(a["cantidad"] or 0), "B.E.", float(a["valorml"] or 0), _fx(a["fecha_compra"]),
                                   ", ".join(resp.get(_t(a["codactivo"]), [])), _t(a["serie"])]))
    filas.append(_total("TOTALES", filas, len(cols), (0, 2), negrita=True))
    return _cierra(b, "INVENTARIO DE BIENES MUEBLES EN BUEN ESTADO", _periodo_txt(p, "DE COMPRA"), cols, filas,
                   enc_a=("No.", 6), enc_b=("DESCRIPCIÓN (CÓDIGO)", 46))


def inventario_baja_aecid(p):
    """
    con_inventario_debaja_aecid: cada unidad del detalle dada de baja (acf_activos_det con fecha de baja),
    con su valor, responsable, fecha y motivo. VFP rotulaba el estado «B.E.» (buen estado) también en este
    listado de bajas; aquí dice «BAJA».
    """
    b = Base(p)
    partes, params = _rangos(p, ("activo",))
    _fechas(p, "a.fecha_compra", partes, params)
    filas_bd = consulta(f"""
        SELECT a.codactivo, a.nombre, a.cantidad, a.fecha_compra, a.serie, d.linea, d.valorml AS valor_det,
               d.fecha_baja AS fecha_baja_det, d.motivo_debaja, d.codresponsable AS resp_det
          FROM acf_activos a
          JOIN acf_activos_det d ON d.codempresa = a.codempresa AND d.codactivo = a.codactivo
               AND IFNULL(d.fecha_baja, '0000-00-00') <> '0000-00-00'
         WHERE a.codempresa = %s {' '.join(partes)}
         ORDER BY a.codactivo, d.linea""", (p.codempresa, *params))
    _sin_datos(filas_bd, "unidades dadas de baja")
    cols = [Columna("CANTIDAD", ancho=9, entero=True), Columna("ESTADO", ancho=7, texto=True), Columna("VALOR"),
            Columna("FECHA COMPRA", ancho=11, texto=True), Columna("RESPONSABLE", ancho=18, texto=True),
            Columna("FECHA BAJA", ancho=11, texto=True), Columna("MOTIVO", ancho=30, texto=True),
            Columna("SERIE", ancho=18, texto=True)]
    filas = []
    for i, a in enumerate(filas_bd, 1):
        filas.append(Fila(tipo="cuenta", nivel=1, cuenta=str(i), nombre=f"{_t(a['nombre'])} ({_t(a['codactivo'])})",
                          valores=[float(a["cantidad"] or 0), "BAJA", float(a["valor_det"] or 0), _fx(a["fecha_compra"]),
                                   _t(a["resp_det"]), _fx(a["fecha_baja_det"]), _t(a["motivo_debaja"]), _t(a["serie"])]))
    filas.append(_total("TOTALES", filas, len(cols), (2,), negrita=True))
    return _cierra(b, "INVENTARIO DE BIENES MUEBLES DE BAJA", _periodo_txt(p, "DE COMPRA"), cols, filas,
                   enc_a=("No.", 6), enc_b=("DESCRIPCIÓN (CÓDIGO)", 46))


def ficha_responsabilidad_aecid(p):
    """con_inventario_ficha_responsa_aecid: unidades del detalle a cargo de un responsable (cantidad y valor por activo)."""
    b = Base(p)
    partes, params = _rangos(p, ("activo",))
    _fechas(p, "d.fecha_compra", partes, params)
    filas_bd = consulta(f"""
        SELECT a.codactivo, a.nombre, SUM(d.cantidad) AS cantidad, SUM(d.valorml) AS valorml,
               MIN(d.fecha_compra) AS fecha_compra, MAX(r.nombre) AS nom_responsable
          FROM acf_activos a
          JOIN acf_activos_det d ON d.codempresa = a.codempresa AND d.codactivo = a.codactivo AND d.codresponsable = %s
          JOIN acf_responsables r ON r.codempresa = d.codempresa AND r.codresponsable = d.codresponsable
         WHERE a.codempresa = %s {' '.join(partes)}
         GROUP BY a.codactivo, a.nombre
         ORDER BY a.codactivo""", (p.codresponsable, p.codempresa, *params))
    _sin_datos(filas_bd)
    cols = [Columna("CANTIDAD", ancho=9, entero=True), Columna("VALOR"), Columna("FECHA COMPRA", ancho=11, texto=True)]
    filas = [_fila_listado(a, [float(a["cantidad"] or 0), float(a["valorml"] or 0), _fx(a["fecha_compra"])], nivel=1)
             for a in filas_bd]
    filas.append(_total("TOTALES", filas, len(cols), (0, 1), negrita=True))
    filas += [Fila(), Fila(tipo="grupo", nombre="Firma del responsable: ______________________________")]
    return _cierra(b, "FICHA DE RESPONSABILIDAD (AECID)",
                   f"RESPONSABLE: {_t(filas_bd[0]['nom_responsable'])} ({p.codresponsable}) · {date.today():%d/%m/%Y}",
                   cols, filas)
