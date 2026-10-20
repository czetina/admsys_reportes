"""
Relación vfmenuc.comando -> reporte, con las opciones y valores por defecto de cada
formulario VFP (checkboxes chkAcumulado, chkIncCtasTodas, etc.).
"""
from dataclasses import dataclass, field, replace

from . import reportes as r
from . import activos
from . import balances
from . import integracion
from . import libros
from . import libros_fiscales
from . import presupuesto_cc as presupuesto
from . import reportes_cc as cc
from . import reportes_chq as chq
from . import reportes_ctacte as ctacte
from . import reportes_fac as fac
from . import reportes_iva as iva


@dataclass(frozen=True)
class Definicion:
    titulo: str
    funcion: object
    opciones: dict = field(default_factory=dict)   # nombre -> valor por defecto
    periodo2: str | None = None                     # "mes_anterior" | "anio_anterior"
    salida: str = "excel"                           # excel | pantalla
    # Campos extra: "centro", "centro_rango", "centros", "cuenta_rango", "meses",
    #              "directorio_rango", "periodo_inicial", "orden", "presentacion", "folio"
    campos: tuple = ()
    usa_nivel: bool = True                          # los libros auxiliares no usan nivel de cuenta
    meses_inicial: int = 12                         # valor inicial de «Meses a reportar»
    nivel_inicial: int | None = None                # valor inicial de «Nivel» (vacío = todos)
    usa_periodo: bool = True                        # False: pide fechas (cuentas corrientes) en vez de año/mes
    usa_fecha_inicial: bool = False                 # con usa_periodo=False, pide también fecha inicial
    sistema: str = ""                               # CXC / CXP para los reportes de cuenta corriente
    tipos_cuenta: tuple = ()                        # Cheques: tipos de cuenta (chqdefctas.codtipocta) del selector
    dimension: str = "cc"                           # reportes por centro: cc (menú 22), ci (24), ca (26)


_BG = {"incluir_todas": False, "no_saldo_cero": True, "fecha_hora": True}
_ER = {"acumulado": True, "incluir_todas": False, "fecha_hora": True}

REPORTES = {
    "con_bg_xls": Definicion("BALANCE GENERAL", r.balance_general, _BG),
    "con_er_xls": Definicion("ESTADO DE RESULTADOS", r.estado_resultados, _ER),
    "con_bg_xls-01": Definicion("BALANCE GENERAL", r.balance_general_columnas, _BG),
    "con_er_xls-01": Definicion("ESTADO DE RESULTADOS", r.estado_resultados_columnas, _ER),
    # Las versiones Crystal se sirven como vista en pantalla (imprimible) del mismo cálculo.
    "con_bg": Definicion("BALANCE GENERAL", r.balance_general, _BG, salida="pantalla", campos=("folio",),
                         nivel_inicial=3),
    "con_er": Definicion("ESTADO DE RESULTADOS", r.estado_resultados, _ER, salida="pantalla", campos=("folio",),
                         nivel_inicial=5),
    "con_cta_orden_xls": Definicion("REPORTE DE CUENTAS DE ORDEN", r.cuentas_de_orden,
                                    {"acumulado": True, "incluir_todas": False}),
    "con_bg_comp_anual_xls": Definicion("ESTADO DE SITUACION COMPARATIVO ANUAL", r.balance_mensual,
                                        {"acumulado": True, "incluir_todas": True, "fecha_hora": True}),
    "con_er_comp_anual_xls": Definicion("ESTADO DE RESULTADOS MENSUAL POR PERIODO", r.estado_resultados_mensual),
    "con_bg_comparativo_xls": Definicion("BALANCE GENERAL COMPARATIVO MES ANTERIOR",
                                         r.balance_comparativo_mes_anterior,
                                         {"incluir_todas": False, "fecha_hora": True}),
    "con_er_comparativo_xls": Definicion("ESTADO DE RESULTADOS COMPARATIVO MES ANTERIOR",
                                         r.estado_resultados_comparativo_mes_anterior,
                                         {"acumulado": True, "incluir_todas": False}, periodo2="mes_anterior"),
    "con_bg_comparativo_periodo_xls": Definicion("BALANCE GENERAL COMPARATIVO PERIODO",
                                                 r.balance_comparativo_periodo,
                                                 {"acumulado": True, "incluir_todas": True, "fecha_hora": True},
                                                 periodo2="anio_anterior"),
    "con_er_comparativo_periodo_xls": Definicion("ESTADO DE RESULTADOS COMPARATIVO PERIODO",
                                                 r.estado_resultados_comparativo_periodo,
                                                 {"acumulado": True, "incluir_todas": False},
                                                 periodo2="anio_anterior"),

    # ---------------------------------------------- 22 Centros de Costo (C.C.)
    "con_er_cc_xls": Definicion("ESTADO DE RESULTADOS - POR CENTRO DE COSTO", cc.estado_resultados_por_centro,
                                {"acumulado": True, "incluir_todas": False, "mayor": False}),
    "con_er_cc_individual_xls": Definicion("ESTADO DE RESULTADOS POR CENTRO DE COSTO INDIVIDUAL",
                                           cc.estado_resultados_centro_individual, {"acumulado": True},
                                           campos=("centro_rango",)),
    "con_er_cc_sub_xls": Definicion("ESTADO DE RESULTADO POR SUB-CENTRO DE COSTO", cc.estado_resultados_subcentros,
                                    {"acumulado": True}),
    "con_er_cc_comp_anual_xls": Definicion("ESTADO DE RESULTADOS POR CUENTA - COMPARATIVO ANUAL - CENTRO DE COSTO",
                                           cc.estado_resultados_cuenta_mensual_centro, campos=("centro",)),
    "con_er_cc_comparativo_anual_xls": Definicion("ESTADO DE RESULTADOS - POR CENTRO DE COSTO - COMPARATIVO ANUAL",
                                                  cc.estado_resultados_centros_por_mes, {"mayor": False}),
    "con_balance_saldo_por_cc_xls": Definicion("BALANCE DE SALDOS POR CENTRO DE COSTO", cc.balance_saldos_por_centro,
                                               {"solo_movimiento": False},
                                               campos=("centro_rango", "cuenta_rango", "meses")),
    "con_presupuesto_cc_er_xls": Definicion("EJECUCION PRESUPUESTARIA (E.R.) POR CENTRO DE COSTO",
                                            cc.estado_resultados_vs_presupuesto_centro, {"acumulado": True},
                                            campos=("centros",)),
    "con_presupuesto_cc_xls": Definicion("EJECUCION PRESUPUESTARIA POR CENTRO DE COSTO",
                                         presupuesto.presupuesto_por_centro,
                                         {"acumulado": True, "detallar": True}, campos=("centros",)),
    "con_presupuesto_cc_cons_xls": Definicion("CONSOLIDADO DE EJECUCION PRESUPUESTARIA POR CENTRO DE COSTO",
                                              presupuesto.presupuesto_consolidado, {"acumulado": True},
                                              campos=("centros",)),
    "con_mov_centro_costo": Definicion("MOVIMIENTO POR CENTRO DE COSTO", libros.movimiento_por_centro,
                                       {"fecha_hora": True}, salida="pantalla",
                                       campos=("centro_rango", "cuenta_rango", "meses"), usa_nivel=False,
                                       meses_inicial=1),
    "con_dmg_cc": Definicion("DETALLE POR CENTRO DE COSTO", libros.mayor_por_centro, {"incluye_concepto": True},
                             salida="pantalla", campos=("centro_rango", "cuenta_rango", "meses", "folio"),
                             usa_nivel=False, meses_inicial=1),

    # ---------------------------------------------- 35 Libros Auxiliares
    "con_balance_de_saldos_xls": Definicion("BALANCE DE SALDOS", balances.balance_de_saldos, {"fecha_hora": True}),
    "con_balance_de_comprobacion_xls": Definicion("BALANCE DE COMPROBACIÓN", balances.balance_de_comprobacion,
                                                  {"fecha_hora": True}),
    "con_balance_de_saldos_analitico_xls": Definicion("BALANCE ANALÍTICO", balances.balance_analitico,
                                                      {"fecha_hora": True}),
    "con_dmg_integracion": Definicion("INTEGRACION DE CUENTAS CONTABLES", integracion.integracion_cuentas,
                                      {"mostrar_llaves": False, "mostrar_cxp": False,
                                       "incluye_desactualizados": False, "incluye_apertura": False},
                                      campos=("cuenta_rango", "directorio_rango", "meses", "periodo_inicial", "orden"),
                                      usa_nivel=False),
    "con_dmg_integracion_td": Definicion("INTEGRACION DE CUENTAS CONTABLES", integracion.integracion_cuentas_td,
                                         {"genera_td": True, "incluye_desactualizados": False,
                                          "incluye_apertura": False},
                                         campos=("cuenta_rango", "directorio_rango", "meses", "periodo_inicial",
                                                 "orden"), usa_nivel=False),
    "con_dmg_integracion_x_directorio": Definicion("LIBRO MAYOR GENERAL", balances.mayor_por_directorio,
                                                   {"no_saldo_cero": False, "ver_detallado": False},
                                                   campos=("cuenta_rango",)),
    "con_dmg": Definicion("DIARIO MAYOR GENERAL", libros.diario_mayor_general, {"incluye_concepto": True},
                          salida="pantalla", campos=("cuenta_rango", "meses", "periodo_inicial", "folio"),
                          usa_nivel=False, meses_inicial=1),
    "con_estado_cta_contable": Definicion("ESTADO DE CUENTA CONTABLE", libros.estado_cuenta_contable,
                                          {"incluye_concepto": True, "fecha_hora": True}, salida="pantalla",
                                          campos=("cuenta_rango", "meses", "folio"), usa_nivel=False,
                                          meses_inicial=1),
    "con_dmg_xls": Definicion("DIARIO MAYOR GENERAL", libros.diario_mayor_general_xls,
                              {"incluir_todas": False, "incluye_desactualizados": False},
                              campos=("cuenta_rango", "meses", "folio"), usa_nivel=False, meses_inicial=1),
    "con_diario": Definicion("LIBRO DIARIO", libros.diario, {"actualizados": True}, salida="pantalla",
                             campos=("presentacion", "folio"), usa_nivel=False),
    "con_diario_especial_auditores": Definicion("DIARIO - AUDITORES", libros.diario_auditores,
                                                {"archivo_texto": False}, campos=("cuenta_rango", "meses"),
                                                usa_nivel=False, meses_inicial=1),
    "con_diario_pwc_2011": Definicion("DIARIO - PWC", libros.diario_pwc_2011,
                                      {"anio_completo": False, "archivo_texto": False},
                                      campos=("cuenta_rango",), usa_nivel=False),
    "con_diario_pwc_2013": Definicion("DIARIO - PWC", libros.diario_pwc_2013, {"archivo_texto": False},
                                      campos=("cuenta_rango", "meses"), usa_nivel=False, meses_inicial=1),

    # ---------------------------------------------- 30 Libros Fiscales (folio, resolución, «sin movimientos»)
    "con_libro_diario": Definicion("LIBRO DE DIARIO", libros.libro_diario, {"actualizados": True},
                                   salida="pantalla", campos=("presentacion", "folio"), usa_nivel=False),
    "con_libro_diario_resumen_xls": Definicion("LIBRO DIARIO", balances.libro_diario_resumido, {},
                                               campos=("folio", "resolucion")),
    "con_libro_mayor": Definicion("LIBRO MAYOR", libros.libro_mayor, {"incluye_concepto": True},
                                  salida="pantalla", campos=("folio",), usa_nivel=False, meses_inicial=1),
    "con_libro_mayor_resumen_xls": Definicion("LIBRO MAYOR", balances.libro_mayor_resumido, {},
                                              campos=("folio", "resolucion")),
    "con_libro_mayor_resumen_xls_diario": Definicion("LIBRO MAYOR MOVIMIENTOS DIARIOS", balances.libro_mayor_por_dia,
                                                     {}, campos=("folio", "resolucion")),
    "con_dmg_resumido": Definicion("DIARIO MAYOR GENERAL", libros.diario_mayor_resumido, {"incluye_concepto": True},
                                   salida="pantalla", campos=("cuenta_rango", "meses", "periodo_inicial", "folio"),
                                   usa_nivel=False, meses_inicial=1),
    "con_libro_inventario_xls": Definicion("LIBRO DE INVENTARIO", balances.libro_inventario,
                                           {"incluir_todas": False}, campos=("folio", "resolucion")),
    "con_libro_estfin_bg_xls": Definicion("LIBRO DE ESTADOS FINANCIEROS", libros_fiscales.libro_estados_bg,
                                          {"acumulado": True, "incluir_todas": False, "no_saldo_cero": False},
                                          campos=("folio", "resolucion")),
    "con_libro_estfin_er_xls": Definicion("LIBRO DE ESTADOS FINANCIEROS", libros_fiscales.libro_estados_er,
                                          {"acumulado": True, "incluir_todas": False}, campos=("folio", "resolucion")),
    "con_ef_balance_de_saldos_xls": Definicion("BALANCE DE SALDOS", libros_fiscales.balance_de_saldos_ef,
                                               {"fecha_hora": True}, campos=("folio",)),
}


# ---------------------------------------------- 24 Centros de Integración (C.I.)
# Los mismos reportes del menú 22 sobre codci / empci (Definicion.dimension); VFP repetía cada
# formulario cambiando codcc por codci. Diferencias de los formularios VFP de C.I.: el ER por centro
# no tiene «solo centros de nivel mayor» ni «incluye cuentas sin movimientos», el comparativo anual
# pide un rango de centros y el DMG no filtra por directorio.
def _ci(comando_cc, comando, titulo, **cambios):
    REPORTES[comando] = replace(REPORTES[comando_cc], titulo=titulo, dimension="ci", **cambios)


_ci("con_dmg_cc", "con_dmg_ci", "DETALLE POR CENTRO DE INTEGRACIÓN")
_ci("con_mov_centro_costo", "con_mov_centro_integracion", "MOVIMIENTO POR CENTRO DE INTEGRACIÓN")
_ci("con_er_cc_xls", "con_er_ci_xls", "ESTADO DE RESULTADOS - POR CENTRO DE INTEGRACIÓN",
    opciones={"acumulado": True})
_ci("con_er_cc_individual_xls", "con_er_ci_individual_xls", "ESTADO DE RESULTADOS POR CENTRO DE INTEGRACIÓN INDIVIDUAL")
_ci("con_er_cc_sub_xls", "con_er_ci_sub_xls", "ESTADO DE RESULTADO POR SUB-CENTRO DE INTEGRACIÓN")
_ci("con_er_cc_comp_anual_xls", "con_er_ci_comp_anual_xls",
    "ESTADO DE RESULTADOS COMPARATIVO ANUAL - POR CENTRO DE INTEGRACIÓN", campos=("centro_rango",))
_ci("con_balance_saldo_por_cc_xls", "con_balance_saldo_por_ci_xls", "BALANCE DE SALDOS POR CENTRO DE INTEGRACIÓN")
_ci("con_presupuesto_cc_er_xls", "con_presupuesto_ci_er_xls", "EJECUCION PRESUPUESTARIA (E.R.) POR CENTRO DE INTEGRACIÓN")
_ci("con_presupuesto_cc_xls", "con_presupuesto_ci_xls", "EJECUCION PRESUPUESTARIA POR CENTRO DE INTEGRACIÓN")
_ci("con_presupuesto_cc_cons_xls", "con_presupuesto_ci_cons_xls",
    "CONSOLIDADO DE EJECUCION PRESUPUESTARIA POR CENTRO DE INTEGRACIÓN")
REPORTES["con_bg_ci_xls"] = Definicion("BALANCE GENERAL - POR CENTRO DE INTEGRACIÓN", cc.balance_general_por_centro,
                                       {"acumulado": True}, dimension="ci")

# ---------------------------------------------- 26 Centros de Actividad (C.A.): mismo esquema sobre codca / empca
REPORTES["con_dmg_ca"] = replace(REPORTES["con_dmg_cc"], titulo="DETALLE POR CENTRO DE ACTIVIDAD", dimension="ca")
REPORTES["con_mov_centro_actividad"] = replace(REPORTES["con_mov_centro_costo"], titulo="MOVIMIENTO POR CENTRO DE ACTIVIDAD",
                                               dimension="ca")

# ---------------------------------------------- 50 Reportes de CTAXC / CTAXP (mismo formulario VFP)
_FILTROS_CC = ("directorio_rango", "clasificaciones", "cliente_base", "cuenta_aux", "contrato")


def _cuenta_corriente(comando, titulo, funcion, opciones, campos, **extra):
    for sistema in ("CXC", "CXP"):
        REPORTES[f"{sistema.lower()}.{comando}"] = Definicion(
            titulo, funcion, dict(opciones), campos=campos, usa_nivel=False, usa_periodo=False,
            sistema=sistema, **extra)


_cuenta_corriente("con_ctacte_integracion", "INTEGRACION DE SALDOS", ctacte.integracion,
                  {"mostrar_me": False, "mostrar_proveedor": False, "mostrar_contable": False},
                  ("tipo_saldos", "fecha_base", "orden_cc", "antiguedad", "moneda") + _FILTROS_CC)
_cuenta_corriente("con_ctacte_estcta", "ESTADO DE CUENTA", ctacte.estado_cuenta,
                  {"mostrar_me": False, "mostrar_proveedor": False, "mostrar_actualizado": False,
                   "mostrar_contable": False},
                  ("moneda",) + _FILTROS_CC, usa_fecha_inicial=True)

_cuenta_corriente("con_ctacte_integracion_me", "INTEGRACION DE SALDOS M.E.", ctacte.integracion_me,
                  {"mostrar_proveedor": False, "mostrar_contable": False},
                  ("tipo_saldos", "fecha_base", "orden_cc", "antiguedad", "moneda") + _FILTROS_CC)
_cuenta_corriente("con_ctacte_integracion_positivos_negativos", "SALDOS POSITIVOS Y NEGATIVOS",
                  ctacte.positivos_negativos, {},
                  ("tipo_saldos", "fecha_base", "orden_cc", "moneda") + _FILTROS_CC)
_cuenta_corriente("con_ctacte_detalle", "DETALLE POR CUENTA", ctacte.detalle_por_cuenta, {},
                  ("tipo_doc", "moneda") + _FILTROS_CC, usa_fecha_inicial=True)
# Solo en el menú de CXC (vfmenuc 5021 y 5014)
REPORTES["cxc.con_ctacte_estcta_crystal"] = Definicion(
    "ESTADO DE CUENTA", ctacte.estado_cuenta_cliente, {}, campos=("directorio_rango", "cliente_base"),
    usa_nivel=False, usa_periodo=False, usa_fecha_inicial=True, sistema="CXC", salida="pantalla")
REPORTES["cxc.con_ctacte_estcta_moras"] = Definicion(
    "ESTADO DE CUENTA PARA MORAS", ctacte.estado_cuenta_moras, {},
    campos=("directorio_rango", "clasificaciones", "cliente_base", "cuenta_aux", "contrato"),
    usa_nivel=False, usa_periodo=False, usa_fecha_inicial=True, sistema="CXC")
_cuenta_corriente("con_proyeccion_de_pago_xls", "PROYECCION", ctacte.proyeccion,
                  {"mostrar_me": False, "mostrar_proveedor": False, "mostrar_contable": False},
                  ("orden_cc", "antiguedad", "semanal", "moneda") + _FILTROS_CC)
_cuenta_corriente("con_documentos_no_aplicados", "DOCUMENTOS NO APLICADOS", ctacte.no_aplicados, {},
                  ("tipo_saldos", "fecha_base", "moneda", "directorio_rango", "clasificaciones"), usa_fecha_inicial=True)
_cuenta_corriente("con_ctacte_td_programacion_pe", "SALDOS PARA PAGOS ELECTRONICOS", ctacte.saldos_pagos_electronicos,
                  {}, ("tipo_saldos", "fecha_base", "antiguedad", "moneda") + _FILTROS_CC)
_cuenta_corriente("con_incongruencias_de_aplicaciones", "INCONGRUENCIAS EN APLICACIONES",
                  ctacte.aplicaciones_incongruentes, {"mostrar_llaves": False}, ("directorio_rango",),
                  usa_fecha_inicial=True)
_cuenta_corriente("con_registros_con_problemas", "REGISTROS CON PROBLEMAS", ctacte.registros_con_problemas, {}, (),
                  usa_fecha_inicial=True)
_cuenta_corriente("con_ctacte_tabla_dinamica", "TABLA DINAMICA DE CUENTA CORRIENTE", ctacte.tabla_dinamica, {},
                  ("tipo_saldos", "fecha_base", "moneda") + _FILTROS_CC)
_cuenta_corriente("det_conta_vrs_auxiliar", "COMPARATIVO CONTABILIDAD VS. AUXILIAR", ctacte.auxiliar_vs_contabilidad, {},
                  ("cuenta_rango", "directorio_rango", "clasificaciones"), usa_fecha_inicial=True)
REPORTES["cxp.con_empdoctos_con_impuestos"] = Definicion(        # solo en el menú de CXP (5040)
    "CONSULTA DE DOCUMENTOS", ctacte.consulta_documentos, {"mostrar_llaves": False, "mostrar_actualizado": False},
    campos=("tipo_doc", "directorio_rango", "clasificaciones", "moneda", "docref", "estado_doc"),
    usa_nivel=False, usa_periodo=False, usa_fecha_inicial=True, sistema="CXP")
_cuenta_corriente("con_detalle_aplicados", "DOCUMENTOS PAGADOS", ctacte.documentos_aplicados,
                  {"mostrar_me": False, "mostrar_proveedor": False},
                  ("tipo_saldos", "fecha_base", "orden_cc", "antiguedad", "moneda") + _FILTROS_CC)
_cuenta_corriente("con_ctacte_pendientes", "DOCUMENTOS PENDIENTES", ctacte.documentos_pendientes, {},
                  ("tipo_saldos", "fecha_base", "orden_pendientes", "moneda", "directorio_rango", "clasificaciones",
                   "cuenta_aux"), usa_fecha_inicial=True)

# ---------------------------------------------- IVA: menús 50 (libros), 52 (especiales) y 55 (Asiste Libros)
def _iva(comando, titulo, funcion, opciones=None, campos=(), **extra):
    REPORTES[f"iva.{comando}"] = Definicion(titulo, funcion, dict(opciones or {}), campos=campos, usa_nivel=False,
                                            **extra)


_iva("con_iva_compras_xls", "LIBRO DE COMPRAS", iva.libro_compras,
     {"incluye_info_adicional": True, "incluye_des_contable": True, "mostrar_ret_fe": False,
      "inc_det_cuadre": True, "fecha_hora": False}, ("sucursal_compras", "folio"))
_iva("con_iva_ventas_xls", "LIBRO DE VENTAS", iva.libro_ventas,
     {"inc_det_cuadre": True, "resumen": False, "fecha_hora": False}, ("sucursal_ventas", "orden_ventas", "folio"))
_iva("con_iva_compras_referencia_xls", "LIBRO DE COMPRAS POR DOCUMENTO DE REFERENCIA", iva.compras_referencia,
     {"fecha_hora": False}, ("sucursal_compras", "referencia"))
_iva("sat_iva_compras_2013", "ASISTE LIBROS 2 - LIBRO DE COMPRAS", iva.sat_compras_2013, {}, ("sucursal_compras",))
_iva("sat_iva_ventas_2013", "ASISTE LIBROS 2 - LIBRO DE VENTAS", iva.sat_ventas_2013, {}, ("sucursal_ventas",))
_iva("sat_iva_compras", "ASISTE LIBROS - LIBRO DE COMPRAS", iva.sat_compras, {}, ("sucursal_compras",))
_iva("sat_iva_ventas", "ASISTE LIBROS - LIBRO DE VENTAS", iva.sat_ventas, {}, ("sucursal_ventas",))

# ---------------------------------------------- Facturación: menús 50 (reportes) y 55 (agencia)
def _fac(comando, titulo, funcion, opciones=None, campos=(), **extra):
    extra.setdefault("usa_periodo", False)
    extra.setdefault("usa_fecha_inicial", True)
    REPORTES[f"fac.{comando}"] = Definicion(titulo, funcion, dict(opciones or {}), campos=campos, usa_nivel=False,
                                            sistema="FAC", **extra)


_CORREL = ("tipo_rango", "directorio_rango", "moneda", "docref", "estado_fac", "orden_fac")
_fac("con_empdoctos_correl_fac", "CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN", fac.correlativo, {"fecha_hora": False},
     _CORREL)
_fac("con_empdoctos_correl_fac_vendedor", "CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN [VENTAS]",
     fac.correlativo_vendedor, {"fecha_hora": False}, _CORREL)
_fac("con_empdoctos_correl_fac_detallado", "CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN - DETALLADO",
     fac.correlativo_detallado, {"fecha_hora": False}, _CORREL[:-1])
_fac("con_empdoctos_correl_fac_gtl", "CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN [ODS]", fac.correlativo_ods,
     {"detallado": False, "fecha_hora": False}, _CORREL[:-1])
_fac("con_facturas_retencion_isr", "FACTURAS CON DETALLE DE RETENCIÓN ISR", fac.retencion_isr,
     {"detalle": True, "fecha_hora": False}, ("tipo_rango", "directorio_rango"))
_fac("con_fac_mensual_por_cli", "FACTURACIÓN MENSUAL POR CLIENTE", fac.mensual_por_cliente,
     {"ver_moneda_local": False, "fecha_hora": False}, ("directorio_rango", "conceptos_cc"), usa_periodo=True,
     usa_fecha_inicial=False)
_fac("con_fac_anual_comparativo_cli", "FACTURACIÓN ANUAL POR CLIENTE (COMPARATIVO)", fac.anual_comparativo,
     {"fecha_hora": False}, ("directorio_rango",), usa_periodo=True, usa_fecha_inicial=False,
     periodo2="anio_anterior")
_fac("con_facturacion_concepto_x_cuenta", "FACTURACIÓN POR CUENTA CONTABLE DE CONCEPTO", fac.concepto_x_cuenta,
     {"fecha_hora": False}, ("tipo_rango", "directorio_rango", "moneda", "docref", "conceptos_cc"))
_fac("con_consecutivo_facturacion", "CONSECUTIVO DE FACTURACIÓN", fac.consecutivo,
     {"desglosa_medios": True, "pendiente_numero": False, "incluye_anulados": False, "fecha_hora": False},
     ("tipo_rango", "directorio_rango", "moneda", "orden_consec"))
_fac("con_facturacion_medios_produccion", "CUADROS FINALES DE FACTURACIÓN", fac.medios_produccion,
     {"pendiente_numero": False, "incluye_anulados": False, "fecha_hora": False},
     ("tipo_rango", "directorio_rango", "moneda"))

# ---------------------------------------------- Cheques: caja y bancos (menús 12, 20, 50, 54, 55, 60, 62, 85)
def _chq(comando, titulo, funcion, opciones=None, campos=(), **extra):
    extra.setdefault("usa_periodo", False)
    extra.setdefault("usa_fecha_inicial", True)
    REPORTES[f"chq.{comando}"] = Definicion(titulo, funcion, dict(opciones or {"fecha_hora": False}),
                                            campos=campos, usa_nivel=False, sistema="CHQ", **extra)


_BANCOS = ("DM", "AH")
_chq("con_dm_estcta", "D.M. - ESTADO DE CUENTA", chq.estado_cuenta_banco, campos=("cuenta_chq",),
     tipos_cuenta=_BANCOS)
_chq("con_dm_estcta_detallado", "D.M. - ESTADO DE CUENTA - DETALLADO", chq.estado_cuenta_banco_detallado,
     campos=("cuenta_chq",), tipos_cuenta=_BANCOS)
_chq("con_dm_tipodoc", "D.M. - CORRELATIVO DE DOCUMENTOS", chq.correlativo_documentos,
     campos=("cuenta_chq", "tipo_rango", "directorio_rango", "clasificaciones"), tipos_cuenta=_BANCOS)
_chq("con_dm_dispo", "DISPONIBILIDAD", chq.disponibilidad, campos=("tipo_cuenta", "moneda"))
_chq("con_chq_autorizado_xls", "MOVIMIENTO DE CHEQUES AUTORIZADOS", chq.cheques_autorizados,
     {"incluye_aplicacion": False, "mostrar_adicionales": False, "fecha_hora": False},
     ("cuenta_chq_opc", "estado_aut", "usuario_chq", "directorio_rango"), tipos_cuenta=_BANCOS)
_chq("con_chq_entregados_xls", "MOVIMIENTO DE CHEQUES ENTREGADOS", chq.cheques_entregados,
     {"incluye_aplicacion": False, "mostrar_adicionales": False, "fecha_hora": False},
     ("cuenta_chq_opc", "estado_ent", "usuario_chq", "directorio_rango"), tipos_cuenta=_BANCOS)
_chq("con_chq_pendientes_numero_xls", "REGISTROS NO IMPRESOS (PENDIENTES DE NÚMERO)", chq.pendientes_numero,
     campos=("cuenta_chq_opc", "directorio_rango"))
_chq("con_cc_estcta", "CAJA CHICA - ESTADO DE CUENTA", chq.estado_cuenta_caja_chica, campos=("cuenta_chq",),
     tipos_cuenta=("CC",))
_chq("con_cl_estcta", "CAJA LIQUIDACIÓN - ESTADO DE CUENTA", chq.estado_cuenta_caja_liquidacion,
     campos=("cuenta_chq",), tipos_cuenta=("CL",))
_chq("con_cl_detalle_cc", "CAJAS POR LIQUIDAR", chq.cajas_detalle,
     campos=("cuenta_chq_opc", "directorio_rango", "docref"), tipos_cuenta=("CC", "CL"))
_chq("con_liquidacion_caja_chica", "LIQUIDACIÓN DE CAJA CHICA", chq.liquidacion_caja_chica,
     campos=("cuenta_chq", "directorio_rango", "clasificaciones"), tipos_cuenta=("CC", "CL"))
_chq("con_cc_resumen_ctas_doc_apl", "RESUMEN DE CUENTAS DE CAJA CHICA", chq.resumen_cuentas_caja,
     campos=("cuenta_chq", "directorio_rango", "numero_rango", "clasificaciones"), tipos_cuenta=("CC", "CL"))
_chq("con_dm_conci", "D.M. - CONCILIACIÓN BANCARIA", chq.conciliacion,
     {"ocultar_conciliados": False, "conci_cuadratica": False, "fecha_hora": False}, ("cuenta_chq",),
     tipos_cuenta=_BANCOS, usa_periodo=True, usa_fecha_inicial=False)
_chq("con_dm_conci_aecid", "D.M. - CONCILIACIÓN BANCARIA - AECID", chq.conciliacion_aecid, campos=("cuenta_chq",),
     tipos_cuenta=_BANCOS, usa_periodo=True, usa_fecha_inicial=False)
# Como el formulario VFP: resumen de conciliación, pendientes de aclarar y pendientes de número marcados
_POS = {"incluye_pend_numero": True, "incluye_aclarar": True, "resumen_conci": True,
        "detalle_no_autorizados": False, "detalle_no_entregados": False, "detalle_caducados": False,
        "fecha_hora": False}
_chq("con_dm_posicion", "POSICIÓN BANCARIA", chq.posicion_bancaria, _POS, ("tipo_cuenta", "moneda", "tasa"))
_chq("con_flujo_de_fondos_xls", "FLUJO DE FONDOS", chq.flujo_de_fondos,
     {"mostrar_resumen_bancos": True, "detalle_no_autorizados": False, "detalle_no_entregados": False,
      "detalle_caducados": False, "fecha_hora": False}, ("tipo_cuenta", "moneda", "tasa"), usa_fecha_inicial=False)
_chq("con_origen_aplicacion_fondos", "ORIGEN Y APLICACIÓN DE FONDOS", chq.origen_aplicacion,
     {"incluye_pend_numero": False, "fecha_hora": False}, campos=("tipo_cuenta", "moneda", "tasa_sola"), usa_periodo=True, usa_fecha_inicial=False)
_chq("con_flujo_de_fondos_proyectado_xls", "FLUJO DE FONDOS PROYECTADO", chq.flujo_proyectado,
     campos=("tipo_cuenta", "moneda", "tasa", "proyeccion", "directorio_rango"))
# Recibos (menú 60)
_chq("con_correlativo_recibos", "CORRELATIVO DE RECIBOS APLICADOS", chq.correlativo_recibos,
     {"mostrar_adicionales": False, "fecha_hora": False},
     ("cuenta_chq_opc", "tipo_doc", "serie", "numero_rango", "directorio_rango", "moneda", "clasificaciones"),
     tipos_cuenta=("CR",))
_chq("con_empdoctos_correl", "CORRELATIVO DE DOCUMENTOS (GENERAL)", chq.correlativo_general,
     {"solo_sin_numero": False, "mostrar_adicionales": False, "mostrar_creacion": False, "fecha_hora": False},
     ("cuenta_chq_opc", "tipo_rango", "serie_rango", "directorio_rango", "numero_rango", "moneda", "docref",
      "valor_doc", "estado_reg", "orden_tipo_serie", "clasificaciones"))
_chq("con_recibos_aplicados", "MOVIMIENTO DE RECIBOS", chq.movimiento_recibos,
     campos=("cuenta_chq", "directorio_rango", "moneda", "clasificaciones"), tipos_cuenta=("CR",))
_chq("con_depositos_aplicados_recibos", "DEPÓSITOS APLICADOS A RECIBOS", chq.depositos_aplicados,
     {"solo_pendientes": False, "fecha_hora": False},
     ("cuenta_chq_opc", "tipo_doc", "numero_rango", "directorio_rango", "moneda", "clasificaciones"), tipos_cuenta=_BANCOS)
_chq("con_correlativo_recibos_depositos", "CORRELATIVO DE RECIBOS Y DEPÓSITOS", chq.recibos_depositos,
     {"detallado": False, "fecha_hora": False},
     ("cuenta_chq", "serie", "numero_rango", "directorio_rango"), tipos_cuenta=("CR",))

ETIQUETAS = {
    "ocultar_bajas": "Ocultar activos dados de baja",
    "solo_electronico": "Mostrar solo equipo electrónico",
    "acumulado": "Acumulado",
    "incluir_todas": "Incluye cuentas sin movimientos",
    "no_saldo_cero": "No mostrar cuentas con saldo cero",
    "fecha_hora": "Mostrar fecha y hora de impresión",
    "mayor": "Mostrar solo centros de nivel mayor",
    "solo_movimiento": "Solo registros con movimiento",
    "detallar": "Detallar centro en hojas independientes",
    "mostrar_llaves": "Mostrar llaves de los registros",
    "mostrar_cxp": "Mostrar datos de orden de compra (CXP)",
    "incluye_desactualizados": "Incluye movimientos desactualizados",
    "incluye_apertura": "Incluir póliza de apertura en el detalle (si inicia en el primer mes del ejercicio)",
    "genera_td": "Generar hoja de análisis (tabla dinámica)",
    "incluye_concepto": "Incluye concepto",
    "actualizados": "Movimientos actualizados (desmarcar = solo los no actualizados)",
    "anio_completo": "Año completo (ignora el mes)",
    "archivo_texto": "Archivo de texto (formato auditores) en lugar de Excel",
    "ver_detallado": "Ver detalladamente (documentos de cada directorio)",
    "mostrar_me": "Mostrar referencia de valores en moneda extranjera",
    "mostrar_proveedor": "Mostrar nombre del documento (proveedor)",
    "mostrar_contable": "Mostrar datos contables",
    "mostrar_actualizado": "Mostrar si el documento está actualizado en contabilidad",
    "inc_det_cuadre": "Incluye detalle de cuadre (IVA y total manual, llaves)",
    "incluye_info_adicional": "Incluir información adicional del registro",
    "incluye_des_contable": "Incluye descripción contable",
    "mostrar_ret_fe": "Mostrar valor de retención para facturas especiales",
    "resumen": "Resumido por día (ventas del día por tipo y serie)",
    "detallado": "Reporte por detalle (líneas de cada factura)",
    "detalle": "Detalle por documento (desmarcar = una línea por cliente)",
    "ver_moneda_local": "Valores en moneda local",
    "desglosa_medios": "Desglosar valores de medios (subtotal, IVA, timbre)",
    "pendiente_numero": "Incluir registros pendientes de número",
    "incluye_anulados": "Incluir anulados",
    "incluye_aplicacion": "Incluir aplicación (documentos pagados con el cheque)",
    "mostrar_adicionales": "Mostrar datos adicionales",
    "solo_sin_numero": "Solo documentos sin número",
    "solo_pendientes": "Solo depósitos pendientes de aplicar",
    "mostrar_creacion": "Mostrar usuario de creación",
    "ocultar_conciliados": "Ocultar filas de movimientos conciliados del mes",
    "conci_cuadratica": "Saldo inicial según contabilidad (conciliación cuadrática)",
    "incluye_pend_numero": "Incluir pendientes de número",
    "incluye_aclarar": "Incluir pendientes de aclarar con el banco",
    "resumen_conci": "Mostrar resumen de conciliación bancaria",
    "detalle_no_autorizados": "Detallar documentos no autorizados (hoja aparte)",
    "detalle_no_entregados": "Detallar documentos no entregados (hoja aparte)",
    "detalle_caducados": "Detallar documentos caducados (hoja aparte)",
    "mostrar_resumen_bancos": "Mostrar resumen de disponibilidad (hoja de bancos)",
}


# ---------------------------------------------- Activos fijos (menú 50 de ACFIJOS)
def _acf(comando, titulo, funcion, opciones=None, campos=(), **extra):
    extra.setdefault("usa_periodo", False)
    REPORTES[f"acf.{comando}"] = Definicion(titulo, funcion, dict(opciones or {}), campos=campos, usa_nivel=False,
                                            sistema="ACF", **extra)


_acf("con_calculo_dep_xdia_acfijos", "CÁLCULO DE DEPRECIACIONES", activos.depreciacion,
     {"ocultar_bajas": False, "solo_electronico": False, "fecha_hora": False}, ("filtros_activos",))

