"""
Menú principal: los comandos de vfmenuc agrupados por tarea contable.

Cada reporte junta sus variantes (Excel, en columnas, imprimible, balance / resultados...) en
un solo renglón. Los permisos siguen saliendo de vfaccess: una variante se muestra solo si el
usuario la tiene en su menú y ya está migrada; un reporte sin variantes visibles no aparece.
Un comando de vfmenuc que no esté aquí cae en la categoría «Otros», así no se pierde ninguno.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Reporte:
    nombre: str
    descripcion: str
    icono: str
    variantes: tuple       # ((etiqueta, comando), ...) la primera es la principal


@dataclass(frozen=True)
class Categoria:
    clave: str
    nombre: str
    icono: str
    reportes: tuple
    modulo: str = "Contabilidad"     # encabezado de la sección en el menú


CATEGORIAS = (
    Categoria("estados", "Estados financieros", "bi-bank", (
        Reporte("Balance general", "Activo, pasivo y patrimonio al cierre del mes", "bi-bank",
                (("Excel", "con_bg_xls"), ("En columnas", "con_bg_xls-01"), ("Imprimible", "con_bg"))),
        Reporte("Estado de resultados", "Ingresos, costos y gastos del periodo", "bi-graph-up",
                (("Excel", "con_er_xls"), ("En columnas", "con_er_xls-01"), ("Imprimible", "con_er"))),
        Reporte("Balance general en moneda extranjera", "Balance con referencia en otra moneda", "bi-currency-exchange",
                (("Excel", "con_bg_ref_me_xls"),)),
        Reporte("Cuentas de orden", "Saldos de las cuentas de orden", "bi-clipboard-check",
                (("Excel", "con_cta_orden_xls"),)),
    )),
    Categoria("comparativos", "Comparativos", "bi-arrow-left-right", (
        Reporte("Mensual por periodo", "Una columna por cada mes del ejercicio", "bi-calendar3",
                (("Balance", "con_bg_comp_anual_xls"), ("Resultados", "con_er_comp_anual_xls"))),
        Reporte("Contra el mes anterior", "Mes actual frente al anterior, con variación", "bi-arrow-left-right",
                (("Balance", "con_bg_comparativo_xls"), ("Resultados", "con_er_comparativo_xls"))),
        Reporte("Contra otro periodo", "El mismo mes de otro año, con variación", "bi-calendar2-range",
                (("Balance", "con_bg_comparativo_periodo_xls"), ("Resultados", "con_er_comparativo_periodo_xls"))),
    )),
    Categoria("centros", "Centros de costo", "bi-diagram-3", (
        Reporte("Resultados por centro", "Estado de resultados con una columna por centro", "bi-diagram-3",
                (("Todos los centros", "con_er_cc_xls"), ("Rango de centros", "con_er_cc_individual_xls"),
                 ("Por subcentro", "con_er_cc_sub_xls"))),
        Reporte("Resultados mensuales por centro", "Cada mes del ejercicio en una columna", "bi-calendar3",
                (("Por centro", "con_er_cc_comparativo_anual_xls"), ("Por cuenta de un centro", "con_er_cc_comp_anual_xls"))),
        Reporte("Balance de saldos por centro", "Saldos de cada cuenta abiertos por centro", "bi-list-columns",
                (("Excel", "con_balance_saldo_por_cc_xls"),)),
        Reporte("Movimiento por centro", "Partidas de cada centro con saldo acumulado", "bi-arrow-repeat",
                (("Imprimible", "con_mov_centro_costo"), ("Tabla dinámica", "con_mov_centro_costo_dmg_td"),
                 ("Por cuenta (tabla dinámica)", "con_mov_centro_costo_det_cuenta_td"))),
        Reporte("Mayor por centro", "Movimientos por cuenta y centro, con saldo acumulado", "bi-journals",
                (("Imprimible", "con_dmg_cc"), ("Plano", "con_dmg_cc_plano"))),
    )),
    Categoria("integracion", "Centros de integración", "bi-bounding-box", (
        Reporte("Estados financieros por C.I.", "Una columna por centro de integración", "bi-bounding-box",
                (("Resultados", "con_er_ci_xls"), ("Balance general", "con_bg_ci_xls"),
                 ("Rango de centros", "con_er_ci_individual_xls"), ("Por subcentro", "con_er_ci_sub_xls"))),
        Reporte("Resultados mensuales por C.I.", "Cada mes del ejercicio, de un rango de centros", "bi-calendar3",
                (("Excel", "con_er_ci_comp_anual_xls"),)),
        Reporte("Balance de saldos por C.I.", "Saldos de cada cuenta abiertos por centro", "bi-list-columns",
                (("Excel", "con_balance_saldo_por_ci_xls"),)),
        Reporte("Movimiento y mayor por C.I.", "Partidas por centro o por cuenta y centro, con saldo acumulado",
                "bi-journals", (("Movimiento", "con_mov_centro_integracion"), ("Mayor", "con_dmg_ci"))),
        Reporte("Presupuesto por C.I.", "Resultados contra presupuesto y ejecución presupuestaria", "bi-bullseye",
                (("Contra E.R.", "con_presupuesto_ci_er_xls"), ("Ejecución", "con_presupuesto_ci_xls"),
                 ("Acumulado", "con_presupuesto_ci_cons_xls"))),
    )),
    Categoria("actividad", "Centros de actividad", "bi-activity", (
        Reporte("Movimiento y mayor por C.A.", "Partidas por centro de actividad o por cuenta y centro, con saldo acumulado",
                "bi-journals", (("Movimiento", "con_mov_centro_actividad"), ("Mayor", "con_dmg_ca"))),
    )),
    Categoria("presupuesto", "Presupuesto", "bi-bullseye", (
        Reporte("Presupuesto general", "Presupuesto de cada cuenta, mes a mes hasta el corte", "bi-calendar3",
                (("Excel", "con_presupuesto"),)),
        Reporte("Ejecución presupuestaria (E.R.)", "Estado de resultados de la empresa frente al presupuesto, del mes y acumulado",
                "bi-bullseye", (("Excel", "con_presupuesto_er_xls"),)),
        Reporte("Resultados contra presupuesto por centro", "Estado de resultados de los centros elegidos frente al presupuesto",
                "bi-bullseye", (("Excel", "con_presupuesto_cc_er_xls"),)),
        Reporte("Ejecución presupuestaria", "Por centro de costo, con hoja de resumen e ISR", "bi-pie-chart",
                (("Por centro", "con_presupuesto_cc_xls"), ("Acumulado", "con_presupuesto_cc_cons_xls"))),
    )),
    Categoria("diario", "Libro diario", "bi-journal-text", (
        Reporte("Libro diario", "Pólizas del mes, en detalle o resumidas por día o tipo de documento",
                "bi-journal-text", (("Imprimible", "con_diario"),)),
    )),
    Categoria("mayor", "Mayor e integración", "bi-journals", (
        Reporte("Diario mayor general", "Movimientos de cada cuenta con saldo acumulado", "bi-journals",
                (("Imprimible", "con_dmg"), ("Excel", "con_dmg_xls"))),
        Reporte("Estado de cuenta contable", "Movimientos por cuenta con centro de costo, sin anuladas",
                "bi-file-earmark-ruled", (("Imprimible", "con_estado_cta_contable"),)),
        Reporte("Integración de cuentas", "Saldo inicial, partidas y saldo final de cada cuenta", "bi-layers",
                (("Excel", "con_dmg_integracion"), ("Con análisis", "con_dmg_integracion_td"))),
        Reporte("Mayor por cliente o proveedor", "Cuentas por cobrar y por pagar abiertas por directorio",
                "bi-people", (("Excel", "con_dmg_integracion_x_directorio"),)),
    )),
    Categoria("balances", "Balances de saldos", "bi-list-check", (
        Reporte("Balance de saldos", "Saldo anterior, cargos, abonos y saldo actual del mes", "bi-list-check",
                (("Excel", "con_balance_de_saldos_xls"),)),
        Reporte("Balance de comprobación", "Cargos y abonos del mes por cuenta", "bi-check2-square",
                (("Excel", "con_balance_de_comprobacion_xls"),)),
        Reporte("Balance analítico", "Cuentas por cobrar y por pagar detalladas por directorio", "bi-search",
                (("Excel", "con_balance_de_saldos_analitico_xls"),)),
    )),
    Categoria("auditores", "Para auditores", "bi-file-earmark-text", (
        Reporte("Diario para auditores", "Todas las partidas en una tabla, en Excel o archivo de texto",
                "bi-file-earmark-text", (("Especial", "con_diario_especial_auditores"), ("PwC 2011", "con_diario_pwc_2011"),
                                         ("PwC 2013", "con_diario_pwc_2013"))),
    )),
    Categoria("fiscales", "Libros fiscales", "bi-bookmark-check", (
        Reporte("Libro de diario", "Libro autorizado: pólizas del mes con folio; hoja «sin movimientos» si no hay",
                "bi-journal-text", (("Imprimible", "con_libro_diario"), ("Resumido", "con_libro_diario_resumen_xls"))),
        Reporte("Libro mayor", "Libro autorizado del mes con folio y resolución", "bi-journals",
                (("Imprimible", "con_libro_mayor"), ("Resumido", "con_libro_mayor_resumen_xls"),
                 ("Movimientos diarios", "con_libro_mayor_resumen_xls_diario"),
                 ("DMG resumido por día", "con_dmg_resumido"))),
        Reporte("Libro de inventario", "Saldos de todas las cuentas con la utilidad del ejercicio", "bi-box-seam",
                (("Excel", "con_libro_inventario_xls"),)),
        Reporte("Libro de estados financieros", "Balance general y estado de resultados con folio y resolución",
                "bi-bank", (("Balance general", "con_libro_estfin_bg_xls"), ("Resultados", "con_libro_estfin_er_xls"),
                            ("Balance de saldos", "con_ef_balance_de_saldos_xls"))),
    )),
)

def _cuenta_corriente(pre, modulo, quien):
    """Categorías de CXC / CXP: los mismos formularios VFP con el prefijo del módulo."""
    return (
        Categoria(f"{pre}saldos", "Saldos y antigüedad", "bi-hourglass-split", (
            Reporte("Integración de saldos", f"Documentos con saldo por {quien}, con antigüedad y resumen",
                    "bi-hourglass-split", (("Moneda local", f"{pre}.con_ctacte_integracion"),
                                           ("Moneda extranjera", f"{pre}.con_ctacte_integracion_me"))),
            Reporte("Saldos positivos y negativos", f"Saldo de cada {quien} separando a favor y en contra",
                    "bi-plus-slash-minus", (("Excel", f"{pre}.con_ctacte_integracion_positivos_negativos"),)),
            Reporte("Proyección de " + ("cobros" if pre == "cxc" else "pagos"),
                    "Saldo de los documentos repartido por fecha de vencimiento", "bi-calendar-week",
                    (("Excel", f"{pre}.con_proyeccion_de_pago_xls"),)),
        ), modulo),
        Categoria(f"{pre}analisis", "Análisis", "bi-table", (
            Reporte("Tabla dinámica", "Hoja de datos con todos los documentos con saldo y análisis por días",
                    "bi-table", (("Excel", f"{pre}.con_ctacte_tabla_dinamica"),)),
            Reporte("Saldos para " + ("cobros" if pre == "cxc" else "pagos") + " electrónicos",
                    "Saldos por días de crédito para programar " + ("cobros" if pre == "cxc" else "pagos"),
                    "bi-credit-card", (("Excel", f"{pre}.con_ctacte_td_programacion_pe"),)),
            Reporte("Consulta de documentos", "Documentos con IVA, retenciones y timbre, por tipo",
                    "bi-search", (("Excel", f"{pre}.con_empdoctos_con_impuestos"),)),
        ), modulo),
        Categoria(f"{pre}estados", "Estados de cuenta", "bi-person-lines-fill", (
            Reporte("Estado de cuenta", "Saldo inicial, movimientos del periodo y saldo final", "bi-person-lines-fill",
                    (("Excel", f"{pre}.con_ctacte_estcta"), ("Imprimible", f"{pre}.con_ctacte_estcta_crystal"),
                     ("Para moras", f"{pre}.con_ctacte_estcta_moras"))),
            Reporte("Detalle por cuenta", f"Una hoja por {quien} con sus movimientos, más resumen", "bi-journal-text",
                    (("Excel", f"{pre}.con_ctacte_detalle"),)),
        ), modulo),
        Categoria(f"{pre}documentos", "Documentos", "bi-files", (
            Reporte("Documentos pendientes", "Documentos con saldo a una fecha", "bi-file-earmark-excel",
                    (("Excel", f"{pre}.con_ctacte_pendientes"),)),
            Reporte("Documentos aplicados", "Pagos y notas aplicados a cada documento", "bi-link-45deg",
                    (("Excel", f"{pre}.con_detalle_aplicados"),)),
            Reporte("Documentos no aplicados", "Documentos con saldo por aplicar, con antigüedad", "bi-link",
                    (("Excel", f"{pre}.con_documentos_no_aplicados"),)),
        ), modulo),
        Categoria(f"{pre}cuadres", "Cuadres", "bi-check2-square", (
            Reporte("Auxiliar vs. contabilidad", "Cargos y abonos por tipo de documento en ambos lados",
                    "bi-arrow-left-right", (("Excel", f"{pre}.det_conta_vrs_auxiliar"),)),
            Reporte("Aplicaciones incongruentes", "Documentos que reciben y dan aplicaciones, o sobreaplicados",
                    "bi-exclamation-triangle", (("Excel", f"{pre}.con_incongruencias_de_aplicaciones"),)),
            Reporte("Registros con problemas", "Documentos en moneda extranjera con tipo de cambio 1",
                    "bi-bug", (("Excel", f"{pre}.con_registros_con_problemas"),)),
        ), modulo),
    )


CATEGORIAS += _cuenta_corriente("cxc", "Cuentas por cobrar", "cliente") +     _cuenta_corriente("cxp", "Cuentas por pagar", "proveedor")

CATEGORIAS += (
    Categoria("ivalibros", "Libros de IVA", "bi-receipt", (
        Reporte("Libro de compras", "Documentos de compra del mes, resumen, verificación del IVA y proveedores",
                "bi-cart", (("Excel", "iva.con_iva_compras_xls"), ("Por documento de referencia",
                                                                 "iva.con_iva_compras_referencia_xls"))),
        Reporte("Libro de ventas", "Documentos de venta del mes (detalle o por día) y verificación del IVA",
                "bi-shop", (("Excel", "iva.con_iva_ventas_xls"),)),
        Reporte("Detalle de compras con combustibles", "Compras de combustible por tipo", "bi-fuel-pump",
                (("Excel", "iva.con_iva_combustibles_xls"),)),
        Reporte("Integración IVA crédito", "IVA de compras contra contabilidad", "bi-layers",
                (("Excel", "iva.con_iva_compras_integracion_xls"),)),
    ), "Auxiliares de IVA"),
    Categoria("ivasat", "Asiste Libros (SAT)", "bi-filetype-txt", (
        Reporte("Archivo de compras", "Archivo .asl para cargar en Asiste Libros de la SAT", "bi-filetype-txt",
                (("Versión 2", "iva.sat_iva_compras_2013"), ("Versión 1", "iva.sat_iva_compras"))),
        Reporte("Archivo de ventas", "Archivo .asl para cargar en Asiste Libros de la SAT", "bi-filetype-txt",
                (("Versión 2", "iva.sat_iva_ventas_2013"), ("Versión 1", "iva.sat_iva_ventas"))),
    ), "Auxiliares de IVA"),
)

CATEGORIAS += (
    Categoria("faccorrel", "Correlativos", "bi-list-ol", (
        Reporte("Correlativo de facturación", "Facturas del periodo con saltos de numeración, retenciones y pagos",
                "bi-list-ol", (("Excel", "fac.con_empdoctos_correl_fac"), ("Con vendedor", "fac.con_empdoctos_correl_fac_vendedor"),
                               ("Detallado", "fac.con_empdoctos_correl_fac_detallado"), ("ODS", "fac.con_empdoctos_correl_fac_gtl"))),
        Reporte("Facturas con retención de ISR", "5 % de ISR por factura y la retención recibida", "bi-percent",
                (("Excel", "fac.con_facturas_retencion_isr"),)),
    ), "Facturación"),
    Categoria("facanalisis", "Análisis de facturación", "bi-bar-chart", (
        Reporte("Facturación por cliente", "Mensual del año o comparativo contra el año anterior", "bi-people",
                (("Mensual", "fac.con_fac_mensual_por_cli"), ("Anual comparativo", "fac.con_fac_anual_comparativo_cli"))),
        Reporte("Facturación por cuenta contable", "Una columna por cuenta contable de los conceptos facturados",
                "bi-columns-gap", (("Excel", "fac.con_facturacion_concepto_x_cuenta"),)),
        Reporte("Ingresos finales", "Ingresos confirmados por factura", "bi-cash-stack",
                (("Excel", "fac.con_ingresos_finales"),)),
    ), "Facturación"),
    Categoria("facagencia", "Agencia", "bi-megaphone", (
        Reporte("Consecutivo de facturación", "Líneas por producción, arte y medios con su proveedor", "bi-list-check",
                (("Excel", "fac.con_consecutivo_facturacion"),)),
        Reporte("Cuadros finales", "Medios, producción, arte y otros por cliente y marca", "bi-grid-3x3",
                (("Excel", "fac.con_facturacion_medios_produccion"),)),
    ), "Facturación"),
)

CATEGORIAS += (
    Categoria("chqbancos", "Bancos", "bi-bank2", (
        Reporte("Estado de cuenta bancario", "Movimientos de la cuenta con saldo corrido", "bi-journal-text",
                (("Excel", "chq.con_dm_estcta"), ("Detallado", "chq.con_dm_estcta_detallado"))),
        Reporte("Correlativo de documentos", "Documentos de la cuenta por tipo con saltos de numeración", "bi-list-ol",
                (("Por cuenta", "chq.con_dm_tipodoc"), ("General", "chq.con_empdoctos_correl"))),
        Reporte("Cheques autorizados / entregados", "Cheques por estado de autorización o entrega", "bi-check2-square",
                (("Autorizados", "chq.con_chq_autorizado_xls"), ("Entregados", "chq.con_chq_entregados_xls"))),
        Reporte("Pendientes de número", "Registros aún no impresos", "bi-hourglass-split",
                (("Excel", "chq.con_chq_pendientes_numero_xls"),)),
    ), "Caja y bancos"),
    Categoria("chqconci", "Conciliación y disponibilidad", "bi-check2-all", (
        Reporte("Conciliación bancaria", "Saldo según banco contra libros, con movimientos en circulación",
                "bi-check2-all", (("Excel", "chq.con_dm_conci"), ("AECID", "chq.con_dm_conci_aecid"))),
        Reporte("Disponibilidad", "Saldo de las cuentas a la fecha", "bi-wallet2", (("Excel", "chq.con_dm_dispo"),)),
        Reporte("Posición bancaria", "Saldos, ajustes y conciliación de todas las cuentas del tipo", "bi-clipboard-data",
                (("Excel", "chq.con_dm_posicion"),)),
    ), "Caja y bancos"),
    Categoria("chqflujo", "Flujo de fondos", "bi-water", (
        Reporte("Flujo de fondos", "Bancos más cuentas por cobrar menos cuentas por pagar a la fecha", "bi-water",
                (("A la fecha", "chq.con_flujo_de_fondos_xls"), ("Proyectado", "chq.con_flujo_de_fondos_proyectado_xls"))),
        Reporte("Origen y aplicación de fondos", "Entradas y salidas por clasificación, mes a mes", "bi-arrow-left-right",
                (("Excel", "chq.con_origen_aplicacion_fondos"),)),
    ), "Caja y bancos"),
    Categoria("chqcajas", "Cajas", "bi-safe", (
        Reporte("Estado de cuenta de caja", "Movimientos de la caja con saldo corrido", "bi-journal-text",
                (("Caja chica", "chq.con_cc_estcta"), ("Caja liquidación", "chq.con_cl_estcta"))),
        Reporte("Cajas por liquidar", "Gastos de caja pendientes de liquidación", "bi-receipt",
                (("Excel", "chq.con_cl_detalle_cc"),)),
        Reporte("Liquidación de caja chica", "Documentos de la liquidación con su cuenta contable", "bi-receipt-cutoff",
                (("Excel", "chq.con_liquidacion_caja_chica"),)),
        Reporte("Resumen de cuentas de caja", "Gastos de caja por cuenta contable", "bi-table",
                (("Excel", "chq.con_cc_resumen_ctas_doc_apl"),)),
    ), "Caja y bancos"),
    Categoria("chqrecibos", "Recibos y depósitos", "bi-receipt", (
        Reporte("Recibos aplicados", "Cada recibo con los documentos que cancela y su saldo", "bi-receipt",
                (("Correlativo", "chq.con_correlativo_recibos"), ("Movimiento", "chq.con_recibos_aplicados"))),
        Reporte("Recibos y depósitos", "Recibos de la caja con el depósito en que se aplicaron", "bi-bank",
                (("Por recibo", "chq.con_correlativo_recibos_depositos"),
                 ("Por depósito", "chq.con_depositos_aplicados_recibos"))),
    ), "Caja y bancos"),
)

CATEGORIAS += (
    Categoria("acfdeprec", "Depreciaciones", "bi-graph-down", (
        Reporte("Cálculo de depreciaciones", "Depreciación del mes y acumulada por activo, con la póliza del mes",
                "bi-graph-down", (("Excel", "acf.con_calculo_dep_xdia_acfijos"),)),
    ), "Activos fijos"),
    Categoria("acflistados", "Listados de activos", "bi-box-seam", (
        Reporte("Activos por tipo", "Activos vigentes por tipo de activo (y responsable), con proveedor y factura",
                "bi-tags", (("Excel", "acf.con_acfactivos_tipos"),)),
        Reporte("Artículos por responsable", "Activos a cargo de cada responsable, con sus accesorios", "bi-person-badge",
                (("Excel", "acf.con_articulos_x_responsable"),)),
        Reporte("No depreciables, de baja y con accesorios", "Listados por situación del activo", "bi-list-check",
                (("No depreciables", "acf.con_articulos_no_depreciables"), ("Dados de baja", "acf.con_articulos_dados_de_baja"),
                 ("Con accesorios", "acf.con_articulos_con_accesorios"))),
    ), "Activos fijos"),
    Categoria("acfresponsables", "Responsabilidad e inventario", "bi-clipboard-check", (
        Reporte("Ficha de responsabilidad", "Tarjeta de un responsable con sus activos y línea de firma",
                "bi-person-vcard", (("Ficha", "acf.con_inventario_tarjeta_responsa"),
                                    ("Formato AECID", "acf.con_inventario_ficha_responsa_aecid"))),
        Reporte("Inventario de bienes muebles (AECID)", "Inventario en buen estado o de baja, con responsables y serie",
                "bi-clipboard-data", (("Buen estado", "acf.con_inventario_aecid"),
                                      ("De baja", "acf.con_inventario_debaja_aecid"))),
    ), "Activos fijos"),
)

_UBICACION = {cmd: (cat, rep, etiqueta) for cat in CATEGORIAS for rep in cat.reportes
              for etiqueta, cmd in rep.variantes}


def ubicar(comando):
    """(Categoria, Reporte, etiqueta de la variante) del comando, o None si no está catalogado."""
    return _UBICACION.get(comando)


def menu_usuario(opciones, migrados):
    """
    Categorías con los reportes que el usuario puede abrir.
    `opciones`: filas de vfmenuc permitidas (dicts con comando y descri); `migrados`: comandos disponibles.
    """
    permitidos = {op["comando"]: op for op in opciones if op["comando"] and op["comando"] in migrados}
    salida = []
    for cat in CATEGORIAS:
        reportes = []
        for rep in cat.reportes:
            variantes = [{"etiqueta": e, "comando": c} for e, c in rep.variantes if c in permitidos]
            if variantes:
                reportes.append({"nombre": rep.nombre, "descripcion": rep.descripcion, "icono": rep.icono,
                                 "variantes": variantes})
        if reportes:
            salida.append({"clave": cat.clave, "nombre": cat.nombre, "icono": cat.icono, "reportes": reportes,
                           "modulo": cat.modulo})
    otros = [{"nombre": op["descri"], "descripcion": "", "icono": "bi-file-earmark",
              "variantes": [{"etiqueta": "Abrir", "comando": c}]}
             for c, op in permitidos.items() if c not in _UBICACION]
    if otros:
        salida.append({"clave": "otros", "nombre": "Otros", "icono": "bi-three-dots", "reportes": otros,
                       "modulo": "Otros"})
    return salida


def hermanas(comando, permitidos):
    """Variantes del mismo reporte que el usuario puede abrir (para cambiar de una a otra)."""
    lugar = ubicar(comando)
    if not lugar:
        return None, []
    _, rep, _ = lugar
    return rep, [{"etiqueta": e, "comando": c, "actual": c == comando} for e, c in rep.variantes if c in permitidos]

# Tablero CFO (financieros/tablero_cfo.py): se muestra a quien tiene estos reportes en su menú
TABLERO_REQUIERE = ("con_bg_xls", "con_er_xls")

# Paquete de la junta directiva (financieros/junta.py): (comando, nombre corto), en orden
REPORTES_JUNTA = (
    ("con_presupuesto_er_xls", "Ejecución presupuestaria"),
    ("cxc.con_ctacte_integracion", "Cuentas por cobrar"),
    ("con_bg_comparativo_periodo_xls", "Balance general comparativo"),
    ("con_er_comparativo_periodo_xls", "Estado de resultados comparativo"),
)
