"""
Reportes del menú 22 'Centros de Costo (C.C.)' de vfmenuc.

Mismo motor que los estados financieros: una consulta de movimientos (ahora separada
por centro de costo y con filtros) y el armado de filas replicando el orden de VFP.
"""
from . import contexto as ctx
from .contexto import ErrorReporte, meses_entre
from .modelo import Columna, Fila
from .motor import DEUDORAS_BG, ORDEN_BG, ORDEN_ER, arma_bg, arma_er, finaliza, prefijos_orden, selecciona
from .reportes import GRUPOS_RESULTADO, _Balance, _Resultados, _sin_datos
from .saldos import FiltroMovs, Movimientos


def _etiqueta_centro(catalogo, codigo):
    centro = catalogo.get(codigo)
    if centro:
        return centro.etiqueta
    return f"({codigo})" if codigo else "(SIN CENTRO)"


class _ResultadosCC(_Resultados):
    def catalogo(self):
        return ctx.centros(self.emp.codempresa, self.corte.periodo.codperiodo, self.p.dim)

    def agrupar_en_mayor(self, catalogo):
        """Opción 'Mostrar solo centros de nivel mayor': IF(centro_mayor = '', codcc, centro_mayor)."""
        self.movs.agrupa_centros({c.codigo: c.mayor for c in catalogo.values() if c.mayor})


def estado_resultados_por_centro(p):
    """con_er_cc_xls: una columna por centro de costo + TOTAL."""
    r = _ResultadosCC(p)
    c = r.corte
    catalogo = r.catalogo()
    saldo, activas = r.preparar([(c.ini, c.fin)], FiltroMovs(dim=p.dim, por_cc=True))
    if p.mayor:
        r.agrupar_en_mayor(catalogo)
    centros = sorted(r.movs.centros(c.ini, c.fin))
    rango = c.rango(p.acumulado)
    columnas = [Columna(_etiqueta_centro(catalogo, cc), ancho=16) for cc in centros]
    columnas.append(Columna("TOTAL", formula=("suma", 0, len(centros) - 1)))

    def valores(cta):
        if cta.codigo not in activas:
            return None
        return [saldo(cta.codigo, *rango, cc=cc) if r.movs.tiene(cta.codigo, c.ini, c.fin, cc) else None
                for cc in centros] + [None]

    lineas = selecciona(r.nomenclatura(), ORDEN_ER, r.nom, None if p.incluir_todas else activas)
    filas = arma_er(lineas, r.nom, r.nivel, columnas, valores, r.signo_resultado)
    return r.reporte("ESTADO DE RESULTADOS - POR CENTRO DE COSTO", c.leyenda(p.acumulado), columnas, filas)


def balance_general_por_centro(p):
    """
    con_bg_ci_xls: Balance General con una columna por centro + TOTAL. La utilidad de cada centro
    (ingresos - egresos del centro) se suma a la cuenta de ganancia del ejercicio en su columna.
    Una consulta de movimientos (VFP: la vista v_saldos_cuenta tres veces y montos como texto).
    """
    b = _Balance(p)
    c = b.corte
    rango = c.rango(p.acumulado)
    movs = b.movimientos(*DEUDORAS_BG, "pasivo", "capital", "regularizacionpasivo", *GRUPOS_RESULTADO,
                         filtro=FiltroMovs(dim=p.dim, por_cc=True))
    centros = sorted(movs.centros(c.ini, c.fin))
    _sin_datos(centros)
    catalogo = ctx.centros(b.emp.codempresa, c.periodo.codperiodo, p.dim)
    deudoras = set(b.nom.prefijos(*DEUDORAS_BG))
    ing, egr = set(b.nom.prefijos("ingresos", "otrosingresos")), set(b.nom.prefijos("costos", "gastos", "egresos",
                                                                                     "otrosegresos"))

    def utilidad_centro(cc):
        return round(sum(movs.neto(cta, *rango, False, cc) for cta in movs.datos if cta[: b.logn] in ing)
                     - sum(movs.neto(cta, *rango, True, cc) for cta in movs.datos if cta[: b.logn] in egr), 2)

    utilidades = {cc: utilidad_centro(cc) for cc in centros}
    ganancia = c.periodo.gananciaejercicio
    activas = b.activas(movs, ORDEN_BG, [(c.ini, c.fin)])
    if ganancia and any(utilidades.values()):
        activas.add(ganancia)

    def valores(cta):
        if cta.codigo not in activas:
            return None
        fila = []
        for cc in centros:
            v = movs.neto(cta.codigo, *rango, cta.codigo[: b.logn] in deudoras, cc)
            if cta.codigo == ganancia:
                v = round(v + utilidades[cc], 2)
            fila.append(v if v or movs.tiene(cta.codigo, c.ini, c.fin, cc) else None)
        return fila + [None]

    columnas = [Columna(_etiqueta_centro(catalogo, cc), ancho=16) for cc in centros]
    columnas.append(Columna("TOTAL", formula=("suma", 0, len(centros) - 1)))
    lineas = selecciona(b.nomenclatura(), ORDEN_BG, b.nom, None if p.incluir_todas else activas)
    filas = arma_bg(lineas, b.nom, b.nivel, columnas, valores)
    return b.reporte("BALANCE GENERAL - POR CENTRO DE COSTO", c.leyenda(p.acumulado), columnas, filas)


def estado_resultados_centro_individual(p):
    """con_er_cc_individual_xls: ER de un rango de centros (inicio..fin)."""
    r = _ResultadosCC(p)
    c = r.corte
    catalogo = r.catalogo()
    saldo, activas = r.preparar([(c.ini, c.fin)], FiltroMovs(dim=p.dim, cc_desde=p.cc_desde, cc_hasta=p.cc_hasta))
    rango = c.rango(p.acumulado)
    nombre_ini = catalogo[p.cc_desde].nombre if p.cc_desde in catalogo else ""
    nombre_fin = catalogo[p.cc_hasta].nombre if p.cc_hasta in catalogo else ""
    columnas = [Columna("")]
    lineas = selecciona(r.nomenclatura(), ORDEN_ER, r.nom, activas)
    filas = arma_er(lineas, r.nom, r.nivel, columnas,
                    lambda cta: [saldo(cta.codigo, *rango)] if cta.codigo in activas else None,
                    r.signo_resultado)
    rep = r.reporte("ESTADO DE RESULTADOS POR CENTRO DE COSTO INDIVIDUAL", c.leyenda(p.acumulado), columnas, filas)
    rep.titulo = f"{rep.titulo} [{nombre_ini}] - [{nombre_fin}]"
    return rep


def estado_resultados_subcentros(p):
    """
    con_er_cc_sub_xls: columnas por centro agrupadas por cuenta de acumulación.
    Corrige VFP, que ordenaba los títulos por acumulación pero colocaba los valores
    por código de centro (las columnas podían quedar desalineadas).
    """
    r = _ResultadosCC(p)
    c = r.corte
    catalogo = r.catalogo()
    saldo, activas = r.preparar([(c.ini, c.fin)], FiltroMovs(dim=p.dim, por_cc=True))

    def acumulacion(cc):
        centro = catalogo.get(cc)
        return (centro.acumulacion if centro else "") or "N/A"

    centros = sorted(r.movs.centros(c.ini, c.fin), key=lambda cc: (acumulacion(cc), cc))
    rango = c.rango(p.acumulado)
    columnas = [Columna(_etiqueta_centro(catalogo, cc), acumulacion(cc), ancho=16) for cc in centros]
    columnas.append(Columna("TOTAL", formula=("suma", 0, len(centros) - 1)))

    def valores(cta):
        if cta.codigo not in activas:
            return None
        return [saldo(cta.codigo, *rango, cc=cc) if r.movs.tiene(cta.codigo, c.ini, c.fin, cc) else None
                for cc in centros] + [None]

    lineas = selecciona(r.nomenclatura(), ORDEN_ER, r.nom, activas)
    filas = arma_er(lineas, r.nom, r.nivel, columnas, valores, r.signo_resultado)
    return r.reporte("ESTADO DE RESULTADO POR SUB-CENTRO DE COSTO", c.leyenda(p.acumulado), columnas, filas)


def estado_resultados_cuenta_mensual_centro(p):
    """con_er_cc_comp_anual_xls: ER por cuenta, una columna por mes, de un centro (o todos)."""
    r = _ResultadosCC(p)
    c = r.corte
    # por C.C. se elige un centro; por C.I. (con_er_ci_comp_anual_xls) un rango inicio..fin
    filtro = FiltroMovs(dim=p.dim, centros=[p.centro] if p.centro else [], cc_desde=p.cc_desde, cc_hasta=p.cc_hasta)
    saldo, activas = r.preparar([(c.ini, c.fin)], filtro)
    meses = meses_entre(c.periodo.fecini, c.fecha)
    columnas = [Columna(f"({m // 100}-{m % 100:02d})") for m in meses]
    columnas.append(Columna("TOTAL", formula=("suma", 0, len(meses) - 1)))
    lineas = selecciona(r.nomenclatura(), ORDEN_ER, r.nom, activas)
    filas = arma_er(lineas, r.nom, r.nivel, columnas,
                    lambda cta: [saldo(cta.codigo, m, m) for m in meses] + [None]
                    if cta.codigo in activas else None,
                    r.signo_resultado)
    rep = r.reporte("ESTADO DE RESULTADOS POR CUENTA - COMPARATIVO ANUAL - CENTRO DE COSTO",
                    c.leyenda(p.acumulado), columnas, filas)
    if p.centro:
        rep.titulo = f"{rep.titulo}: {_etiqueta_centro(r.catalogo(), p.centro)}"
    return rep


def estado_resultados_centros_por_mes(p):
    """
    con_er_cc_comparativo_anual_xls: resultado (ingresos - egresos) de cada centro por mes.
    Corrige VFP, que tenía fijo el periodo '2020-01' para la nomenclatura y los centros.
    """
    r = _ResultadosCC(p)
    c = r.corte
    catalogo = r.catalogo()
    r.preparar([(c.ini, c.fin)], FiltroMovs(dim=p.dim, por_cc=True))
    if p.mayor:
        r.agrupar_en_mayor(catalogo)
    # VFP usa INNER JOIN con empcc: solo centros definidos en el catálogo
    centros = sorted(cc for cc in r.movs.centros(c.ini, c.fin) if cc in catalogo)
    if not centros:
        raise ErrorReporte("No hay movimientos con centro de costo en el periodo.")
    meses = meses_entre(c.periodo.fecini, c.fecha)
    columnas = [Columna(ctx_mes(m)) for m in meses]
    columnas.append(Columna("TOTAL", formula=("suma", 0, len(meses) - 1)))
    cuentas = list(r.movs.datos)

    filas = []
    for cc in centros:
        valores = [round(sum(r.movs.neto(cta, m, m, False, cc) for cta in cuentas), 2) for m in meses] + [None]
        filas.append(Fila(tipo="cuenta", codigo=cc, cuenta=cc, nombre=catalogo[cc].nombre, nivel=1, valores=valores))
    total = Fila(tipo="total", nombre="TOTAL", negrita=True, rango=(filas[0], filas[-1]))
    filas += [Fila(), total]
    finaliza(filas, columnas)
    return r.reporte("ESTADO DE RESULTADOS - POR CENTRO DE COSTO - COMPARATIVO ANUAL",
                     c.leyenda(True), columnas, filas)


def ctx_mes(ym):
    return ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")[ym % 100 - 1] + f" {ym // 100}"


def balance_saldos_por_centro(p):
    """con_balance_saldo_por_cc_xls: saldo inicial, movimiento y saldo actual por centro y cuenta."""
    r = _ResultadosCC(p)
    c = r.corte
    catalogo = r.catalogo()
    filtro = FiltroMovs(dim=p.dim, por_cc=True, cc_desde=p.cc_desde, cc_hasta=p.cc_hasta,
                        cuenta_desde=p.cuenta_desde, cuenta_hasta=p.cuenta_hasta)
    movs = Movimientos(r.emp.codempresa, c.periodo.fecini, c.fecha, None, r.logn, filtro)
    meses = max(1, min(p.meses or 1, 12))
    mes_ini = max(1, p.mes - meses + 1)
    ini_mov = p.anio * 100 + mes_ini
    nombres = {cta.codigo: cta.nombre for cta in r.nomenclatura()}

    columnas = [Columna("SALDO INICIAL", "DEBE"), Columna("", "HABER"),
                Columna("MOVIMIENTO DEL MES", "DEBE"), Columna("", "HABER"), Columna("SALDO ACTUAL")]
    filas, cuentas_fila = [], []
    for cc in sorted({cc for por_cc in movs.datos.values() for cc in por_cc if cc in catalogo}):
        bloque = []
        for cuenta in sorted(movs.datos):
            if cc not in movs.datos[cuenta]:
                continue
            anterior = movs.neto(cuenta, c.ini, ini_mov - 1, True, cc)     # cargo - abono
            actual = movs.neto(cuenta, ini_mov, c.fin, True, cc)
            if p.solo_movimiento and actual == 0:
                continue
            valores = [anterior if anterior > 0 else None, abs(anterior) if anterior <= 0 else None,
                       actual if actual > 0 else None, abs(actual) if actual <= 0 else None,
                       round(anterior + actual, 2)]
            bloque.append(Fila(tipo="cuenta", codigo=cuenta, cuenta=r.nom.formatea(cuenta),
                               nombre=nombres.get(cuenta, ""), nivel=1, valores=valores))
        if not bloque:
            continue
        filas.append(Fila(tipo="grupo", cuenta=cc, nombre=catalogo[cc].nombre))
        filas += bloque
        filas.append(Fila(tipo="total", nombre="TOTAL CENTRO DE COSTO", negrita=True, rango=(bloque[0], bloque[-1])))
        filas.append(Fila())
        cuentas_fila += bloque
    _sin_datos(cuentas_fila)
    filas.append(Fila(tipo="total", nombre="TOTALES", negrita=True, rango=(cuentas_fila[0], cuentas_fila[-1])))
    finaliza(filas, columnas)

    if meses == 1:
        sub = "PERIODO: " + c.fecha.strftime("%d/%m/%Y")
    else:
        sub = f"DEL 01/{mes_ini:02d}/{p.anio} AL {c.fecha.strftime('%d/%m/%Y')}"
    rep = r.reporte("BALANCE DE SALDOS POR CENTRO DE COSTO", sub, columnas, filas)
    rep.fecha_hora = None
    return rep


def estado_resultados_vs_presupuesto_centro(p):
    """con_presupuesto_cc_er_xls: ejecución presupuestaria (E.R.) por centros seleccionados."""
    r = _ResultadosCC(p)
    c = r.corte
    filtro = FiltroMovs(dim=p.dim, centros=list(p.centros))
    grupos = [g for _, gs in ORDEN_ER for g in gs]
    movs = r.movimientos(*grupos, filtro=filtro)
    pres = ctx.presupuesto(r.emp.codempresa, c.periodo.codperiodo, p.centros, p.dim)
    n_mes = len(meses_entre(c.periodo.fecini, c.fecha))       # meses transcurridos del ejercicio
    deudoras = set(r.nom.prefijos(*r.deudoras))
    prefijos = prefijos_orden(ORDEN_ER, r.nom)
    nomencla = r.nomenclatura()

    valores, activas = {}, set()
    for cta in nomencla:
        if not cta.ultimo or cta.codigo[: r.logn] not in prefijos:
            continue
        deud = cta.codigo[: r.logn] in deudoras
        mensual = pres.get(cta.codigo, [0.0] * 12)
        v = [mensual[n_mes - 1], movs.neto(cta.codigo, c.fin, c.fin, deud), None, None,
             round(sum(mensual[:n_mes]), 2), movs.neto(cta.codigo, c.ini, c.fin, deud), None, None]
        if any(v[i] for i in (0, 1, 4, 5)):
            valores[cta.codigo] = v
            activas.add(cta.codigo)
    activas |= {cta for cta in pres if cta[: r.logn] in prefijos}
    _sin_datos(valores)

    columnas = [
        Columna("PRESUPUESTO", "MES"), Columna("REAL", "MES"),
        Columna("DIFERENCIA", "MES", ("resta", 0, 1)), Columna("%", "MES", ("ratio", 2, 0), ancho=11),
        Columna("PRESUPUESTO", "ACUMULADO"), Columna("REAL", "ACUMULADO"),
        Columna("DIFERENCIA", "ACUMULADO", ("resta", 4, 5)), Columna("%", "ACUMULADO", ("ratio", 6, 4), ancho=11),
    ]
    lineas = selecciona(nomencla, ORDEN_ER, r.nom, activas)
    filas = arma_er(lineas, r.nom, r.nivel, columnas, lambda cta: valores.get(cta.codigo), r.signo_resultado)
    sub = c.leyenda(p.acumulado)
    if p.centros:
        sub += " · Centro(s): " + ", ".join(p.centros)
    return r.reporte("EJECUCION PRESUPUESTARIA (E.R.) POR CENTRO DE COSTO", sub, columnas, filas)
