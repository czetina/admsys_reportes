"""
Los 13 reportes del menú 20 'Estados Financieros' de vfmenuc.

Cada función recibe Parametros y devuelve un modelo.Reporte. Todas hacen UNA sola
consulta de movimientos (saldos.Movimientos) y calculan el resto en memoria.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import contexto as ctx
from .contexto import ErrorReporte, fin_de_mes, meses_entre, mes_anterior, ym
from .modelo import Columna, Reporte
from .motor import (
    ACREEDORAS_ER, DEUDORAS_BG, DEUDORAS_ER, ORDEN_BG, ORDEN_CTAS_ORDEN, ORDEN_ER,
    acumula_por_mayor, arma_bg, arma_bg_columnas, arma_er, arma_er_columnas,
    prefijos_orden, selecciona,
)
from .saldos import Movimientos, utilidad

GRUPOS_RESULTADO = ("ingresos", "otrosingresos", "costos", "gastos", "egresos", "otrosegresos")


@dataclass
class Parametros:
    codempresa: str
    anio: int
    mes: int
    nombre: str = ""
    nivel: int | None = None
    acumulado: bool = True
    incluir_todas: bool = False
    no_saldo_cero: bool = True
    fecha_hora: bool = True
    anio2: int | None = None
    mes2: int | None = None
    # Centros de costo / rangos (grupo 22 del menú)
    mayor: bool = False
    solo_movimiento: bool = False
    centro: str = ""
    centros: list = field(default_factory=list)
    cc_desde: str = ""
    cc_hasta: str = ""
    cuenta_desde: str = ""
    cuenta_hasta: str = ""
    meses: int = 12
    detallar: bool = True     # presupuesto: un centro por hoja + RESUMEN
    # Integración de cuentas (menú 35)
    anio_ini: int | None = None
    mes_ini: int | None = None
    directorio_desde: str = ""
    directorio_hasta: str = ""
    orden: int = 1
    mostrar_llaves: bool = False
    mostrar_cxp: bool = False
    incluye_desactualizados: bool = False
    incluye_apertura: bool = False
    genera_td: bool = True
    # Libros (diario, mayor, estado de cuenta, diarios para auditores)
    presentacion: int = 4     # diario: 1 fecha+tipo, 2 fecha, 3 tipo sin C.C., 4 por transacción, 5 tipo
    folio: int = 0
    incluye_concepto: bool = True
    actualizados: bool = True
    anio_completo: bool = False
    archivo_texto: bool = False
    ver_detallado: bool = False
    resolucion: str = ""          # libros fiscales: resolución de la SAT (encabezado de cada hoja)
    libro_fiscal: bool = False   # libros fiscales (menú 30): hoja «sin movimientos», diario con centros
    # Cuentas corrientes (CXC / CXP)
    sistema: str = ""
    fecha_ini: object = None
    fecha_fin: object = None
    cuenta_aux: str = ""
    no_contrato: int = 0
    codmoneda: str = ""
    codaca: str = ""
    clasificaciones: list = field(default_factory=list)
    tipo_saldos: int = 1          # 1 deudores y acreedores, 2 solo deudores, 3 solo acreedores
    fecha_base: int = 1           # 1 fecha de movimiento, 2 fecha de vencimiento / a trabajar
    orden_cc: int = 1             # 1 nombre, 2 código de directorio
    intervalo: int = 30           # días por columna de antigüedad
    columnas_antiguedad: int = 5
    mostrar_me: bool = False      # referencia de valores en moneda extranjera
    mostrar_proveedor: bool = False
    mostrar_contable: bool = False
    mostrar_actualizado: bool = False
    codtipo: str = ""
    docref: str = ""
    estado_doc: str = "todos"
    # Auxiliares de IVA
    sucursal: int | None = None   # None = todas; ventas: 0 = documentos sin sucursal
    inc_det_cuadre: bool = True
    incluye_info_adicional: bool = True
    incluye_des_contable: bool = True
    mostrar_ret_fe: bool = False
    resumen: bool = False
    codsistema_ref: str = ""
    codctachq: str = ""
    tipos_ref: str = ""
    # Facturación
    tipo_desde: str = ""
    tipo_hasta: str = ""
    estado_fac: str = "todas"     # todas | pagadas | pendientes
    orden_fac: int = 1            # 1 tipo/serie/número, 2 fecha ascendente, 3 fecha descendente (consecutivo: 2 = llave)
    detallado: bool = False
    detalle: bool = True
    codcc: str = ""
    # Activos fijos
    activo_desde: str = ""
    activo_hasta: str = ""
    ubica_desde: str = ""
    ubica_hasta: str = ""
    tipoact_desde: str = ""
    tipoact_hasta: str = ""
    depto_desde: str = ""
    depto_hasta: str = ""
    responsable_desde: str = ""
    responsable_hasta: str = ""
    solo_electronico: bool = False
    ocultar_bajas: bool = False
    orden_activos: int = 1        # 1 departamento y tipo de activo, 2 tipo de activo y departamento
    dim: str = "cc"               # dimensión de los reportes por centro: cc / ci / ca (Definicion.dimension)
    codci: str = ""
    codca: str = ""
    ver_moneda_local: bool = False
    incluye_anulados: bool = False
    pendiente_numero: bool = False
    desglosa_medios: bool = True
    # Cheques (caja y bancos)
    coduser: str = ""             # usuario de la sesión (acceso a cuentas con control de usuarios)
    codtipocta: str = ""
    estado_chq: str = "si"        # si: autorizados / entregados · pendientes
    usuario_chq: str = ""
    numero_desde: int | None = None
    numero_hasta: int | None = None
    incluye_aplicacion: bool = False
    mostrar_adicionales: bool = False
    conci_cuadratica: bool = False
    ocultar_conciliados: bool = False
    tasa_cambio: float | None = None
    meses_caducado: int = 6
    incluye_pend_numero: bool = False
    incluye_aclarar: bool = False
    resumen_conci: bool = False
    detalle_no_autorizados: bool = False
    detalle_no_entregados: bool = False
    detalle_caducados: bool = False
    mostrar_resumen_bancos: bool = True
    serie: str = ""
    serie_desde: str = ""
    serie_hasta: str = ""
    solo_sin_numero: bool = False
    solo_pendientes: bool = False
    estado_reg: str = "todos"     # correlativo general: todos | actualizados | desactualizados | anulados | ...
    valor_doc: float | None = None
    mostrar_creacion: bool = False
    proyectar_por: str = "mes"    # flujo proyectado: semana | mes | anio
    primer_dia: int = 0           # 0 = lunes ... 6 = domingo
    mostrar_por: str = "directorio"   # directorio | clasificacion | aca


@dataclass
class Corte:
    """Un año-mes de corte con su ejercicio fiscal."""
    periodo: ctx.Periodo
    fecha: object
    ini: int   # AAAAMM inicio de ejercicio
    fin: int   # AAAAMM de corte

    def rango(self, acumulado):
        return (self.ini, self.fin) if acumulado else (self.fin, self.fin)

    def leyenda(self, acumulado):
        if acumulado:
            return "AL " + self.fecha.strftime("%d/%m/%Y")
        return f"DEL MES [{self.fecha.month:02d}/{self.fecha.year}]"


def _corte(codempresa, anio, mes):
    per = ctx.periodo(codempresa, anio, mes)
    fecha = fin_de_mes(anio, mes)
    return Corte(per, fecha, ym(per.fecini), ym(fecha))


class Base:
    """Contexto común: empresa, periodo, definición de nomenclatura y nivel."""

    def __init__(self, p, *cortes_extra):
        self.p = p
        self.emp = ctx.empresa(p.codempresa)
        self.corte = _corte(p.codempresa, p.anio, p.mes)
        self.cortes = [self.corte] + [_corte(p.codempresa, a, m) for a, m in cortes_extra]
        self.nom = ctx.defnomencla(self.corte.periodo.codnomencla)
        self.nivel = max(1, min(p.nivel or self.nom.niveles, self.nom.niveles))
        self.logn = self.nom.logn

    def movimientos(self, *grupos, filtro=None):
        desde = min(c.periodo.fecini for c in self.cortes)
        hasta = max(c.fecha for c in self.cortes)
        return Movimientos(self.emp.codempresa, desde, hasta, self.nom.prefijos(*grupos), self.logn, filtro)

    def nomenclatura(self):
        codigos = list(dict.fromkeys(c.periodo.codperiodo for c in self.cortes))
        return ctx.nomenclatura(self.emp.codempresa, *codigos)

    def activas(self, movs, orden_def, rangos):
        prefijos = prefijos_orden(orden_def, self.nom)
        out = set()
        for desde, hasta in rangos:
            out |= {c for c in movs.cuentas(desde, hasta) if c[: self.logn] in prefijos}
        return out

    def reporte(self, titulo_def, subtitulo, columnas, filas, usar_formulas=True, sufijo=""):
        titulo = (self.p.nombre or titulo_def).strip()
        archivo = re.sub(r"[^\w\- ]", "", f"{titulo} {self.emp.codempresa} {self.p.anio}-{self.p.mes:02d}{sufijo}")
        return Reporte(
            titulo=titulo, subtitulo=subtitulo, empresa=self.emp, columnas=columnas, filas=filas,
            nombre_archivo=archivo.strip() + ".xlsx", usar_formulas=usar_formulas,
            fecha_hora=datetime.now() if self.p.fecha_hora else None,
        )


def _sin_datos(activas):
    if not activas:
        raise ErrorReporte("No existen datos para los parámetros indicados.")


# ------------------------------------------------------------------ Balance General

class _Balance(Base):
    def saldo_fn(self, movs, rango, utilidad_valor, ganancia):
        deudoras = set(self.nom.prefijos(*DEUDORAS_BG))

        def saldo(cuenta):
            v = movs.neto(cuenta, rango[0], rango[1], cuenta[: self.logn] in deudoras)
            if cuenta == ganancia:
                v = round(v + utilidad_valor, 2)
            return v
        return saldo

    def preparar(self, rangos):
        """Movimientos, funciones de saldo por columna y cuentas activas (incluye utilidad)."""
        movs = self.movimientos(*DEUDORAS_BG, "pasivo", "capital", "regularizacionpasivo", *GRUPOS_RESULTADO)
        funciones, activas = [], set()
        for corte, rango in rangos:
            u = utilidad(movs, self.nom, *rango)
            funciones.append(self.saldo_fn(movs, rango, u, corte.periodo.gananciaejercicio))
            activas |= self.activas(movs, ORDEN_BG, [(corte.ini, corte.fin)])
            if u and corte.periodo.gananciaejercicio:
                activas.add(corte.periodo.gananciaejercicio)
        _sin_datos(activas)
        return funciones, activas


def balance_general(p):
    """con_bg_xls / con_bg (Crystal)."""
    b = _Balance(p)
    # Libro de Estados Financieros: «Acumulado» desmarcado = solo los movimientos del mes
    acumulado = p.acumulado or not p.libro_fiscal
    rango = (b.corte.ini, b.corte.fin) if acumulado else (b.corte.fin, b.corte.fin)
    (saldo,), activas = b.preparar([(b.corte, rango)])
    if p.no_saldo_cero:
        activas = {c for c in activas if saldo(c) != 0}
    lineas = selecciona(b.nomenclatura(), ORDEN_BG, b.nom, None if p.incluir_todas else activas)
    columnas = [Columna("")]
    filas = arma_bg(lineas, b.nom, b.nivel, columnas,
                    lambda c: [saldo(c.codigo)] if c.codigo in activas else None)
    return b.reporte("BALANCE GENERAL", b.corte.leyenda(acumulado), columnas, filas)


def balance_general_columnas(p):
    """con_bg_xls-01: saldos en una columna por nivel."""
    b = _Balance(p)
    (saldo,), activas = b.preparar([(b.corte, (b.corte.ini, b.corte.fin))])
    if p.no_saldo_cero:
        activas = {c for c in activas if saldo(c) != 0}
    nomencla = b.nomenclatura()
    lineas = selecciona(nomencla, ORDEN_BG, b.nom, None if p.incluir_todas else activas)
    saldos = acumula_por_mayor({c: saldo(c) for c in activas}, nomencla)
    columnas = [Columna(f"NIVEL {b.nivel - j}") for j in range(b.nivel)]
    filas = arma_bg_columnas(lineas, b.nom, b.nivel, saldos)
    return b.reporte("BALANCE GENERAL", b.corte.leyenda(True), columnas, filas, usar_formulas=False)


def balance_comparativo_mes_anterior(p):
    """con_bg_comparativo_xls."""
    b = _Balance(p)
    c = b.corte
    a, m = mes_anterior(p.anio, p.mes)
    ant = a * 100 + m
    rangos = [(c, (ant, ant)), (c, (c.fin, c.fin)), (c, (c.ini, ant)), (c, (c.ini, c.fin))]
    funciones, activas = b.preparar(rangos)
    lineas = selecciona(b.nomenclatura(), ORDEN_BG, b.nom, None if p.incluir_todas else activas)
    columnas = [
        Columna("MES ANTERIOR"), Columna("MES ACTUAL"),
        Columna("VARIACION", "MONTO", ("resta", 1, 0)), Columna("", "%", ("pct", 2, 0), ancho=11),
        Columna("ACUMULADO ANTERIOR"), Columna("ACUMULADO ACTUAL"),
        Columna("VARIACION", "MONTO", ("resta", 5, 4)), Columna("", "%", ("pct", 6, 4), ancho=11),
    ]
    orden_vals = (0, 1, None, None, 2, 3, None, None)

    def valores(cta):
        if cta.codigo not in activas:
            return None
        return [None if i is None else funciones[i](cta.codigo) for i in orden_vals]

    filas = arma_bg(lineas, b.nom, b.nivel, columnas, valores)
    return b.reporte("BALANCE GENERAL COMPARATIVO MES ANTERIOR", c.leyenda(True), columnas, filas)


def balance_comparativo_periodo(p):
    """con_bg_comparativo_periodo_xls: periodo vs. periodo comparativo (por defecto año anterior)."""
    b = _Balance(p, (p.anio2, p.mes2))
    c0, c1 = b.cortes
    funciones, activas = b.preparar([(c0, c0.rango(p.acumulado)), (c1, c1.rango(p.acumulado))])
    lineas = selecciona(b.nomenclatura(), ORDEN_BG, b.nom, None if p.incluir_todas else activas)
    columnas = [
        Columna(c0.leyenda(p.acumulado)), Columna(c1.leyenda(p.acumulado)),
        Columna("VARIACION", "MONTO", ("resta", 1, 0)), Columna("", "%", ("pct", 2, 0), ancho=11),
    ]
    filas = arma_bg(lineas, b.nom, b.nivel, columnas,
                    lambda cta: [funciones[0](cta.codigo), funciones[1](cta.codigo), None, None]
                    if cta.codigo in activas else None)
    return b.reporte("BALANCE GENERAL COMPARATIVO PERIODO", "COMPARATIVO", columnas, filas)


def balance_mensual(p):
    """con_bg_comp_anual_xls: una columna por mes del ejercicio hasta el corte."""
    b = _Balance(p)
    c = b.corte
    meses = meses_entre(c.periodo.fecini, c.fecha)
    rangos = [(c, (c.ini, m) if p.acumulado else (m, m)) for m in meses]
    funciones, activas = b.preparar(rangos)
    lineas = selecciona(b.nomenclatura(), ORDEN_BG, b.nom, None if p.incluir_todas else activas)
    columnas = [Columna(f"({m // 100}-{m % 100:02d})") for m in meses]
    if not p.acumulado:
        columnas.append(Columna("TOTAL", formula=("suma", 0, len(meses) - 1)))

    def valores(cta):
        if cta.codigo not in activas:
            return None
        v = [f(cta.codigo) for f in funciones]
        return v + [None] if not p.acumulado else v

    filas = arma_bg(lineas, b.nom, b.nivel, columnas, valores, total_activo=False)
    return b.reporte("ESTADO DE SITUACION COMPARATIVO ANUAL",
                     "ACUMULADO" if p.acumulado else "MENSUAL", columnas, filas)


# -------------------------------------------------------------- Estado de Resultados

class _Resultados(Base):
    orden_def = ORDEN_ER
    deudoras = DEUDORAS_ER

    def preparar(self, rangos_detalle, filtro=None):
        grupos = [g for _, gs in self.orden_def for g in gs]
        movs = self.movs = self.movimientos(*grupos, filtro=filtro)
        activas = self.activas(movs, self.orden_def, rangos_detalle)
        _sin_datos(activas)
        deudoras = set(self.nom.prefijos(*self.deudoras))

        def saldo(cuenta, desde, hasta, cc=None):
            return movs.neto(cuenta, desde, hasta, cuenta[: self.logn] in deudoras, cc)
        return saldo, activas

    def signo_resultado(self, cuenta):
        return 1 if self.nom.grupo(cuenta.codigo, *ACREEDORAS_ER) else -1

    def utilidad_bruta(self, valores_activas):
        if not self.nom.grupos.get("costos"):
            return 0.0
        ing = sum(v for c, v in valores_activas.items() if self.nom.grupo(c, "ingresos"))
        cto = sum(v for c, v in valores_activas.items() if self.nom.grupo(c, "costos"))
        return round(ing - cto, 2)


def estado_resultados(p):
    """con_er_xls / con_er (Crystal)."""
    r = _Resultados(p)
    c = r.corte
    saldo, activas = r.preparar([(c.ini, c.fin)])
    rango = c.rango(p.acumulado)
    valores = {cta: saldo(cta, *rango) for cta in activas}
    ub = r.utilidad_bruta(valores)
    lineas = selecciona(r.nomenclatura(), ORDEN_ER, r.nom, None if p.incluir_todas else activas)
    columnas = [Columna("")]
    filas = arma_er(lineas, r.nom, r.nivel, columnas,
                    lambda cta: [valores[cta.codigo]] if cta.codigo in valores else None,
                    r.signo_resultado, utilidad_bruta=[ub] if ub else None)
    return r.reporte("ESTADO DE RESULTADOS", c.leyenda(p.acumulado), columnas, filas)


def estado_resultados_columnas(p):
    """con_er_xls-01: saldos en una columna por nivel."""
    r = _Resultados(p)
    c = r.corte
    saldo, activas = r.preparar([(c.ini, c.fin)])
    rango = c.rango(p.acumulado)
    valores = {cta: saldo(cta, *rango) for cta in activas}
    nomencla = r.nomenclatura()
    lineas = selecciona(nomencla, ORDEN_ER, r.nom, None if p.incluir_todas else activas)
    saldos = acumula_por_mayor(valores, nomencla)
    columnas = [Columna(f"NIVEL {r.nivel - j}") for j in range(r.nivel)]
    filas = arma_er_columnas(lineas, r.nom, r.nivel, saldos, r.utilidad_bruta(valores), r.signo_resultado)
    return r.reporte("ESTADO DE RESULTADOS", c.leyenda(p.acumulado), columnas, filas, usar_formulas=False)


def _er_comparativo(p, titulo, diferencia_actual_menos_comparativo):
    r = _Resultados(p, (p.anio2, p.mes2))
    c0, c1 = r.cortes
    saldo, activas = r.preparar([(c0.ini, c0.fin), (c1.ini, c1.fin)])
    r0, r1 = c0.rango(p.acumulado), c1.rango(p.acumulado)
    lineas = selecciona(r.nomenclatura(), ORDEN_ER, r.nom, None if p.incluir_todas else activas)
    if diferencia_actual_menos_comparativo:   # con_er_comparativo_xls: C - D, % sobre D
        variacion = [Columna("VARIACION", "MONTO", ("resta", 0, 1)), Columna("", "%", ("pct", 2, 1), ancho=11)]
    else:                                     # con_er_comparativo_periodo_xls: D - C, % sobre C
        variacion = [Columna("VARIACION", "MONTO", ("resta", 1, 0)), Columna("", "%", ("pct", 2, 0), ancho=11)]
    columnas = [Columna(c0.leyenda(p.acumulado)), Columna(c1.leyenda(p.acumulado))] + variacion
    filas = arma_er(lineas, r.nom, r.nivel, columnas,
                    lambda cta: [saldo(cta.codigo, *r0), saldo(cta.codigo, *r1), None, None]
                    if cta.codigo in activas else None,
                    r.signo_resultado)
    return r.reporte(titulo, "COMPARATIVO", columnas, filas)


def estado_resultados_comparativo_mes_anterior(p):
    """con_er_comparativo_xls (periodo comparativo por defecto: mes anterior)."""
    return _er_comparativo(p, "ESTADO DE RESULTADOS COMPARATIVO MES ANTERIOR", True)


def estado_resultados_comparativo_periodo(p):
    """con_er_comparativo_periodo_xls (periodo comparativo por defecto: año anterior)."""
    return _er_comparativo(p, "ESTADO DE RESULTADOS COMPARATIVO PERIODO", False)


def estado_resultados_mensual(p):
    """con_er_comp_anual_xls: movimiento de cada mes del ejercicio + TOTAL."""
    r = _Resultados(p)
    c = r.corte
    saldo, activas = r.preparar([(c.ini, c.fin)])
    meses = meses_entre(c.periodo.fecini, c.fecha)
    lineas = selecciona(r.nomenclatura(), ORDEN_ER, r.nom, activas)
    columnas = [Columna(f"({m // 100}-{m % 100:02d})") for m in meses]
    columnas.append(Columna("TOTAL", formula=("suma", 0, len(meses) - 1)))
    filas = arma_er(lineas, r.nom, r.nivel, columnas,
                    lambda cta: [saldo(cta.codigo, m, m) for m in meses] + [None]
                    if cta.codigo in activas else None,
                    r.signo_resultado)
    return r.reporte("ESTADO DE RESULTADOS MENSUAL POR PERIODO", c.leyenda(True), columnas, filas)


class _CuentasOrden(_Resultados):
    orden_def = ORDEN_CTAS_ORDEN
    deudoras = ("ordendeudoras",)

    def signo_resultado(self, cuenta):
        return -1 if self.nom.grupo(cuenta.codigo, "ordenacreedoras") else 1


def cuentas_de_orden(p):
    """con_cta_orden_xls."""
    r = _CuentasOrden(p)
    if not r.nom.prefijos("ordendeudoras", "ordenacreedoras"):
        raise ErrorReporte("No existe detalle de cuentas de orden en la nomenclatura, favor verifique.")
    c = r.corte
    saldo, activas = r.preparar([(c.ini, c.fin)])
    rango = c.rango(p.acumulado)
    lineas = selecciona(r.nomenclatura(), ORDEN_CTAS_ORDEN, r.nom, None if p.incluir_todas else activas)
    columnas = [Columna("")]
    filas = arma_er(lineas, r.nom, r.nivel, columnas,
                    lambda cta: [saldo(cta.codigo, *rango)] if cta.codigo in activas else None,
                    r.signo_resultado)
    return r.reporte("REPORTE DE CUENTAS DE ORDEN", c.leyenda(p.acumulado), columnas, filas)
