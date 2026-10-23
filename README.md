# ADMSys · Reportes financieros (Django)

Migración de los reportes del menú **20 – Estados Financieros** (`vfmenuc`, CONTA/CNT)
del sistema Visual FoxPro 9 (`ADMSYS.PJX`) a Django, leyendo la misma base `vadmon`.

## Puesta en marcha

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
copy .env.example .env      # y completar VADMON_USER / VADMON_PASSWORD
.venv/Scripts/python manage.py runserver 8010
```

Ingresar en http://localhost:8010 con un usuario del sistema (tabla `vfusers`).

* No hay migraciones: no se crean tablas en `vadmon`; solo se hacen `SELECT`.
* Las sesiones van en cookie firmada (`DJANGO_SECRET_KEY` debe ser fija en producción).
* Se usa PyMySQL directo (no el ORM) porque Django 5.2 no soporta MariaDB 10.4.
* Interfaz: Bootstrap 5.3.3, Bootstrap Icons 1.11.3, SweetAlert2 11.14.5 y fuente Inter,
  cargados desde CDN (jsdelivr / Google Fonts). Estilos y JS propios en `core/static/core/`.
  Si el servidor de producción no tiene salida a internet, descargar esos archivos a
  `core/static/` y cambiar las rutas en `templates/base.html`. En producción
  (`DJANGO_DEBUG=0`) servir estáticos con `collectstatic` + IIS/nginx o WhiteNoise.
* El Excel se descarga por `fetch`: la página muestra una alerta de progreso y, al terminar,
  el tiempo de generación; los errores (sin datos, sin periodo) se muestran en una alerta.

## Ambientes: desarrollo y producción

* **Producción**: servidor y base del cliente → `.env.servirsa` (fuera del repositorio).
* **Desarrollo**: MariaDB `localhost:3306`, base `vadmon` → `.env`. Sus datos no son los de
  producción, así que los totales pueden diferir.

Toda conexión se abre en modo solo lectura (`SET SESSION TRANSACTION READ ONLY` en `core/db.py`):
el servidor rechaza cualquier escritura aunque el usuario tenga permisos. En producción el usuario
`consulte` tiene ALL PRIVILEGES sobre la base; para la puesta en marcha conviene un usuario con
solo `SELECT` sobre las tablas que leen los reportes.

`ADMSYS_AMBIENTE` (`desarrollo` | `pruebas` | `produccion`) muestra una franja arriba de cada página
con el ambiente y la base conectada (amarilla en desarrollo, roja en producción) y antepone
`[DES]` / `[PROD]` al título de la pestaña. Vacía o sin definir, no se muestra: al publicar en
producción basta con no definirla.

`ADMSYS_ENV` elige el archivo de ambiente (por defecto `.env`). Para correr contra producción:

```bash
set ADMSYS_ENV=.env.servirsa
.venv/Scripts/python manage.py runserver 8013
```

Cada ambiente usa su propia cookie de sesión (`DJANGO_SESSION_COOKIE`), así dos servidores
locales contra bases distintas no se pisan la sesión. Los archivos `.env.*` no se versionan
(salvo `.env.example`).

Contra un servidor remoto cada consulta cuesta una ida y vuelta (~160 ms a Servirsa) y abrir una
conexión 1.2–1.6 s; por eso `core/db.py` reutiliza conexiones (pool de 4) y empresa, periodo,
definición de nomenclatura y permisos del menú se guardan unos minutos en memoria.

## Menú principal

El menú no copia la estructura de `vfmenuc`: agrupa los reportes por tarea contable en
`financieros/catalogo.py` (Estados financieros, Comparativos, Centros de costo, Presupuesto,
Libro diario, Mayor e integración, Balances de saldos, Para auditores). Cada reporte es un renglón
con su descripción y sus variantes como botones (Excel · En columnas · Imprimible, Balance ·
Resultados…). Solo se muestran las variantes del menú del usuario que ya están migradas; los
pendientes no aparecen.

**Permisos (misma regla que el menú de VFP, `_sys_mgr.prg` → `llenarmenu`):** una opción aparece solo
si el usuario tiene su fila en `vfaccess` para la empresa en la que entró, con `imprime = 1`.
`vfaccessperfiles` es la plantilla del perfil que VFP copia a `vfaccess` al asignar el perfil
(`_carga_user.prg`): no da acceso por sí sola. La marca `supervisor` de `vfusers` tampoco abre
opciones (en VFP solo da derechos de modificar / anular en los mantenimientos). La vista de cada
reporte valida el mismo permiso, así que no se entra por URL a un reporte no asignado. Antes la web
unía el perfil y le mostraba todo a los supervisores: 28 de 46 usuarios activos de Servirsa veían
reportes que en VFP no tienen (p. ej. un usuario de cobros: 4 en VFP y 156 en la web); verificado: los 46 ven
ahora exactamente lo mismo que en VFP.

* Búsqueda en todas las categorías, sin distinguir acentos; Enter abre el primer resultado.
* «Recientes»: los últimos 3 reportes generados (en la sesión), abren con el mismo año y mes.
* La categoría elegida se recuerda en el navegador y se puede enlazar (`/#mayor`).
* Un comando nuevo de `vfmenuc` que no esté en el catálogo aparece en «Otros»; para ubicarlo,
  agregarlo a `CATEGORIAS`.

## Reportes migrados

| comando (`vfmenuc`) | Reporte | Función |
|---|---|---|
| `con_bg_xls` | Balance General | `reportes.balance_general` |
| `con_er_xls` | Estado de Resultados | `reportes.estado_resultados` |
| `con_bg_xls-01` | Balance General en columnas | `reportes.balance_general_columnas` |
| `con_er_xls-01` | Estado de Resultados en columnas | `reportes.estado_resultados_columnas` |
| `con_bg` / `con_er` | Versiones Crystal → vista en pantalla imprimible (nivel 3 / 5 por defecto, folio) | mismas funciones |
| `con_cta_orden_xls` | Cuentas de Orden | `reportes.cuentas_de_orden` |
| `con_bg_comp_anual_xls` | BG mensual por periodo | `reportes.balance_mensual` |
| `con_er_comp_anual_xls` | ER mensual por periodo | `reportes.estado_resultados_mensual` |
| `con_bg_comparativo_xls` | BG comparativo mes anterior | `reportes.balance_comparativo_mes_anterior` |
| `con_er_comparativo_xls` | ER comparativo mes anterior | `reportes.estado_resultados_comparativo_mes_anterior` |
| `con_bg_comparativo_periodo_xls` | BG comparativo periodo | `reportes.balance_comparativo_periodo` |
| `con_er_comparativo_periodo_xls` | ER comparativo periodo | `reportes.estado_resultados_comparativo_periodo` |

### Menú 22 – Centros de Costo (C.C.)

| comando | Reporte | Función (`reportes_cc.py`) |
|---|---|---|
| `con_er_cc_xls` | ER por centro de costo (una columna por centro + TOTAL) | `estado_resultados_por_centro` |
| `con_er_cc_individual_xls` | ER de un rango de centros | `estado_resultados_centro_individual` |
| `con_er_cc_sub_xls` | ER por subcentro (columnas agrupadas por cuenta de acumulación) | `estado_resultados_subcentros` |
| `con_er_cc_comp_anual_xls` | ER por cuenta, mensual, de un centro | `estado_resultados_cuenta_mensual_centro` |
| `con_er_cc_comparativo_anual_xls` | Resultado de cada centro por mes | `estado_resultados_centros_por_mes` |
| `con_balance_saldo_por_cc_xls` | Balance de saldos por centro | `balance_saldos_por_centro` |
| `con_presupuesto_cc_er_xls` | ER vs. presupuesto por centros seleccionados | `estado_resultados_vs_presupuesto_centro` |
| `con_presupuesto_cc_xls` | Libro: hoja RESUMEN (con ISR) + una hoja por centro, o una hoja combinada | `presupuesto_cc.presupuesto_por_centro` |
| `con_presupuesto_cc_cons_xls` | Consolidado de los centros seleccionados | `presupuesto_cc.presupuesto_consolidado` |
| `con_mov_centro_costo` | Movimiento por C.C. (Crystal): partidas por centro con saldo corrido | `libros.movimiento_por_centro` |
| `con_dmg_cc` | Detalle por C.C. (Crystal DMG_CC): por cuenta y centro, saldo corrido | `libros.mayor_por_centro` |

### Menú 24 – Centros de Integración (C.I.)

Los reportes por centro son genéricos por **dimensión** (`Definicion.dimension`: `cc`, `ci`, `ca`):
el motor de saldos (`FiltroMovs.dim`), el catálogo de centros (`ctx.centros(..., dim)` sobre
`empcc` / `empci` / `empca`), el presupuesto (`empnomenclaturadet.codcc / codci / codca`), el mayor por
centro y el formulario usan la columna de la dimensión; `ctx.rotula` cambia «CENTRO DE COSTO» / «C.C.»
de títulos y totales. Los comandos de C.I. se registran a partir de los del menú 22 (`_ci` en
`registro.py`); VFP tenía un formulario copiado por dimensión.

| comando | Equivale a (menú 22) |
|---|---|
| `con_er_ci_xls`, `con_er_ci_individual_xls`, `con_er_ci_sub_xls` | `con_er_cc_xls`, `_individual_xls`, `_sub_xls` |
| `con_er_ci_comp_anual_xls` | `con_er_cc_comp_anual_xls`, con rango de centros en vez de uno |
| `con_balance_saldo_por_ci_xls`, `con_mov_centro_integracion`, `con_dmg_ci` | `con_balance_saldo_por_cc_xls`, `con_mov_centro_costo`, `con_dmg_cc` |
| `con_presupuesto_ci_er_xls`, `con_presupuesto_ci_xls`, `con_presupuesto_ci_cons_xls` | los de presupuesto por C.C. |
| `con_bg_ci_xls` | nuevo: Balance General con una columna por centro (`reportes_cc.balance_general_por_centro`); la utilidad de cada centro va a la cuenta de ganancia en su columna |

**Sin datos para validar:** en Servirsa (producción) y en desarrollo `empci` / `empca` están vacías y
ningún movimiento tiene `codci` / `codca`. Validación por equivalencia (003, dic-2016): cada reporte de
C.I. ejecutado con la dimensión C.C. da exactamente las mismas filas y valores que su par del menú 22,
y el BG por centro suma lo mismo que el Balance General (12,699,572.15). Con los datos reales, los
reportes que agrupan por centro muestran una sola columna «(SIN CENTRO)», igual que VFP.
No encontrados: `con_dmg_ci_interperiodo`, `con_mov_centro_integracion_det_cuenta_td`.

### Menú 26 – Centros de Actividad (C.A.)

`con_dmg_ca` y `con_mov_centro_actividad`: los mismos `con_dmg_cc` / `con_mov_centro_costo` con
dimensión `ca` (codca / empca). Detalles de las plantillas Crystal que se respetan en el movimiento
por centro: la columna de detalle es el C.I. en C.C. y C.A. (en `Movimiento por Centro de
Actividad.rpt` el encabezado dice «C.Actividad» pero el campo es `codci`); la de C.I. no tiene esa
columna y no excluye los movimientos sin centro (C.C. y C.A. sí: `codca <> ''`). Validación por
equivalencia (003, dic-2016): con dimensión C.C. dan las mismas filas que los del menú 22. Sin datos
de C.A. en Servirsa.

### Menú 30 – Libros Fiscales

Los libros autorizados usan el mismo cálculo que los reportes ya migrados (`libros_fiscales.py`
describe de dónde sale cada uno). Lo propio de los libros: folio inicial, número de **resolución**
de la SAT en el encabezado de cada hoja impresa y, si el mes no tiene movimientos, la hoja
«Período sin movimientos…» con totales en cero (la numeración de folios no se corta).

| comando | Reporte | Función |
|---|---|---|
| `con_libro_diario` | Libro de Diario (Crystal): las 5 presentaciones; «consolida tipo» con centros | `libros.libro_diario` |
| `con_libro_diario_resumen_xls` / `con_libro_mayor_resumen_xls` | Diario / Mayor resumido: balance del mes por nomenclatura | `balances.libro_diario_resumido` / `libro_mayor_resumido` |
| `con_libro_mayor` | Libro Mayor (Crystal Dmg_Nombre) del mes, todas las cuentas | `libros.libro_mayor` |
| `con_libro_mayor_resumen_xls_diario` | Mayor resumido con los cargos y abonos de cada día bajo la cuenta | `balances.libro_mayor_por_dia` |
| `con_dmg_resumido` | Diario Mayor General con un renglón por día (Crystal DMG_Nombre_Resumido) | `libros.diario_mayor_resumido` |
| `con_libro_inventario_xls` | Balance con la utilidad del ejercicio en la cuenta de ganancia | `balances.libro_inventario` |
| `con_libro_estfin_bg_xls` / `con_libro_estfin_er_xls` | Balance General / Estado de Resultados, del mes o acumulado | `libros_fiscales.py` |
| `con_ef_balance_de_saldos_xls` | Balance de Saldos con folio | `libros_fiscales.balance_de_saldos_ef` |

Validado (003, sep-2026): cargos = abonos = 8,656,770.24 en diario, mayor y resumidos; la suma de
los días del mayor diario = cargos del mes; el libro de BG = Balance General; la ganancia del
Libro de Inventario (1,443,545.82) = resultado del ER. Enero: la apertura va al saldo anterior en
los resumidos. Diciembre (sin movimientos): hoja «sin movimientos» con el folio. 0.05–0.5 s.

### Menú 35 – Libros Auxiliares

| comando | Reporte | Función (`integracion.py`) |
|---|---|---|
| `con_dmg_integracion` | Integración de cuentas: saldo inicial, partidas y saldo final por cuenta | `integracion_cuentas` |
| `con_dmg_integracion_td` | Hoja "Datos" (Tabla de Excel) + hoja "Analisis" (cuenta principal > cuenta × módulo) | `integracion_cuentas_td` |

| `con_balance_de_saldos_xls` / `con_balance_de_comprobacion_xls` / `con_balance_de_saldos_analitico_xls` | Balances de saldos, comprobación y analítico (CXC/CXP por directorio) | `balances.py` |
| `con_dmg_integracion_x_directorio` | Libro mayor resumido: balance del mes, CXC/CXP abiertas por directorio, documentos de bancos | `balances.mayor_por_directorio` |
| `con_dmg` | Diario Mayor General (Crystal Dmg_Nombre): saldo corrido por cuenta y mes | `libros.diario_mayor_general` |
| `con_estado_cta_contable` | Estado de cuenta contable (con C.C./C.I., sin anuladas) | `libros.estado_cuenta_contable` |
| `con_dmg_xls` | DMG Excel: nomenclatura completa, saldo inicial, partidas, saldo final, SUBTOTAL por mayor | `libros.diario_mayor_general_xls` |
| `con_diario` | Libro Diario: por transacción o consolidado (fecha / tipo doc. / sin C.C.), folio | `libros.diario` |
| `con_diario_especial_auditores`, `con_diario_pwc_2011`, `con_diario_pwc_2013` | Diarios planos para auditores (Excel o archivo de texto) | `libros.py` |

Validado (003, sep 2026): cargos = abonos = 6,403,536.19 en Diario (5 presentaciones), DMG y DMG Excel.

Validado (003, jul–sep y ene–sep 2026): el saldo final de cada una de las 135 cuentas
coincide con el motor de saldos del BG/ER, y cargos = abonos.

Las cuentas de la sección Inversión del presupuesto se leen de la plantilla VFP
`Ejecucion Presupuestaria Detalle.xls` (carpeta `VFP_PLANTILLAS_DIR` del `.env`), porque
son fijas por cliente; si la plantilla no está accesible se usa `PRESUPUESTO_CUENTAS_INVERSION`.

Validado con datos reales (003 dic-2016, KOW dic-2017): la columna TOTAL del ER por
centro, el ER individual, el de subcentros y el mensual por centro dan exactamente el
resultado del ER general.

Pendientes:
* `con_bg_ref_me_xls`, `con_mov_centro_costo_dmg_td`, `con_mov_centro_costo_det_cuenta_td`,
  `con_dmg_cc_plano`: están en `vfmenuc` pero sus formularios no existen en las copias revisadas.

### Menú 40 – Presupuesto

| Comando VFP | Reporte |
|---|---|
| `con_presupuesto` | Presupuesto general: presupuesto de cada cuenta de resultados mes a mes hasta el corte, con total |
| `con_presupuesto_er_xls` | Ejecución presupuestaria (E.R.): mes y acumulado, presupuesto vs. real, de toda la empresa |

Mismo cálculo que el de centros de costo (`empnomenclaturadet`, sumando todos los centros). Validado
contra VFP (003 sep-2026): presupuesto general 55 cuentas sin diferencias; ejecución presupuestaria
67 cuentas, los montos reales iguales.

### Tablero CFO

Botón **Tablero CFO** en el menú principal (`/financieros/tablero-cfo/`), para quien tiene el
balance general y el estado de resultados en su menú. Muestra 20 indicadores de los 12 meses que
terminan en el cierre elegido, en cinco pestañas (resumen con semáforo, rentabilidad, cartera,
solvencia, eficiencia NIIF 15 / 16), la matriz de indicadores, tres alertas al CFO y descarga a
Excel con fórmulas vivas. Cálculo en `financieros/tablero_cfo.py`, Excel en `tablero_cfo_excel.py`.

* Cuentas de cada variable en `CUENTAS` (prefijos de 003; otra empresa agrega su entrada).
* Cartera neta: clientes CP / LP y contratos en arrendamiento CP / LP, sin estimaciones, menos
  ingresos por devengar. Morosidad: facturas y notas de débito de CXC vencidas (las notas de
  reclasificación y los pagarés no tienen el vencimiento de las cuotas).
* Rentabilidad, márgenes y coberturas: flujos de 12 meses sobre el saldo promedio de 13 cierres.
  Resultado antes de ISR (no se registra mensualmente).
* DSCR: EBITDA aproximado ÷ (intereses + capital pagado desde bancos, sin reclasificaciones LP / CP
  ni diferencial cambiario).
* Desviación del valor residual: no aplica sin arrendamiento operativo.
* Saldos solo de cuentas de último nivel: activo, pasivo y patrimonio de sep-2026 (003) iguales al
  balance general. Primera carga del mes ~16 s (12 antigüedades de CXC); después, caché de 30 min.

### Paquete de la junta directiva

En el menú principal, **Paquete junta directiva** (`/financieros/junta/`) descarga un .zip con los
reportes del cierre elegido; solo incluye los que el usuario tiene en su menú y, si alguno falla, lo
explica en `NO GENERADOS.txt`. El mismo paquete, a una carpeta local:

```bash
python manage.py reportes_junta --empresa 003 --anio 2026 --mes 9 --carpeta "D:/Junta directiva"
```

Genera a Excel, en `<carpeta>/Junta <empresa> <año>-<mes>`, los reportes que la gerencia general
presenta a la junta (`financieros/junta.py`): ejecución presupuestaria (E.R.), integración de saldos
de CXC, y balance general y estado de resultados comparativos contra el mismo mes del año anterior.
Cada uno sale con los valores iniciales de su formulario, igual que pedido a mano. Sin `--anio` /
`--mes` toma el mes anterior; sin `--carpeta`, `Documentos\Junta directiva`.

### Cuentas por cobrar y por pagar (menú 50 de CTAXC / CTAXP)

El mismo formulario VFP sirve a CXC y a CXP; en la web el comando lleva el prefijo del módulo
(`cxc.con_ctacte_integracion`, `cxp.con_ctacte_integracion`) y los permisos salen de `vfaccess`
con `directorio` CTAXC / CTAXP. Los reportes piden fechas (no año / mes).

| comando | Reporte | Función (`reportes_ctacte.py`) |
|---|---|---|
| `con_ctacte_integracion` | Integración de saldos: documentos con saldo por clasificación y directorio, antigüedad configurable + hoja RESUMEN con porcentajes | `integracion` |
| `con_ctacte_integracion_me` | Ídem con saldos en moneda extranjera | `integracion_me` |
| `con_ctacte_integracion_positivos_negativos` | Saldo neto por directorio con antigüedad fija, separando positivos y negativos | `positivos_negativos` |
| `con_ctacte_estcta` | Estado de cuenta: saldo inicial, movimientos del rango, saldo final; opcional comparación con contabilidad | `estado_cuenta` |
| `con_ctacte_estcta_crystal` (solo CXC) | Estado de cuenta para enviar al cliente, por cliente base | `estado_cuenta_cliente` |
| `con_ctacte_estcta_moras` (solo CXC) | Saldo en mora por cliente | `estado_cuenta_moras` |
| `con_ctacte_detalle` | Una hoja por directorio con sus movimientos + RESUMEN con vínculos | `detalle_por_cuenta` |
| `con_ctacte_pendientes` | Cobros / pagos pendientes por directorio o por mes de vencimiento | `documentos_pendientes` |
| `con_detalle_aplicados` | Documentos pagados: lo aplicado de cada documento, con antigüedad | `documentos_aplicados` |
| `con_proyeccion_de_pago_xls` | Proyección de cobros / pagos: saldo por fecha de vencimiento (vencido, N periodos, posterior) | `proyeccion` |
| `con_documentos_no_aplicados` | Documentos con saldo por aplicar, con antigüedad por fecha del documento | `no_aplicados` |
| `con_ctacte_tabla_dinamica` | Hoja «Datos» (Tabla de Excel) + «Analisis» por días de crédito, con sucursal | `tabla_dinamica` |
| `con_ctacte_td_programacion_pe` | Saldos para cobros / pagos electrónicos (ídem, tramos configurables) | `saldos_pagos_electronicos` |
| `con_empdoctos_con_impuestos` (solo CXP) | Consulta de documentos con IVA, retención ISR y timbre | `consulta_documentos` |
| `det_conta_vrs_auxiliar` | Cuadre: contabilidad (cuentas de control) vs. auxiliar por tipo de documento | `auxiliar_vs_contabilidad` |
| `con_incongruencias_de_aplicaciones` | Documentos que reciben y dan aplicaciones, o sobreaplicados | `aplicaciones_incongruentes` |
| `con_registros_con_problemas` | Documentos en moneda extranjera con tipo de cambio 1 | `registros_con_problemas` |

No encontrados en las copias revisadas: `con_pagos_recibidos_por_vendedor_xls` (CXC),
`con_integracion_opay_danmar` (CXP); `con_ctacte_detalle_tasacambio` (CXP) solo existe como
`…-eliminar.scx`.

Rendimiento: los reportes que solo usan documentos con saldo piden a la base únicamente los
pendientes (`documentos(..., solo_pendientes=True)`); Estado de cuenta, Moras y Detalle por cuenta
suman el saldo anterior por directorio en la base (`saldos_anteriores`) y traen solo los
documentos del rango. Resultados idénticos celda por celda; contra producción, de 7–9 s a 2–4 s.

El saldo de cada documento sale de `financieros/ctacte.py`, equivalente a
`sistema\prgs\_saldos_cuenta_corriente.prg`. Validado contra la consulta original de VFP (con la
vista `v_transacciones_aplica`) documento por documento: 0 diferencias en 70 mil documentos
(003 y KOW, CXC y CXP, cortes dic-2025 y sep-2026). Los parámetros
`recalcular_valordocml_en_estado_de_cuenta_cc` y `mostrar_valores_negativos_integracion_cuentas`
se leen de la tabla `parametros` como en VFP.

### Auxiliares de IVA (menús 50, 52 y 55 del sistema IVA)

Comandos con prefijo `iva.`; permisos de `vfaccess` con `directorio` / `codsys` IVA. Piden año y mes.

| comando | Reporte | Función (`reportes_iva.py`) |
|---|---|---|
| `con_iva_compras_xls` | Libro de compras: documentos del mes por tipo (bienes, combustibles, servicios, importación por documento, pequeño contribuyente), cuadre de IVA y total manual, llaves, retenciones; resumen de documentos; hojas «Resumen» (verificación del IVA) y «Resumen Proveedores» | `libro_compras` |
| `con_iva_ventas_xls` | Libro de ventas en detalle o resumido por día / tipo / serie; exportaciones por documento; hoja «Resumen» | `libro_ventas` |
| `con_iva_compras_referencia_xls` | Compras agrupadas por cuenta de cheques y documento que las aplica | `compras_referencia` |
| `sat_iva_compras_2013` / `sat_iva_ventas_2013` | Archivo `AAAAMMC.asl` / `AAAAMMV.asl` para Asiste Libros versión 2 (se descarga) | `sat_compras_2013` / `sat_ventas_2013` |
| `sat_iva_compras` / `sat_iva_ventas` | Ídem versión 1 (con encabezado `NIT|01|MM|AAAA`) | `sat_compras` / `sat_ventas` |

Las filas salen de `libro_comprasventas` (captura manual) más `transacciones` del mes, con las
mismas fórmulas de cada versión del INSERT de VFP (`VARIANTES`). El libro se arma en memoria:
VFP creaba una tabla en el servidor y **modificaba datos reales en cada corrida**, cosa que aquí no
ocurre (ver «Diferencias intencionales»). No se migró la versión 2012. No encontrados:
`con_iva_combustibles_xls` y `con_iva_compras_integracion_xls` (solo existen sus plantillas).

Validación: el IVA del libro coincide con la suma directa de transacciones + libro manual
(003 sep-2026, KOW abr-2026); la «diferencia contabilidad» de la verificación queda en centavos
de redondeo. Producción (003 sep-2026): 0.6–1.6 s por reporte.

### Facturación (menús 50 y 55 del sistema FAC)

Comandos con prefijo `fac.`; permisos de `vfaccess` con `directorio` FACTURA / `codsys` FAC. Piden
fechas, salvo el mensual y el anual por cliente (año / mes).

| comando | Reporte | Función (`reportes_fac.py`) |
|---|---|---|
| `con_empdoctos_correl_fac` | Correlativo: documentos por tipo con saltos de numeración en rojo, retención de IVA calculada (`_calcula_retenciones`), ISR 5 %, exención, neto a pagar, pagos y retenciones aplicadas, centro de renta | `correlativo` |
| `con_empdoctos_correl_fac_vendedor` | Ídem con cliente base, vendedor y comisión (tabla de comisiones) | `correlativo_vendedor` |
| `con_empdoctos_correl_fac_detallado` | Ídem con las líneas de cada factura | `correlativo_detallado` |
| `con_empdoctos_correl_fac_gtl` | Correlativo [ODS]: neto, IVA, cuenta ajena (y datos de `gtl_mov` si existe); detallado por línea | `correlativo_ods` |
| `con_facturas_retencion_isr` | 5 % de ISR por factura, quién retiene y retención recibida | `retencion_isr` |
| `con_fac_mensual_por_cli` / `con_fac_anual_comparativo_cli` | Facturación por cliente: meses del año / año actual vs. anterior | `mensual_por_cliente` / `anual_comparativo` |
| `con_facturacion_concepto_x_cuenta` | Una columna por cuenta contable de los conceptos facturados | `concepto_x_cuenta` |
| `con_consecutivo_facturacion` / `con_facturacion_medios_produccion` | Agencia: consecutivo por tipo de medio; cuadros finales por cliente y marca | `consecutivo` / `medios_produccion` |

`_calcula_retenciones.prg` está portado como `Retenciones` (tablas `comretenciones`,
`comretenciones_tabla`, datos del directorio y de la empresa leídos una vez). No encontrado:
`con_ingresos_finales` (solo está en el menú). En Servirsa no existen `gtl_mov` ni
`v_liq_tipsubmedio`: en VFP el ODS y los cuadros finales fallan; aquí funcionan sin esos datos.
Validación: cargos / abonos del correlativo iguales a la suma directa (003, sep-2026 y año 2025).
Producción: 0.4–2.9 s por reporte (VFP: ~8 consultas por factura en el correlativo).

### Caja y bancos (menús 12, 20, 50, 54, 55, 60, 62 y 85 del sistema CHQ)

Comandos con prefijo `chq.`; permisos con `directorio` CHEQUES / `codsys` CHQ. La lista de cuentas
respeta `chqdefctas.usa_control_de_usuarios` / `chqdefctas_users` y solo muestra las del tipo que
usa el reporte (DM / AH bancos, CC caja chica, CL caja liquidación, CR caja de recibos).

| comando | Reporte | Función (`reportes_chq.py`) |
|---|---|---|
| `con_dm_estcta` / `con_dm_estcta_detallado` | Estado de cuenta con saldo corrido (fórmulas); detallado con aplicaciones | `estado_cuenta_banco` / `estado_cuenta_banco_detallado` |
| `con_dm_tipodoc` / `con_empdoctos_correl` | Correlativo por tipo de la cuenta; general del sistema (tipo o serie, registros actualizados / anulados / contables…) con saltos en rojo | `correlativo_documentos` / `correlativo_general` |
| `con_dm_dispo` | Disponibilidad por cuenta a la fecha | `disponibilidad` |
| `con_chq_autorizado_xls` / `con_chq_entregados_xls` | Cheques autorizados / entregados o pendientes, con aplicaciones | `cheques_autorizados` / `cheques_entregados` |
| `con_chq_pendientes_numero_xls` | Registros sin número (no impresos) | `pendientes_numero` |
| `con_cc_estcta` / `con_cl_estcta` | Estado de cuenta de caja chica / caja liquidación | `estado_cuenta_caja_chica` / `estado_cuenta_caja_liquidacion` |
| `con_cl_detalle_cc` / `con_liquidacion_caja_chica` / `con_cc_resumen_ctas_doc_apl` | Cajas por liquidar (Crystal), liquidación y resumen por cuenta contable | `cajas_detalle` / `liquidacion_caja_chica` / `resumen_cuentas_caja` |
| `con_dm_conci` / `con_dm_conci_aecid` | Conciliación bancaria (resumen, detalle, pendientes de aclarar, no entregados) | `conciliacion` / `conciliacion_aecid` |
| `con_dm_posicion` | Posición bancaria de todas las cuentas del tipo, con ajustes y resumen de conciliación | `posicion_bancaria` |
| `con_flujo_de_fondos_xls` / `con_flujo_de_fondos_proyectado_xls` | Flujo a la fecha; proyectado por semana / mes / año con CXC, CXP y provisiones repartidas por fecha a trabajar | `flujo_de_fondos` / `flujo_proyectado` |
| `con_origen_aplicacion_fondos` | Saldo inicial mensual, entradas y salidas por clasificación y saldo final del año | `origen_aplicacion` |
| `con_correlativo_recibos` / `con_recibos_aplicados` | Recibos con los documentos que cancelan, saldo al descubierto y diferencial | `correlativo_recibos` / `movimiento_recibos` |
| `con_correlativo_recibos_depositos` / `con_depositos_aplicados_recibos` | Recibos con su depósito (y formas de pago); depósitos con sus recibos o «NO APLICADO» | `recibos_depositos` / `depositos_aplicados` |

Validación: saldo inicial y final del estado de cuenta iguales a la suma directa (BIQ1); diferencia de
conciliación 0 (sep-2026, jun-2024); posición: saldo banco + circulación = libros; origen y
aplicación: el saldo final de cada mes es el inicial del siguiente; proyectado: cada periodo arranca
con el saldo final del anterior; recibos: valor = aplicado + saldo al descubierto. Producción (003):
0.8–3 s por reporte; el proyectado 6 s (CXC y CXP pendientes de toda la empresa).

No migrados: `con_dm_reporte_pago_electronico` y `_revision` (archivos para Provenet / ACH BI, BAM,
G&T): ninguna cuenta tiene `usa_pe`, no hay documentos con `codtipo_pe` ni datos en
`grudirectorio_pe`, ni en desarrollo ni en producción. La cadena de liquidaciones de agencia de
`con_recibos_aplicados` (LIQ → facturas del proveedor → pagos) tampoco: no hay liquidaciones. Sin
fuente en VFP: `con_dm_conci_cuadratica_sat`, `con_dm_documentos`, `con_dm_pagos_efectuados`,
`con_cr_estcta`. Ninguna cuenta usa autorización ni entrega de cheques (`usachqautorizados` /
`usaentrega_docs` en 0): esos reportes responden «No existen datos».

### Activos fijos (menú 50 de ACFIJOS)

| comando | Reporte | Función (`activos.py`) |
|---|---|---|
| `con_calculo_dep_xdia_acfijos` | Cálculo de depreciaciones al día de corte (hoja DEPTOS) y póliza del mes (hoja POLIZA) | `depreciacion` |
| `con_acfactivos_tipos` | Activos vigentes por tipo (y responsable), con proveedor, factura y valores | `por_tipo` |
| `con_articulos_x_responsable` | Activos de cada responsable, con sus accesorios | `por_responsable` |
| `con_inventario_tarjeta_responsa` / `con_inventario_ficha_responsa_aecid` | Ficha de responsabilidad (por código o nombre) / formato AECID con cantidad y valor del detalle | `ficha_responsabilidad` / `ficha_responsabilidad_aecid` |
| `con_inventario_aecid` / `con_inventario_debaja_aecid` | Inventario de bienes muebles en buen estado / unidades dadas de baja (acf_activos_det) | `inventario_aecid` / `inventario_baja_aecid` |
| `con_articulos_no_depreciables` / `con_articulos_dados_de_baja` / `con_articulos_con_accesorios` | Listados por situación del activo | `no_depreciables` / `dados_de_baja` / `con_accesorios` |

`calcula` es el puerto línea por línea de `calculo_depreciacion.prg` (mes parcial inicial y final,
bajas, año comercial, valor residual y el ajuste contra el mes anterior). Validado: en los 93 activos
de Servirsa, mes a mes durante toda su vida útil, acumulado = acumulado anterior + depreciado del
mes, valor actual = valor − acumulado y el acumulado nunca pasa de valor − rescate; la póliza cuadra.
Falta compararlo contra una corrida de VFP.
«Depreciaciones a contabilidad» (`con_polizas_dep_acfijos_xdia`) graba pólizas: no se migra (la web es de
solo lectura). En los listados una fecha en blanco significa «sin límite», como en VFP. Validado (003 y KOW):
el valor total de los activos es el mismo en todos los listados y en la depreciación (35,080,499.43 en 003).
Sin datos en Servirsa para validar: tipos con 0 %, bajas (`acf_bajas`), accesorios (`acf_consolidado`) y
unidades dadas de baja en el detalle.

## Por qué ahora es rápido

1. **Una consulta por reporte** (`financieros/saldos.py`) en lugar de la vista
   `v_saldos_cuenta`. La vista agrupa todas las empresas y meses y calcula `yearmes`
   con `CONCAT(...)`, así que el filtro no usa índices. La consulta nueva filtra por
   `t.codempresa` y rango de `t.fecmov` y usa los índices `v_saldos_cuenta` que ya
   existen. Medido en la empresa 003, ene–oct 2026: vista 3.6 s, consulta nueva 0.06 s,
   mismos resultados. VFP llamaba la vista 3+ veces por reporte (≈ 3 por mes en los
   mensuales).
2. **Excel sin COM** (`financieros/excel.py`): XlsxWriter genera el archivo completo
   de una vez. VFP hacía miles de llamadas COM (insertar fila, seleccionar, escribir,
   negrita, agrupar) por cada cuenta.

Resultado: cada reporte se genera en menos de 1 segundo.

## Fidelidad con VFP

* El orden de filas, líneas en blanco y posición de los totales
  ("TOTAL: PASIVO + CAPITAL", "UTILIDAD BRUTA", "RESULTADO DEL PERIODO") se obtienen
  replicando el mismo algoritmo de inserción de VFP (`financieros/motor.py`).
* Cuentas mayores con `SUBTOTAL(9, ...)`, filas agrupadas por nivel y ocultas cuando
  el nivel supera el elegido, igual que antes. Las fórmulas llevan su valor ya calculado.
* La utilidad del ejercicio se inyecta en `empperiodos.gananciaejercicio` igual que VFP.

### Diferencias intencionales (correcciones)

* Leyenda "(Cifras Expresadas en ...)": VFP unía `commonedas.codmoneda = empmonedas.codempresa`
  (error) y salía vacía; ahora se toma de `empresas.codmoneda`.
* BG comparativo mes anterior: el % de las cuentas de detalle salía dividido entre
  100; ahora todas las filas usan la fórmula de las cuentas mayores. Se agregó la
  columna "VARIACION MONTO" del acumulado y su % es variación / acumulado anterior
  (antes dividía el movimiento del mes entre el acumulado anterior).
* ER comparativo mes anterior: el % era periodo / variación; ahora es variación / periodo comparativo.
* Comparativos: la nomenclatura es la unión de los dos ejercicios (como ya hacían los ER).
* Comparativos contra otro periodo: el periodo más reciente es siempre el actual, sin importar en qué
  casilla se capture; columnas anterior, actual y variación = actual − anterior, como en VFP.
* Cuentas de orden: excluye transacciones anuladas (VFP no filtraba `anulado`).
* Login: consultas con parámetros (VFP concatenaba texto → inyección SQL) y se
  excluyen usuarios con `es_inactivo = 1`.
* ER por centro, comparativo anual: VFP tenía fijo `codperiodo = '2020-01'` para
  nomenclatura y centros; ahora usa el periodo del corte.
* ER por subcentro: VFP ordenaba los títulos por cuenta de acumulación y los valores por
  código de centro (columnas desalineadas); ahora ambos usan el mismo orden.
* Presupuesto por C.C.: los movimientos excluyen transacciones anuladas (VFP no filtraba).
* Presupuesto por C.C. (detalle): el real de Inversión se calcula; en VFP siempre daba 0
  (comparaba contra `_detalle.cuenta` con el cursor en EOF). No se crean hojas para centros
  sin presupuesto ni movimientos.
* Libros (Crystal): el saldo corrido del DMG / estado de cuenta parte del inicio del ejercicio aunque se
  pida un rango de periodos (Crystal lo reiniciaba en cero en el periodo inicial). El Diario respeta la
  presentación elegida (VFP siempre terminaba usando LibroDiario y la llave de agrupación quedaba vacía).
* Mayor por directorio: el saldo por directorio sale de contabilidad y lo no asignable va en una fila de
  ajuste; VFP sumaba la apertura completa a cada directorio.
* CXC / CXP: VFP hacía de 1 a 3 consultas POR DOCUMENTO (pagos de cliente en
  `transacciones_aplica_det`, cuenta contable, comparación con contabilidad); aquí una por dato
  para todo el reporte. Integración con «datos contables»: VFP escribía la cuenta y luego la
  sobrescribía con la clasificación en la misma celda; ahora son dos columnas. Detalle por
  cuenta: el saldo anterior siempre salía en 0 (la consulta ya venía filtrada desde la fecha
  inicial); ahora se calcula. Moras: los pagos eran TODOS los anteriores a la fecha inicial (ya
  restados en el saldo inicial); ahora son los del periodo. Los tramos de antigüedad se
  rotulan 1 - 30, 31 - 60… (VFP: «0 - 31», «31 - 61»). Integración M.E.: solo documentos en
  moneda extranjera (VFP listaba también los de moneda local, en 0).
* Integración de cuentas: el saldo inicial de enero incluye la póliza de apertura (VFP tenía
  fijo el año 2010 y nunca la sumaba), excluye anuladas, y en "solo saldo inicial" el saldo va
  en su columna. TD: la utilidad del ejercicio es una fila propia (VFP sobrescribía una partida
  real de la cuenta de ganancia); la tabla dinámica nativa se reemplaza por la hoja "Analisis".
* IVA: no se escribe en la base. VFP, en cada corrida, hacía `UPDATE transacciones SET
  total_servicios = total_servicios + timbre_prensa` (TODAS las empresas; aquí el ajuste se aplica
  en la consulta), `UPDATE libro_comprasventas SET base_no_gravada = 0` (todas las empresas, SAT
  versión 1; aquí se omite) y `actualiza_regs_retenciones_exenciones_cab.prg` (sale sin hacer nada:
  las banderas `usa_act_aut_ret_exe_*` están en 0 en todas las empresas). Llave / registro de
  referencia: una consulta por cada 500 documentos (VFP, una por documento). Compras: las columnas
  de importación por documento se ponen en 0 si el documento está anulado (VFP las llenaba).
  Constancias y retenciones: los títulos NÚMERO / VALOR estaban invertidos. Ventas, resumen
  «EXPORTACIONES»: suma las columnas de exportación (la fórmula de VFP estaba corrida una columna:
  incluía servicios netos y omitía la última). Por documento de referencia: la importación se
  separa en D. Aduanera / FAUCA según el documento (VFP no copiaba `tipodocimp` y todo caía en FAUCA).
* Facturación: subtotal por tipo de documento siempre (VFP solo si se indicaba «tipo inicio»).
  Retenciones de IVA aplicadas: VFP buscaba `codtipodoctos = 'RET'` en lugar de RI/RIA en una de
  las dos mitades de la consulta; ahora se usan RI/RIA en ambas. Vendedor: el ISR 5 % se calcula
  sobre el valor neto (la fórmula de VFP apuntaba a la columna SUCURSAL). Mensual por cliente: el
  % es sobre el total de la moneda (VFP dividía entre la celda dos filas abajo) y los montos son
  números (VFP los escribía como texto). Por cuenta contable: una fila por factura con todas sus
  cuentas (VFP una fila por concepto). Cuadros finales: % por marca sobre el total del cliente.
* Caja y bancos: saldos de todas las cuentas en una consulta (VFP: 2 a 4 por cuenta) y
  aplicaciones / subdetalles por lotes (VFP: una consulta por documento). Correlativo por tipo: el
  rango de tipos estaba invertido. Autorizados: el usuario se filtraba por `codusrentrega`. Subdetalle
  de liquidaciones: VFP lo pedía con la llave del cheque en lugar de la de la liquidación. No
  entregados: comparaba `codsistema` con la cuenta (siempre vacío). Pendientes de aclarar: unía
  `chq_pendconci` sin condición (sumaba los de todas las cuentas). Cajas (Crystal): precedencia de
  AND / OR en el filtro. Liquidación de caja chica: `grudirectorio` sin `codgrupo` y la fecha inicial
  repetida en el encabezado. Flujo: el saldo excluía el último día (`fecmov < fecha`); caducados restaba
  aplicaciones en M.L. a valores en M.E.; la tasa se buscaba con monedas `'$.'` / `GTQ` que no existen
  (ahora `comtdc` con la moneda de la empresa). Recibos: «días crédito» = vencimiento − fecha del
  documento (VFP los contaba contra la fecha de hoy). Origen y aplicación: el saldo inicial solo
  incluía cuentas abiertas dentro del año, del saldo previo leía solo el primer año (`GROUP BY
  YEAR`), la tasa se truncaba a 2 decimales (`STR(n,4,8)`), los movimientos sin clasificación no
  aparecían (el saldo final no cuadraba con el inicial del mes siguiente) y la moneda no filtraba; por
  defecto se excluyen los documentos sin número, como en el estado de cuenta. Proyectado: provisiones
  agrupadas solo por cuenta (todo el periodo caía en la columna de una fecha cualquiera); por año las
  columnas no tenían fechas (nada se repartía); por mes, pasando diciembre se armaban fechas
  inválidas; solo se escribían montos positivos (notas de crédito y anticipos desaparecían); el rango
  de directorios solo se aplicaba a ingresos y lo vencido antes del rango solo se mostraba en ingresos.
  Correlativo general: el salto de numeración se compara dentro de la misma cuenta, tipo y serie
  (los depósitos y notas usan el número del banco y saltan por naturaleza).
* CXC / CXP Integración de saldos ordenada por nombre: VFP indexa razón social + fecha y luego agrupa por
  código de directorio consecutivo; si un cliente tiene varios contratos (directorios con la misma razón
  social) sus documentos se intercalan por fecha, cada contrato queda partido en bloques y el RESUMEN
  muestra solo el último bloque (los totales del grupo sí cuadran). Comparado contra VFP (003, al
  16/10/2026): mismos 184 directorios y 217 documentos, total 70,460,330.31 igual; las 11 líneas del
  resumen que difieren son exactamente los directorios que VFP parte. Estado de cuenta: mismo orden que
  VFP (fecha, serie, número; en empate, el documento antes que el pago).
* Balance de Saldos / Analítico: VFP excluía del movimiento del mes los documentos cuyo tipo empieza con
  «PA» o «AP» (PAG, PAGD…): `FOR codtipo <> 'AP' AND codtipo <> 'PA'` con `SET EXACT OFF` compara solo los
  primeros caracteres. Ejemplo real (003, sep-2026): el PAG de 548,096.33 de la cuenta 201010405 no aparecía
  y los totales daban 8,108,673.91 en lugar de 8,656,770.24 (lo que suma el Libro Diario).
* Balance General en columnas: VFP mostraba en 0 las cuentas de detalle con saldo negativo (la reserva de
  incobrables 1.02.03.01.09, −680,000) aunque el subtotal sí las restaba.
* CXP Proyección de pagos: VFP fija «solo deudores» y `_saldos_cuenta_corriente` lo invierte en CXP, así que
  proyectaba solo los saldos negativos (N/C, cheques) y no las facturas por pagar. La web proyecta las
  facturas pendientes (12,322,591.19 en 003 al 18/10/2026 = la integración sin los 119,884 negativos).
  Intervalo inicial de 7 días, como VFP.
* Posición bancaria: mismas cifras que VFP con la misma tasa; VFP siempre terminaba usando 8 (buscaba la
  moneda «$.», que no existe). Resumen de conciliación, pendientes de aclarar y de número marcados por defecto.
* Asiste Libros ventas versión 2: los importes enteros van sin decimales, como `mas()` de VFP; los archivos
  de compras y ventas de sep-2026 quedan idénticos byte por byte a los de VFP.
* CXC / CXP Tabla dinámica: los rangos de «días de crédito» se calculan al corte (VFP usaba `date()`, la
  fecha en que se genera, aunque se pida otro corte; la Integración de VFP sí usa el corte). Comparado
  contra VFP con el mismo corte (003, al 31/10/2026): mismos 215 documentos y saldo 50,647,643.18; solo
  cambian de rango los documentos cuyos días al 16/10 (día en que corrió VFP) y al 31/10 caen en rangos
  distintos.
* Activos fijos, depreciaciones: los filtros de responsable sí se aplican (en VFP estaban en el
  formulario pero no en la consulta); «Ocultar activos de baja» oculta todas las bajas hasta la fecha
  (VFP solo ocultaba las del mes y seguía mostrando las anteriores); la póliza agrupa el debe por la
  cuenta de gasto (VFP agrupaba por la cuenta de depreciación y mostraba una cuenta de gasto
  cualquiera del grupo) y compara fechas como fechas (VFP comparaba `DTOC()`, texto dd/mm/aaaa).
* Libros fiscales: la utilidad del ejercicio en el Libro de Inventario va como saldo anterior + resultado
  del mes en la cuenta de ganancia (VFP agregaba otra fila con la utilidad del año en cargos / abonos,
  sin saldo anterior: los TOTALES de cargos y abonos dejaban de cuadrar, y si la cuenta ya tenía
  movimientos quedaban dos registros para la misma fila). Libro de BG «del mes»: la utilidad es la
  del mes (VFP sumaba la del año a saldos del mes). «Incluye todas las cuentas» del inventario funciona
  (en VFP un segundo ciclo borraba igual las cuentas sin movimiento). Los libros de estados
  financieros y el balance EF ya no usan las vistas `v_saldos_cuenta` / `v_balance_saldos` (el EF
  además convertía los montos a texto con `CAST(cargo*100 AS CHAR)` y los volvía a leer con `VAL`).
