"""
Libros del menú 35 – Libros Auxiliares:

  con_dmg                       Diario Mayor General (Crystal Dmg_Nombre.rpt): por cuenta y mes,
                                saldo corrido línea a línea.
  con_estado_cta_contable       Estado de Cuenta Contable (Crystal): igual, con C.C./C.I. y sin anuladas.
  con_dmg_xls                   Diario Mayor General en Excel (plantilla DMG.xls): toda la nomenclatura,
                                saldo inicial, partidas y saldo final con subtotales por cuenta mayor.
  con_diario                    Libro Diario (Crystal LibroDiario / Diario_Tipo_Fecha): por transacción
                                o consolidado por fecha / tipo de documento.
  con_diario_especial_auditores, con_diario_pwc_2011, con_diario_pwc_2013
                                Diarios planos para auditores (Excel o archivo de texto).

Cada reporte hace una consulta de detalle y, si necesita saldo anterior, una agregada.
"""
import dataclasses
from collections import OrderedDict, defaultdict
from datetime import date

from core.db import consulta

from . import contexto as ctx
from .contexto import ErrorReporte, fin_de_mes
from .integracion import _Integracion, _texto
from .modelo import ArchivoTexto, Columna, Fila
from .reportes import Base

ANULADO = "***A N U L A D O ***"


def _fecha(valor, formato="%d/%m/%Y"):
    return valor.strftime(formato) if hasattr(valor, "strftime") else _texto(valor)


def _documento(f):
    numero = str(int(f["numero"] or 0)) if f["numero"] else ""
    return " ".join(x for x in (_texto(f["codtipo"]), _texto(f["serie"]), numero) if x)


def _filtro_cuentas(p, campo="d.codcuenta"):
    partes, params = [], []
    for valor, op in ((p.cuenta_desde, ">="), (p.cuenta_hasta, "<=")):
        if valor:
            partes.append(f"AND {campo} {op} %s")
            params.append(valor)
    return " ".join(partes), params


# ------------------------------------------------------------ Mayor / Estado de cuenta (Crystal)

SQL_MAYOR = """
SELECT d.codcuenta, d.cargo, d.abono, d.codcc, d.codci, d.descripcioncont,
       n.nomcta, n.naturalezacta,
       t.fecmov, t.codsistema, t.codtipo, t.serie, t.numero, t.nombre, t.descontable, t.anulado
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
  JOIN empnomenclatura n
    ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
 WHERE t.codempresa = %s
   AND t.actualizadocnt = 1
   AND t.fecmov BETWEEN %s AND %s
   {anulado} {cuentas}
 ORDER BY d.codcuenta, t.fecmov, t.codtipo, t.serie, t.numero, d.iddettransacciones
"""

SQL_PREVIO = """
SELECT d.codcuenta, MAX(n.nomcta) AS nomcta, MAX(n.naturalezacta) AS naturalezacta,
       SUM(d.cargo) AS cargo, SUM(d.abono) AS abono
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
  JOIN empnomenclatura n
    ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
 WHERE t.codempresa = %s
   AND t.actualizadocnt = 1
   AND t.fecmov >= %s AND t.fecmov < %s
   {anulado} {cuentas}
 GROUP BY d.codcuenta
"""


class _Mayor(Base):
    """Rango de los reportes Crystal: «Meses a reportar» dentro del ejercicio o «Rango de periodos»."""

    def __init__(self, p):
        super().__init__(p)
        c = self.corte
        self.fecha_fin = c.fecha
        if p.anio_ini and p.mes_ini:     # «Mostrar rango de periodos»
            self.fecha_ini = date(p.anio_ini, p.mes_ini, 1)
        else:   # Crystal: mes_ini = MAX(0, mes - meses) + 1 del mismo año, sin salir del ejercicio
            ini = date(p.anio, max(0, p.mes - max(1, p.meses or 1)) + 1, 1)
            self.fecha_ini = max(ini, c.periodo.fecini)
        if self.fecha_ini > self.fecha_fin:
            raise ErrorReporte("La fecha inicial es posterior a la fecha final.")
        # El saldo se acumula desde el inicio del ejercicio del primer mes mostrado
        self.inicio_saldo = ctx.periodo(p.codempresa, self.fecha_ini.year, self.fecha_ini.month).fecini

    def subtitulo(self):
        a, b = self.fecha_ini, self.fecha_fin
        if (a.year, a.month) == (b.year, b.month):
            return f"PERIODO: {b.year}-{b.month:02d}"
        return f"PERIODO: {a.year}-{a.month:02d} A {b.year}-{b.month:02d}"


def _por_dia(movs):
    """DMG_Nombre_Resumido.rpt: los movimientos de cada día en una línea (la apertura queda aparte)."""
    salida, dias = [], OrderedDict()
    for f in movs:
        if _texto(f["codsistema"]) == "CNT" and _texto(f["codtipo"]) in ("AP", "PA"):
            salida.append(f)
            continue
        d = dias.get(f["fecmov"])
        if d is None:
            d = dias[f["fecmov"]] = dict(f, cargo=0.0, abono=0.0, codtipo="", serie="", numero=0, anulado=0,
                                         nombre="Movimientos del día", descripcioncont="", descontable="",
                                         codcc="", codci="", codsistema="")
            salida.append(d)
        d["cargo"] += float(f["cargo"] or 0)
        d["abono"] += float(f["abono"] or 0)
    return salida


def _mayor(p, estado_cuenta, por_dia=False):
    m = _Mayor(p)
    cuentas_sql, cuentas_params = _filtro_cuentas(p)
    anulado = "AND t.anulado = 0" if estado_cuenta else ""
    previos = {f["codcuenta"]: f for f in consulta(
        SQL_PREVIO.format(anulado=anulado, cuentas=cuentas_sql),
        (m.emp.codempresa, m.inicio_saldo, m.fecha_ini, *cuentas_params))}
    lineas = OrderedDict()
    for f in consulta(SQL_MAYOR.format(anulado=anulado, cuentas=cuentas_sql),
                      (m.emp.codempresa, m.fecha_ini, m.fecha_fin, *cuentas_params)):
        lineas.setdefault(f["codcuenta"], []).append(f)
    cuentas = sorted(set(previos) | set(lineas))
    if p.libro_fiscal and not lineas:      # VFP: sin movimientos en el mes -> hoja «sin movimientos»
        return _sin_movimientos(m, "LIBRO MAYOR", m.subtitulo(),
                                [Columna("DESCRIPCIÓN", ancho=55, texto=True), Columna("S. INICIAL"),
                                 Columna("CARGO"), Columna("ABONO"), Columna("SALDO")], ("FECHA", 11), ("No. DOC.", 22))
    if not cuentas:
        raise ErrorReporte("No existen datos para los parámetros indicados.")

    columnas = ([Columna("CC", ancho=8, texto=True), Columna("CI", ancho=8, texto=True)] if estado_cuenta else [])
    columnas += [Columna("DESCRIPCIÓN", ancho=55, texto=True), Columna("S. INICIAL"), Columna("CARGO"),
                 Columna("ABONO"), Columna("SALDO")]
    k = len(columnas) - 5            # índice de DESCRIPCIÓN
    vacio = [None] * len(columnas)

    filas, gran_cargo, gran_abono = [], 0.0, 0.0
    for cuenta in cuentas:
        movs, prev = lineas.get(cuenta, []), previos.get(cuenta)
        if por_dia:
            movs = _por_dia(movs)
        base = movs[0] if movs else prev
        deudora = (base["naturalezacta"] or "").strip() == "D"
        saldo = 0.0
        if filas:
            filas.append(Fila())
        filas.append(Fila(tipo="grupo", cuenta=m.nom.formatea(cuenta), nombre=_texto(base["nomcta"])))
        if prev:
            c, a = float(prev["cargo"] or 0), float(prev["abono"] or 0)
            saldo = round(c - a if deudora else a - c, 2)
            v = list(vacio)
            v[k], v[k + 1], v[k + 4] = "SALDO ANTERIOR", saldo, saldo
            filas.append(Fila(tipo="cuenta", nivel=2, valores=v))
        cargos = abonos = 0.0
        mes_actual = None
        for f in movs:
            if not estado_cuenta and (f["fecmov"].year, f["fecmov"].month) != mes_actual:
                mes_actual = (f["fecmov"].year, f["fecmov"].month)
                filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=f"{mes_actual[0]}-{mes_actual[1]:02d}",
                                  valores=list(vacio)))
            c, a = round(float(f["cargo"] or 0), 2), round(float(f["abono"] or 0), 2)
            mov = c - a if deudora else a - c
            apertura = (not estado_cuenta and _texto(f["codsistema"]) == "CNT"
                        and _texto(f["codtipo"]) in ("AP", "PA"))
            if apertura:     # Crystal: la apertura es saldo inicial, no cargo/abono
                saldo = round(saldo + mov, 2)
                s_ini, c, a = saldo, 0.0, 0.0
            else:
                s_ini = saldo
                saldo = round(saldo + mov, 2)
            descripcion = ANULADO if f["anulado"] else _texto(f["nombre"])
            if p.incluye_concepto:
                concepto = _texto(f["descripcioncont"]) or _texto(f["descontable"])[:60]
                if concepto and concepto != descripcion:
                    descripcion = f"{descripcion} — {concepto}" if descripcion else concepto
            v = list(vacio)
            if estado_cuenta:
                v[0], v[1] = _texto(f["codcc"]), _texto(f["codci"])
            v[k:] = [descripcion, s_ini, c, a, saldo]
            filas.append(Fila(tipo="cuenta", nivel=2, cuenta=_fecha(f["fecmov"]), nombre=_documento(f), valores=v))
            cargos, abonos = cargos + c, abonos + a
        v = list(vacio)
        v[k + 2], v[k + 3], v[k + 4] = round(cargos, 2), round(abonos, 2), saldo
        filas.append(Fila(tipo="total", nombre=f"TOTAL {m.nom.formatea(cuenta)}", negrita=True, valores=v))
        gran_cargo, gran_abono = gran_cargo + cargos, gran_abono + abonos

    v = list(vacio)
    v[k + 2], v[k + 3] = round(gran_cargo, 2), round(gran_abono, 2)
    filas += [Fila(), Fila(tipo="total", nombre="GRAN TOTAL", negrita=True, valores=v)]

    titulo = "ESTADO DE CUENTA CONTABLE" if estado_cuenta else ("LIBRO MAYOR" if p.libro_fiscal else "DIARIO MAYOR GENERAL")
    rep = m.reporte(titulo, m.subtitulo(), columnas, filas, usar_formulas=False)
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("No. DOC.", 22)
    rep.folio = p.folio or 0
    if not estado_cuenta:
        rep.fecha_hora = None
    return rep


def _sin_movimientos(base, titulo, subtitulo, columnas, enc_a, enc_b):
    """
    Libros fiscales de un mes sin movimientos («Diario / DMG_Nombre Sin Movimientos.rpt»): la hoja
    se imprime igual, con la leyenda y totales en cero, para que la numeración de folios no se corte.
    """
    ceros = [0.0 if not c.texto else None for c in columnas]
    filas = [Fila(tipo="grupo", nombre="Período sin movimientos..."), Fila(),
             Fila(tipo="total", negrita=True, nombre="GRAN TOTAL", valores=ceros)]
    rep = base.reporte(titulo, subtitulo, columnas, filas, usar_formulas=False)
    rep.encabezado_a, rep.encabezado_b = enc_a, enc_b
    rep.folio = base.p.folio or 0
    rep.fecha_hora = None
    return rep


def diario_mayor_general(p):
    """con_dmg (Crystal Dmg_Nombre.rpt)."""
    return _mayor(p, estado_cuenta=False)


def diario_mayor_resumido(p):
    """con_dmg_resumido (DMG_Nombre_Resumido.rpt): el Diario Mayor General con un renglón por día."""
    return _mayor(p, estado_cuenta=False, por_dia=True)


def libro_mayor(p):
    """con_libro_mayor (menú 30, Libros Fiscales): Dmg_Nombre.rpt de un mes, todas las cuentas."""
    p.libro_fiscal = True
    p.meses, p.anio_ini, p.mes_ini = 1, None, None     # siempre el mes indicado
    return _mayor(p, estado_cuenta=False)


def estado_cuenta_contable(p):
    """con_estado_cta_contable (Crystal Estado de Cuenta Contable.rpt)."""
    return _mayor(p, estado_cuenta=True)


# ------------------------------------------------------------ Por centro de costo (Crystal, menú 22)

SQL_MAYOR_CC = """
SELECT d.codcuenta, {centro} AS centro, d.codcc, d.codci, d.cargo, d.abono, d.descripcioncont, n.nomcta, n.naturalezacta,
       t.fecmov, t.codtipo, t.serie, t.numero, t.nombre, t.descontable
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
  JOIN empnomenclatura n
    ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
 WHERE t.codempresa = %s AND t.actualizadocnt = 1 AND t.anulado = 0
   AND t.fecmov {rango}
   {filtros}
 ORDER BY {orden}, t.fecmov, t.numero, d.iddettransacciones
"""


def _filtros_cc(p, solo_con_cc):
    partes, params = _filtro_cuentas(p)
    partes = [partes] if partes else []
    col = "d." + ctx.columna_centro(p.dim)
    for valor, op in ((p.cc_desde, ">="), (p.cc_hasta, "<=")):
        if valor:
            partes.append(f"AND {col} {op} %s")
            params.append(valor)
    if solo_con_cc:
        partes.append(f"AND {col} <> ''")
    return " ".join(partes), params


def _mayor_cc(p, por_cuenta):
    """
    por_cuenta=False  con_mov_centro_costo (Movimiento por Centro de Costo.rpt): por centro, saldo cargo - abono.
    por_cuenta=True   con_dmg_cc (DMG_CC.rpt): por cuenta y centro, saldo según la naturaleza de la cuenta.
    El saldo de cada grupo se acumula desde el inicio del ejercicio, como en Crystal (PerIni).
    """
    m = _Mayor(p)
    # Movimiento por centro: Crystal filtra «centro <> ''» por C.C. y C.A.; por C.I. no filtra
    filtros, params = _filtros_cc(p, solo_con_cc=not por_cuenta and p.dim != "ci")
    col = "d." + ctx.columna_centro(p.dim)
    orden = f"d.codcuenta, {col}" if por_cuenta else f"{col}, d.codcuenta"
    lineas = consulta(SQL_MAYOR_CC.format(centro=col, rango="BETWEEN %s AND %s", filtros=filtros, orden=orden),
                      (m.emp.codempresa, m.fecha_ini, m.fecha_fin, *params))
    previas = consulta(SQL_MAYOR_CC.format(centro=col, rango=">= %s AND t.fecmov < %s", filtros=filtros, orden=orden),
                       (m.emp.codempresa, m.inicio_saldo, m.fecha_ini, *params))
    if not lineas and not previas:
        raise ErrorReporte("No existen datos para los parámetros indicados.")
    centros = ctx.centros(m.emp.codempresa, m.corte.periodo.codperiodo, p.dim)
    # Movimiento por centro: la columna de detalle es el C.I. (plantillas de C.C. y C.A.); la de C.I. no la tiene
    otro, titulo_otro = "codci", "C. INTEGRACIÓN"

    def clave(f):
        return (f["codcuenta"], _texto(f["centro"])) if por_cuenta else (_texto(f["centro"]),)

    def neto(f):
        c, a = float(f["cargo"] or 0), float(f["abono"] or 0)
        if por_cuenta and _texto(f["naturalezacta"]) != "D":
            return a - c
        return c - a

    saldo_previo, nombres = defaultdict(float), {}
    for f in previas:
        saldo_previo[clave(f)] += neto(f)
        nombres[f["codcuenta"]] = _texto(f["nomcta"])
    por_grupo = defaultdict(list)
    for f in lineas:
        por_grupo[clave(f)].append(f)
        nombres[f["codcuenta"]] = _texto(f["nomcta"])

    if por_cuenta:
        columnas = [Columna("DESCRIPCIÓN", ancho=55, texto=True), Columna("S. INICIAL"), Columna("CARGOS"),
                    Columna("ABONOS"), Columna("SALDO")]
    else:
        columnas = [Columna(titulo_otro, ancho=10, texto=True), Columna("No. DOCUMENTO", ancho=18, texto=True),
                    Columna("FECHA", ancho=11, texto=True), Columna("DESCRIPCIÓN", ancho=50, texto=True),
                    Columna("S. INICIAL"), Columna("DEBE"), Columna("HABER"), Columna("SALDO ACTUAL")]
    k = len(columnas) - 5          # índice de DESCRIPCIÓN
    vacio = [None] * len(columnas)

    def nombre_cc(cc):
        centro = centros.get(cc)
        return centro.nombre if centro else ""

    filas, totales = [], [0.0, 0.0]
    cuenta_actual, tot_cuenta = None, None

    def cierra_cuenta():
        if por_cuenta and cuenta_actual is not None:
            v = list(vacio)
            v[k + 2], v[k + 3] = round(tot_cuenta[0], 2), round(tot_cuenta[1], 2)
            filas.append(Fila(tipo="total", nombre=f"TOTAL {m.nom.formatea(cuenta_actual)}", negrita=True, valores=v))

    for grupo in sorted(set(saldo_previo) | set(por_grupo)):
        if por_cuenta and grupo[0] != cuenta_actual:
            cierra_cuenta()
            if filas:
                filas.append(Fila())
            cuenta_actual, tot_cuenta = grupo[0], [0.0, 0.0]
            filas.append(Fila(tipo="grupo", cuenta=m.nom.formatea(grupo[0]), nombre=nombres.get(grupo[0], "")))
        cc = grupo[-1]
        if por_cuenta:
            filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=cc or "(SIN C.C.)", nombre=nombre_cc(cc),
                              valores=list(vacio)))
        else:
            if filas:
                filas.append(Fila())
            filas.append(Fila(tipo="grupo", cuenta=cc, nombre=nombre_cc(cc)))
        saldo = round(saldo_previo.get(grupo, 0.0), 2)
        if grupo in saldo_previo:
            v = list(vacio)
            v[k], v[k + 1], v[k + 4] = "SALDO ANTERIOR", saldo, saldo
            filas.append(Fila(tipo="cuenta", nivel=2, valores=v))
        cargos = abonos = 0.0
        for f in por_grupo.get(grupo, []):
            c, a = round(float(f["cargo"] or 0), 2), round(float(f["abono"] or 0), 2)
            s_ini, saldo = saldo, round(saldo + neto(f), 2)
            cargos, abonos = cargos + c, abonos + a
            if por_cuenta and not p.incluye_concepto:
                continue        # «Detalle» desmarcado: solo saldos y totales por centro
            v = list(vacio)
            if por_cuenta:
                descripcion = _texto(f["nombre"])
                concepto = _texto(f["descripcioncont"])
                if concepto and concepto != descripcion:
                    descripcion = f"{descripcion} — {concepto}" if descripcion else concepto
                v[k:] = [descripcion, s_ini, c, a, saldo]
                filas.append(Fila(tipo="cuenta", nivel=2, cuenta=_fecha(f["fecmov"]), nombre=_documento(f), valores=v))
            else:
                numero = str(int(f["numero"] or 0))
                v[:] = [_texto(f[otro]), f"{_texto(f['serie'])} - {numero}" if _texto(f["serie"]) else numero,
                        _fecha(f["fecmov"]), _texto(f["descontable"]), s_ini, c, a, saldo]
                filas.append(Fila(tipo="cuenta", nivel=2, cuenta=m.nom.formatea(f["codcuenta"]),
                                  nombre=nombres.get(f["codcuenta"], ""), valores=v))
        v = list(vacio)
        v[k + 2], v[k + 3], v[k + 4] = round(cargos, 2), round(abonos, 2), saldo
        filas.append(Fila(tipo="total", nombre=f"TOTAL C.C. {cc}", negrita=True, valores=v))
        totales = [totales[0] + cargos, totales[1] + abonos]
        if por_cuenta:
            tot_cuenta = [tot_cuenta[0] + cargos, tot_cuenta[1] + abonos]
    cierra_cuenta()

    v = list(vacio)
    v[k + 2], v[k + 3] = round(totales[0], 2), round(totales[1], 2)
    filas += [Fila(), Fila(tipo="total", nombre="GRAN TOTAL", negrita=True, valores=v)]
    titulo = "DETALLE POR CENTRO DE COSTO" if por_cuenta else "MOVIMIENTO POR CENTRO DE COSTO"
    rep = m.reporte(titulo, m.subtitulo(), columnas, filas, usar_formulas=False)
    rep.encabezado_a, rep.encabezado_b = (("FECHA", 11), ("No. DOC.", 22)) if por_cuenta else \
        (("C. COSTO / CUENTA", 17), ("NOMBRE", 40))
    rep.folio = p.folio or 0
    if not por_cuenta and p.dim == "ci":
        rep.columnas_ocultas = {0}
    return rep


def movimiento_por_centro(p):
    """con_mov_centro_costo."""
    return _mayor_cc(p, por_cuenta=False)


def mayor_por_centro(p):
    """con_dmg_cc."""
    return _mayor_cc(p, por_cuenta=True)


# ------------------------------------------------------------ DMG en Excel (plantilla DMG.xls)

def diario_mayor_general_xls(p):
    """
    con_dmg_xls: recorre la nomenclatura; cada cuenta de detalle lleva saldo inicial, sus partidas
    del rango (con la póliza de apertura, como VFP) y saldo final; las mayores, SUBTOTAL.
    VFP ejecutaba 2 a 4 consultas por cuenta (_calcular_saldos_cuenta); aquí son dos en total.
    """
    p = dataclasses.replace(p, incluye_apertura=True, orden=1, directorio_desde="", directorio_hasta="")
    r = _Integracion(p)
    por_cuenta = defaultdict(list)
    for f in r.detalle():
        por_cuenta[f["codcuenta"]].append(f)
    saldos = r.saldos_iniciales()

    def en_rango(codigo):
        return (not p.cuenta_desde or codigo >= p.cuenta_desde) and (not p.cuenta_hasta or codigo <= p.cuenta_hasta)

    nom = ctx.nomenclatura(r.emp.codempresa, r.periodo_ini.codperiodo)
    hojas = {c.codigo for c in nom if c.ultimo and en_rango(c.codigo)
             and (p.incluir_todas or c.codigo in por_cuenta or saldos.get(c.codigo))}
    if not hojas:
        raise ErrorReporte("No se encontraron registros con los parámetros ingresados.")
    con_hijos = {h[:i] for h in hojas for i in range(1, len(h) + 1)}
    lineas = [c for c in nom if c.codigo in con_hijos and (not c.ultimo or c.codigo in hojas)]

    columnas = [Columna("TIPO DOC.", ancho=10, texto=True), Columna("NÚMERO", ancho=12, texto=True),
                Columna("CONCEPTO", ancho=50, texto=True), Columna("SALDO INICIAL"), Columna("CARGO"),
                Columna("ABONO"), Columna("SALDO FINAL")]
    filas, padres, hojas_filas = [], [], []
    for cta in lineas:
        if cta.nivel == 1 and filas:
            filas += [Fila(), Fila()]
        fila = Fila(tipo="cuenta", codigo=cta.codigo, cuenta=r.nom.formatea(cta.codigo),
                    nombre=" " * len(cta.codigo) + cta.nombre, nivel=cta.nivel, negrita=True)
        filas.append(fila)
        if not cta.ultimo:
            padres.append(fila)
            continue
        movs = por_cuenta.get(cta.codigo, [])
        # sin partidas la naturaleza solo afecta la fórmula (saldo final = saldo inicial)
        deudora = _texto(movs[0]["naturalezacta"]) == "D" if movs else True
        bloque = []
        for f in movs:
            concepto = _texto(f["descripcioncont"]) or _texto(f["descontable"])[:60]
            bloque.append(Fila(
                tipo="cuenta", nivel=cta.nivel + 1, codigo=cta.codigo, cuenta=_fecha(f["fecmov"]),
                nombre=" " * len(cta.codigo) + (_texto(f["nombre_pol"]) or _texto(f["nombre"])),
                valores=[_texto(f["codtipo"]), str(int(f["numero"] or 0)), concepto, None,
                         float(f["cargo"]) if f["cargo"] else None, float(f["abono"]) if f["abono"] else None, None]))
        ini = saldos.get(cta.codigo, 0.0)
        cargos = round(sum(b.valores[4] or 0 for b in bloque), 2)
        abonos = round(sum(b.valores[5] or 0 for b in bloque), 2)
        fin = round(ini + cargos - abonos if deudora else ini - cargos + abonos, 2)
        fila.valores = [None, None, None, ini, cargos, abonos, fin]
        fila.formulas[6] = ("lineal", [(1, 3), (1, 4), (-1, 5)] if deudora else [(1, 3), (-1, 4), (1, 5)])
        if bloque:
            fila.formulas[4] = ("subtotal", bloque[0], bloque[-1])
            fila.formulas[5] = ("subtotal", bloque[0], bloque[-1])
            filas += bloque + [Fila()]
        hojas_filas.append(fila)

    # Cuentas mayores: suma de sus cuentas de detalle (SUBTOTAL en Excel)
    for padre in padres:
        hijos = [h for h in hojas_filas if h.codigo.startswith(padre.codigo)]
        padre.valores = [None, None, None] + [round(sum(h.valores[k] for h in hijos), 2) for k in (3, 4, 5, 6)]
        ultimo = filas.index(hijos[-1])
        while ultimo + 1 < len(filas) and filas[ultimo + 1].tipo == "cuenta" and filas[ultimo + 1].nivel > hijos[-1].nivel:
            ultimo += 1
        padre.hasta = filas[ultimo]
    total = Fila(tipo="total", nombre="TOTALES", negrita=True, rango=(filas[0], filas[-1]),
                 valores=[None, None, None, None, round(sum(h.valores[4] for h in hojas_filas), 2),
                          round(sum(h.valores[5] for h in hojas_filas), 2), None])
    filas += [Fila(), total]

    rep = r.reporte("DIARIO MAYOR GENERAL", r.subtitulo(), columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("CUENTA / FECHA", 15), ("NOMBRE", 50)
    rep.folio = p.folio or 0
    rep.fecha_hora = None
    return rep


# ------------------------------------------------------------ Libro Diario

SQL_DIARIO = """
SELECT d.codcuenta, d.cargo, d.abono, d.codca, d.codcc, d.codci,
       d.coddirectorio AS coddir_lin, d.codclasifica AS clasif_lin, d.keyref AS keyref_lin,
       IFNULL(g.razonsocial, '') AS razon_lin, n.nomcta,
       t.keytra, t.fecmov, t.codsistema, t.codtipodoctos, t.codtipo, t.serie, t.numero,
       t.nombre, t.docref, t.descontable
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
  JOIN empnomenclatura n
    ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
  LEFT JOIN grudirectorio g
    ON g.codgrupo = d.codgrupo AND g.coddirectorio = d.coddirectorio AND d.coddirectorio <> ''
 WHERE t.codempresa = %s
   AND t.anulado = 0
   AND t.actualizadocnt = %s
   AND t.fecmov BETWEEN %s AND %s
   {usacnt}
 ORDER BY t.fecmov, t.numero, t.keytra, d.iddettransacciones
"""

PRESENTACIONES = {
    1: "Consolida fecha + tipo de documento",
    2: "Consolida por fecha",
    3: "Consolida tipo de documento sin C.C.",
    4: "Detalle por transacción",
    5: "Consolida tipo de documento",
}


def diario(p):
    """con_diario: Libro Diario del mes, en cinco presentaciones como el formulario VFP."""
    b = Base(p)
    ini, fin = date(p.anio, p.mes, 1), fin_de_mes(p.anio, p.mes)
    presentacion = p.presentacion if p.presentacion in PRESENTACIONES else 4
    filas_bd = consulta(SQL_DIARIO.format(usacnt="AND t.usacnt = 1" if presentacion == 4 else ""),
                        (b.emp.codempresa, 1 if p.actualizados else 0, ini, fin))
    columnas = [Columna("CA", ancho=8, texto=True), Columna("CC", ancho=8, texto=True),
                Columna("CI", ancho=8, texto=True), Columna("CARGO"), Columna("ABONO")]
    if not filas_bd and p.libro_fiscal:
        return _sin_movimientos(b, "LIBRO DE DIARIO", f"PERIODO: {p.anio}-{p.mes:02d}", columnas,
                                ("CUENTA", 17), ("DESCRIPCIÓN", 55))
    if not filas_bd:
        raise ErrorReporte("No existen datos para los parámetros indicados.")
    # Libro fiscal: «consolida tipo de documento» con centros (Diario_Tipo_Fecha.rpt); el diario
    # auxiliar usa la plantilla sin centros
    filas = (_diario_transacciones(b, filas_bd) if presentacion == 4
             else _diario_consolidado(b, filas_bd, presentacion, con_centros=presentacion != 3 or p.libro_fiscal))
    primera = next(f for f in filas if f.tipo == "cuenta")
    filas += [Fila(), Fila(tipo="total", nombre="GRAN TOTAL", negrita=True, rango=(primera, filas[-1]),
                           valores=[None, None, None,
                                    round(sum(float(f["cargo"] or 0) for f in filas_bd), 2),
                                    round(sum(float(f["abono"] or 0) for f in filas_bd), 2)])]
    rep = b.reporte("LIBRO DE DIARIO" if p.libro_fiscal else "LIBRO DIARIO", f"PERIODO: {p.anio}-{p.mes:02d} · {PRESENTACIONES[presentacion]}"
                    + ("" if p.actualizados else " · MOVIMIENTOS NO ACTUALIZADOS"), columnas, filas)
    rep.encabezado_b = ("DESCRIPCIÓN", 55)
    rep.folio = p.folio or 0
    rep.fecha_hora = None
    return rep


def libro_diario(p):
    """con_libro_diario (menú 30, Libros Fiscales): el Libro Diario con hoja «sin movimientos»."""
    p.libro_fiscal = True
    return diario(p)


def _total_bloque(bloque, texto):
    return Fila(tipo="total", nombre=texto, negrita=True, rango=(bloque[0], bloque[-1]),
                valores=[None, None, None, round(sum(f.valores[3] or 0 for f in bloque), 2),
                         round(sum(f.valores[4] or 0 for f in bloque), 2)])


def _diario_transacciones(b, filas_bd):
    """LibroDiario.rpt: encabezado de cada póliza y sus líneas."""
    filas = []
    por_poliza = OrderedDict()
    for f in filas_bd:
        por_poliza.setdefault(f["keytra"], []).append(f)
    for keytra, lineas in por_poliza.items():
        t = lineas[0]
        nombre = " · ".join(x for x in (_documento(t), _texto(t["nombre"])) if x)
        filas.append(Fila(tipo="grupo", cuenta=_fecha(t["fecmov"]), nombre=f"Póliza {_texto(keytra)} · {nombre}"))
        referencia = " — ".join(x for x in (f"Doc. referencia: {_texto(t['docref'])}" if _texto(t["docref"]) else "",
                                            _texto(t["descontable"])) if x)
        if referencia:
            filas.append(Fila(tipo="cuenta", nivel=1, nombre=referencia, valores=[None] * 5))
        bloque = []
        for f in lineas:
            fila = Fila(tipo="cuenta", nivel=2, cuenta=b.nom.formatea(f["codcuenta"]), nombre=_texto(f["nomcta"]),
                        valores=[_texto(f["codca"]), _texto(f["codcc"]), _texto(f["codci"]),
                                 float(f["cargo"]) if f["cargo"] else None,
                                 float(f["abono"]) if f["abono"] else None])
            bloque.append(fila)
            filas.append(fila)
            if _texto(f["coddir_lin"]):
                filas.append(Fila(tipo="cuenta", nivel=3, valores=[None] * 5, nombre=" ".join(
                    x for x in (_texto(f["clasif_lin"]), _texto(f["coddir_lin"]), _texto(f["razon_lin"]),
                                _texto(f["keyref_lin"])) if x)))
        filas += [_total_bloque(bloque, "Total póliza"), Fila()]
    return filas[:-1]


def _diario_consolidado(b, filas_bd, presentacion, con_centros=True):
    """Diario_Tipo_Fecha.rpt: suma por llave (fecha y/o tipo de documento) y cuenta + CA/CC/CI."""
    tipos = {(_texto(f["codsistema"]), _texto(f["codtipodoctos"]), _texto(f["codtipo"])): _texto(f["nomtipodoc"])
             for f in consulta("SELECT codsistema, codtipodoctos, codtipo, MAX(nomtipodoc) AS nomtipodoc "
                               "FROM empdoctos WHERE codempresa = %s GROUP BY codsistema, codtipodoctos, codtipo",
                               (b.emp.codempresa,))}
    grupos = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    nombres = {}
    for f in filas_bd:
        tipo = (_texto(f["codsistema"]), _texto(f["codtipodoctos"]), _texto(f["codtipo"]))
        llave = {1: (f["fecmov"], tipo), 2: (f["fecmov"], None)}.get(presentacion, (None, tipo))
        linea = (f["codcuenta"], _texto(f["codca"]), _texto(f["codcc"]), _texto(f["codci"])) if con_centros \
            else (f["codcuenta"], "", "", "")
        suma = grupos[llave][linea]
        suma[0] += float(f["cargo"] or 0)
        suma[1] += float(f["abono"] or 0)
        nombres[f["codcuenta"]] = _texto(f["nomcta"])

    filas = []
    for (fecha, tipo) in sorted(grupos, key=lambda k: (k[0] or date.min, k[1] or ())):
        etiqueta = []
        if fecha:
            etiqueta.append(_fecha(fecha, "%d/%m/%y"))
        if tipo:
            etiqueta.append("[" + "-".join(tipo) + "]")
        nomtipo = tipos.get(tipo, "").title() if tipo else ""
        if presentacion == 2:
            descripcion = "Movimiento del día."
        else:
            descripcion = f"Movimiento de {nomtipo}" + (" del día." if presentacion == 1 else ".")
        filas.append(Fila(tipo="grupo", cuenta="   ".join(etiqueta), nombre=descripcion))
        bloque = []
        for (cuenta, ca, cc, ci), (cargo, abono) in sorted(grupos[(fecha, tipo)].items()):
            bloque.append(Fila(tipo="cuenta", nivel=2, cuenta=b.nom.formatea(cuenta), nombre=nombres[cuenta],
                               valores=[ca, cc, ci, round(cargo, 2) or None, round(abono, 2) or None]))
        filas += bloque + [_total_bloque(bloque, "Total"), Fila()]
    return filas[:-1]


# ------------------------------------------------------------ Diarios para auditores

SQL_AUDITOR = """
SELECT d.keytra, d.codempresa, d.espropio, d.descripcioncont, d.codcuenta, d.codcc, d.codci,
       d.fechatransac, d.codperiodo, d.deha, d.cargo, d.abono, d.codusr, d.fecusr,
       t.fecmov, t.yearmes, t.codtipo, t.serie, t.numero, t.codmoneda,
       IFNULL(e.nombreemp, '') AS nombreemp, IFNULL(e.nitemp, '') AS nitemp,
       IFNULL(n.nomcta, '') AS nomcta, IFNULL(n.codctainternacional, '') AS codctainternacional,
       IFNULL(n.nombrectainternacional, '') AS nombrectainternacional
  FROM dettransacciones d
  JOIN transacciones t ON t.keytra = d.keytra
  LEFT JOIN empresas e ON e.codempresa = t.codempresa
  LEFT JOIN empnomenclatura n
    ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
 WHERE d.codempresa = %s
   AND t.actualizadocnt = 1
   AND t.anulado = 0
   AND d.fechatransac BETWEEN %s AND %s
   {cuentas}
 ORDER BY d.codcuenta, t.yearmes, d.iddettransacciones
"""


def _rango_meses(p):
    meses = max(1, min(p.meses or 1, 12))
    a, m = p.anio, p.mes - (meses - 1)
    while m < 1:
        a, m = a - 1, m + 12
    return date(a, m, 1), fin_de_mes(p.anio, p.mes)


def _detalle_auditor(p, ini, fin):
    b = Base(p)
    cuentas_sql, params = _filtro_cuentas(p)
    filas = consulta(SQL_AUDITOR.format(cuentas=cuentas_sql), (b.emp.codempresa, ini, fin, *params))
    if not filas:
        raise ErrorReporte("No existen datos para los parámetros indicados.")
    return b, filas


def _monto(f):
    return float(f["cargo"] or 0) if _texto(f["deha"]) == "D" else float(f["abono"] or 0)


def _archivo_texto(b, filas, mes):
    """Formato de genera_txt de VFP (idéntico en los tres formularios): campos separados por «|»."""
    nit = _texto(filas[0]["nitemp"]).replace("-", "")
    lineas = [f"{nit}|1|{mes:02d}|{b.p.anio:04d}"]
    for f in filas:
        ym = _texto(f["yearmes"])
        documento = _texto(f["serie"]) + str(int(f["numero"] or 0))
        concepto = _texto(f["descripcioncont"])[:128]
        campos = [_texto(f["keytra"]), _texto(f["codempresa"]) + _texto(f["nombreemp"]), _texto(f["codcc"]),
                  _texto(f["codcuenta"]), ym[:4], ym[4:], _fecha(f["fecmov"]), _fecha(f["fecusr"], "%d/%m/%Y %H:%M:%S"),
                  _fecha(f["fechatransac"]), "", documento, "A" if f["espropio"] == 1 else "M", "",
                  concepto, concepto, _texto(f["codusr"]), _texto(f["codmoneda"]), _texto(f["deha"]),
                  f"{_monto(f):.2f}"]
        lineas.append("".join("|" + c for c in campos))
    return ArchivoTexto(f"{b.emp.codempresa}{b.p.anio:04d}{mes:02d}V.txt", lineas)


def _hoja_auditor(b, titulo, subtitulo, encabezados, numericos, registros):
    """Tabla plana: las dos primeras columnas van en cuenta/nombre de la Fila."""
    vistos = defaultdict(int)
    unicos = []
    for e in encabezados:          # una Tabla de Excel no admite encabezados repetidos
        vistos[e] += 1
        unicos.append(e if vistos[e] == 1 else f"{e} {vistos[e]}")
    columnas = [Columna(e, ancho=14 if i + 2 in numericos else max(12, min(40, len(e) + 4)),
                        texto=i + 2 not in numericos) for i, e in enumerate(unicos[2:])]
    filas = [Fila(tipo="cuenta", cuenta=r[0], nombre=r[1], valores=r[2:]) for r in registros]
    rep = b.reporte(titulo, subtitulo, columnas, filas)
    rep.encabezado_a, rep.encabezado_b = (unicos[0], 18), (unicos[1], 18)
    rep.tabla_plana, rep.hoja = True, "Diario"
    rep.fecha_hora = None
    return rep


def diario_auditores(p):
    """con_diario_especial_auditores (plantilla Diario_especial_auditores.xls)."""
    ini, fin = _rango_meses(p)
    b, filas = _detalle_auditor(p, ini, fin)
    if p.archivo_texto:
        return _archivo_texto(b, filas, p.mes)
    encabezados = ["HEADER", "BUSINESS UNIT", "AFFILIATE", "DESCR254", "JOURNAL CLASS", "DOCUMENTO NO.",
                   "ACCOUNTING DT", "FOREIGN CURRENCY CD", "FOREIGN AMMOUNT", "ACCOUNT", "ACCOUNT NAME",
                   "ACCOUNT PS", "ACCOUNT NAME", "ACCOUNT NAME PS", "PROYECT", "ID", "LINE DESCRIPTION",
                   "LINE DESCRIPTION"]
    registros = [["", "", "", _fecha(f["fechatransac"]), _texto(f["keytra"]), str(int(f["numero"] or 0)),
                  _fecha(f["fecmov"]), _texto(f["codmoneda"]),
                  float(f["cargo"] or 0) if _texto(f["deha"]) == "D" else -float(f["abono"] or 0),
                  _texto(f["codcuenta"]), _texto(f["nomcta"]), _texto(f["codctainternacional"]),
                  _texto(f["nombrectainternacional"]), _texto(f["nombrectainternacional"]),
                  _texto(f["codcc"]), _texto(f["codci"]), "", _texto(f["descripcioncont"])] for f in filas]
    return _hoja_auditor(b, "SISTEMA DE CONTABILIDAD", f"PERIODO: DEL MES [{p.mes:02d}/{p.anio}]",
                         encabezados, {8}, registros)


def _registros_pwc(filas, version):
    out = []
    for f in filas:
        ym = _texto(f["yearmes"])
        comun_ini = [_texto(f["codempresa"]) + _texto(f["nombreemp"]), "A" if f["espropio"] == 1 else "M"]
        concepto = _texto(f["descripcioncont"])[:128]
        if version == 2011:
            fila = comun_ini + [_texto(f["serie"]) + str(int(f["numero"] or 0)), concepto, _texto(f["codtipo"]),
                                _texto(f["codcuenta"]), _texto(f["nomcta"]), _fecha(f["fecmov"]),
                                _fecha(f["fechatransac"]), f"{_texto(f['codperiodo'])}  {ym[:4]}"]
        else:
            fila = comun_ini + [_texto(f["keytra"]), concepto, _texto(f["codtipo"]), _texto(f["serie"]),
                                str(int(f["numero"] or 0)), _texto(f["codcuenta"]), _texto(f["nomcta"]),
                                _fecha(f["fecmov"], "%Y%m%d"), _fecha(f["fechatransac"], "%Y%m%d"),
                                f"{ym[:4]}-{ym[4:]}"]
        out.append(fila + [_monto(f), 1.0, _texto(f["deha"]), _texto(f["codusr"])])
    return out


def diario_pwc_2011(p):
    """con_diario_pwc_2011: un mes o el año completo («Año completo» = mes 0 en VFP)."""
    if p.anio_completo:
        ini, fin = date(p.anio, 1, 1), date(p.anio, 12, 31)
        subtitulo, mes = f"PERIODO: AÑO {p.anio}", 0
    else:
        ini, fin = date(p.anio, p.mes, 1), fin_de_mes(p.anio, p.mes)
        subtitulo, mes = f"PERIODO: DEL MES [{p.mes:02d}/{p.anio}]", p.mes
    b, filas = _detalle_auditor(p, ini, fin)
    if p.archivo_texto:
        return _archivo_texto(b, filas, mes)
    encabezados = ["EMPRESA", "TIPO POLIZA", "NO. POLIZA", "DESCRIPCION", "TIPO TRANSAC.", "NO. CUENTA",
                   "DESCRIPCION CUENTA", "F. ENTRADA", "F. POSTEO", "PERIODO y AÑO", "MONTO", "TASA CAMBIO",
                   "MOVIMIENTO", "USUARIO"]
    return _hoja_auditor(b, "SISTEMA DE CONTABILIDAD", subtitulo, encabezados, {10, 11},
                         _registros_pwc(filas, 2011))


def diario_pwc_2013(p):
    """con_diario_pwc_2013: «Meses a reportar» hasta el mes indicado."""
    ini, fin = _rango_meses(p)
    b, filas = _detalle_auditor(p, ini, fin)
    if p.archivo_texto:
        return _archivo_texto(b, filas, p.mes)
    encabezados = ["EMPRESA", "TIPO POLIZA", "NO. POLIZA", "DESCRIPCION", "TIPO TRANSAC.", "SERIE DOC.",
                   "NÚMERO DOC.", "NO. CUENTA", "DESCRIPCION CUENTA", "F. ENTRADA", "F. POSTEO", "PERIODO y AÑO",
                   "MONTO", "TASA CAMBIO", "MOVIMIENTO", "USUARIO"]
    return _hoja_auditor(b, "SISTEMA DE CONTABILIDAD", f"PERIODO: DEL MES [{p.mes:02d}/{p.anio}]",
                         encabezados, {12, 13}, _registros_pwc(filas, 2013))
