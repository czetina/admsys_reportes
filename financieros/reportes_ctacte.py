"""
Reportes de cuenta corriente (menú 50 de CTAXC / CTAXP). Un mismo formulario VFP sirve a CXC y a
CXP; `p.sistema` dice cuál.

  con_ctacte_integracion   Integración de saldos con antigüedad + hoja de resumen
  con_ctacte_estcta        Estado de cuenta: saldo inicial, movimientos del rango y saldo final

Respecto a VFP: una consulta para los saldos (ctacte.documentos) y, si se piden, una consulta por
dato adicional para TODO el reporte; VFP hacía de 1 a 3 consultas por documento
(transacciones_aplica_det, cuenta contable, comparación con contabilidad).
"""
from collections import OrderedDict, defaultdict
from datetime import date, timedelta

from core.db import consulta

from . import ctacte
from .contexto import ErrorReporte
from .modelo import Columna, Fila, Libro
from .reportes import Base

TITULO_SISTEMA = {"CXC": "CUENTAS POR COBRAR", "CXP": "CUENTAS POR PAGAR"}


def _texto_fecha(d):
    return d.strftime("%d/%m/%Y") if d else ""


class _CtaCte(Base):
    """Contexto común: empresa, sistema, filtro y leyendas de los reportes de cuenta corriente."""

    def __init__(self, p):
        super().__init__(p)
        if p.sistema not in ctacte.LADOS:
            raise ErrorReporte("Reporte sin sistema de cuenta corriente.")
        self.sistema = p.sistema
        self.fecha_fin = p.fecha_fin or date.today()
        self.moneda_local, self.nombre_moneda_local = ctacte.moneda_empresa(p.codempresa)

    def filtro(self, con_moneda=True):
        p = self.p
        return ctacte.FiltroCtaCte(
            fecha_fin=self.fecha_fin, cuenta_aux=p.cuenta_aux, dir_desde=p.directorio_desde,
            dir_hasta=p.directorio_hasta, no_contrato=p.no_contrato or 0,
            codmoneda=p.codmoneda if con_moneda else "", codaca=p.codaca, clasificaciones=list(p.clasificaciones))

    def leyenda_filtros(self):
        p, partes = self.p, []
        if p.codaca:
            partes.append(f"CLIENTE BASE: {p.codaca}")
        if p.directorio_desde or p.directorio_hasta:
            rango = p.directorio_desde if p.directorio_desde == p.directorio_hasta else \
                f"{p.directorio_desde or '…'} - {p.directorio_hasta or '…'}"
            partes.append(f"DIRECTORIO: {rango}")
        if p.clasificaciones:
            partes.append("CLASIFICACIÓN: " + ", ".join(p.clasificaciones))
        if p.no_contrato:
            partes.append(f"CONTRATO: {p.no_contrato}")
        if p.cuenta_aux:
            partes.append(f"CUENTA: {p.cuenta_aux}")
        return "REGISTROS FILTRADOS POR: " + " · ".join(partes) if partes else ""

    def reporte_cc(self, titulo_def, subtitulo, columnas, filas, hoja=""):
        filtros = self.leyenda_filtros()
        rep = self.reporte(titulo_def, f"{TITULO_SISTEMA[self.sistema]} · {subtitulo}"
                           + (f" · {filtros}" if filtros else ""), columnas, filas)
        rep.hoja = hoja
        return rep


def _lado_natural(sistema, lado):
    """Columna en que cae el saldo positivo del documento: CXC facturas → cargo, pagos → abono;
    CXP facturas → abono, pagos → cargo (VFP: filectacorriente MOVDE / MOVHA)."""
    if sistema == "CXC":
        return "cargo" if lado == "documentos" else "abono"
    return "abono" if lado == "documentos" else "cargo"


def _signo(sistema):
    """Saldo = cargo - abono en CXC (lo que nos deben) y abono - cargo en CXP (lo que debemos)."""
    return 1 if sistema == "CXC" else -1


# ------------------------------------------------------------ Integración de saldos

def _pagos_cliente(codempresa, keyrefs):
    """transacciones_aplica_det por keyaux (VFP: una consulta por documento)."""
    keyrefs = [k for k in set(keyrefs) if k]
    out = {}
    for i in range(0, len(keyrefs), 1000):
        lote = keyrefs[i:i + 1000]
        for f in consulta(f"""SELECT keyaux, SUM(montodocml) AS pago FROM transacciones_aplica_det
                              WHERE codempresa = %s AND keyaux IN ({','.join(['%s'] * len(lote))})
                              GROUP BY keyaux""", (codempresa, *lote)):
            out[f["keyaux"]] = float(f["pago"] or 0)
    return out


def _cuentas_contables(codempresa, sistema, docs):
    """Cuenta contable de cada documento sin cuenta_aux: la de su póliza con codsistema del módulo."""
    claves = {d["keytra"]: (d["keytra"] if d["usacnt"] == 1 else d["keyref"]) for d in docs if not d["cuenta_aux"]}
    polizas = [k for k in set(claves.values()) if k]
    por_poliza = {}
    for i in range(0, len(polizas), 1000):
        lote = polizas[i:i + 1000]
        for f in consulta(f"""SELECT d.keytra, MIN(d.codcuenta) AS codcuenta FROM dettransacciones d
                               JOIN empnomenclatura n ON n.codempresa = d.codempresa
                                AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
                              WHERE d.codempresa = %s AND n.codsistema = %s
                                AND d.keytra IN ({','.join(['%s'] * len(lote))})
                              GROUP BY d.keytra""", (codempresa, sistema, *lote)):
            por_poliza[f["keytra"]] = f["codcuenta"]
    return {doc: por_poliza.get(pol, "") for doc, pol in claves.items()}


def _lados(sis, tipo_saldos):
    if tipo_saldos == 2:          # «Solo deudores»
        return ("documentos",) if sis == "CXC" else ("pagos",)
    if tipo_saldos == 3:          # «Solo acreedores»
        return ("pagos",) if sis == "CXC" else ("documentos",)
    return ("documentos", "pagos")


def integracion(p):
    """con_ctacte_integracion: documentos con saldo, agrupados por clasificación y directorio, con antigüedad."""
    return _integracion(p, "saldo")


def integracion_me(p):
    """
    con_ctacte_integracion_me: la integración con los saldos en moneda extranjera (diferenciame);
    la columna de al lado muestra el saldo en moneda local. Solo documentos en moneda extranjera
    (VFP listaba también los de moneda local, con saldo 0).
    """
    return _integracion(p, "me")


def proyeccion(p):
    """
    con_proyeccion_de_pago_xls: saldo de cada documento repartido según su vencimiento: vencido
    (hasta la fecha de corte) y los siguientes N periodos de «intervalo» días; lo posterior va
    en «POSTERIOR» (VFP lo omitía). Solo el lado de los documentos (facturas): en CXP VFP pedía
    «solo deudores», que en CXP son los pagos / anticipos, y la proyección de pagos salía vacía.
    """
    return _integracion(p, "proyeccion")


def documentos_aplicados(p):
    """
    con_detalle_aplicados («Documentos pagados»): el mismo formulario que la integración, pero en
    cargo / abono va lo APLICADO (pagado) de cada documento con saldo, y la antigüedad no separa
    la columna «corriente» (lo no vencido cae en el primer tramo).
    """
    return _integracion(p, "aplicado")


def _integracion(p, monto):
    r = _CtaCte(p)
    sis = r.sistema
    lados = ("documentos",) if monto == "proyeccion" else _lados(sis, p.tipo_saldos)
    usa_trabajar = p.fecha_base == 2 and monto != "proyeccion"
    todos = ctacte.documentos(p.codempresa, sis, r.filtro(), lados, solo_pendientes=True)
    if monto == "proyeccion":       # VFP: diferencia <> 0, sin las reglas de moneda de solo_saldos
        docs = [d for d in todos if d["diferencia"]]
    else:
        docs = ctacte.solo_saldos(todos, p.codempresa, usa_fecha_trabajar=usa_trabajar, fecha_pago=r.fecha_fin)
    if monto == "me":
        docs = [d for d in docs if d["codmoneda"] != r.moneda_local and d["diferenciame"]]
    if not docs:
        raise ErrorReporte("No se encontraron movimientos.")
    clave_dir = (lambda d: d["razonsocial"]) if p.orden_cc == 1 else (lambda d: d["coddirectorio"])
    docs.sort(key=lambda d: (d["codclasifica"].upper(), clave_dir(d), d["coddirectorio"], d["fecmov"] or date.min,
                             d["serie"], d["numero"]))
    pagos = _pagos_cliente(p.codempresa, [d["keyref"] for d in docs])
    cuentas = _cuentas_contables(p.codempresa, sis, docs) if p.mostrar_contable else {}

    intervalo = max(1, p.intervalo or 30)
    ncols = max(1, min(p.columnas_antiguedad or 5, 30))
    if monto == "proyeccion":
        tramos = ["VENCIDO"] + [f"AL {_texto_fecha(r.fecha_fin + timedelta(days=intervalo * (i + 1)))}"
                                for i in range(ncols)] + ["POSTERIOR"]
    elif monto in ("saldo", "me"):
        tramos = ["CORRIENTE"] + [f"{1 + intervalo * i:,} - {intervalo * (i + 1):,}" for i in range(ncols - 1)] \
            + [f"MÁS DE {intervalo * (ncols - 1):,}"]
    else:   # sin columna «corriente»: el primer tramo incluye lo no vencido
        tramos = [f"{1 + intervalo * i if i else 0:,} - {intervalo * (i + 1):,}" for i in range(ncols)] \
            + [f"MÁS DE {intervalo * ncols:,}"]
    columnas = [Columna("SERIE", ancho=8, texto=True), Columna("NÚMERO", ancho=12, texto=True),
                Columna("NOMBRE / DESCRIPCIÓN", ancho=40, texto=True),
                Columna("PROVEEDOR" if sis == "CXP" else "NOMBRE DOC.", ancho=30, texto=True),
                Columna("MONEDA", ancho=8, texto=True), Columna("T/C", ancho=9),
                Columna("CARGO", "M. LOCAL"), Columna("CARGO", "M. EXTRANJERA"),
                Columna("ABONO", "M. LOCAL"), Columna("ABONO", "M. EXTRANJERA"),
                Columna("SALDO M. LOCAL")]
    i_tramo = len(columnas)
    columnas += [Columna(t, ancho=15) for t in tramos] + [Columna("TOTAL")]
    i_total = len(columnas) - 1
    columnas += [Columna("FECHA VENCIMIENTO", ancho=12, texto=True), Columna("DOC. REF.", ancho=16, texto=True)]
    i_limite = len(columnas)
    columnas.append(Columna("LÍMITE DE CRÉDITO"))
    i_pago = len(columnas)
    columnas.append(Columna("PAGO CLIENTE"))
    i_cuenta = len(columnas)
    columnas += [Columna("CUENTA CONTABLE", ancho=16, texto=True), Columna("CLASIFICACIÓN", ancho=14, texto=True)]
    n = len(columnas)
    numericas = [k for k, c in enumerate(columnas) if not c.texto and k not in (5, 10, i_limite)]

    filas, totales_clasif, directorios = [], [], []
    signo = _signo(sis)
    for clasif, docs_c in _agrupa(docs, lambda d: d["codclasifica"].upper()):
        filas.append(Fila(tipo="grupo", cuenta=clasif or "(SIN CLASIFICACIÓN)", nombre=""))
        inicio_clasif = len(filas)
        for coddir, docs_d in _agrupa(docs_c, lambda d: d["coddirectorio"]):
            cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=f"{clasif}|{coddir}", cuenta=coddir,
                       valores=[None] * n)
            cab.valores[2] = docs_d[0]["razonsocial"]      # el nombre va en la columna ancha
            filas.append(cab)
            saldo, bloque = 0.0, []
            for d in docs_d:
                v = [None] * n
                v[0], v[1] = d["serie"], str(d["numero"])
                v[2], v[3] = d["descontable"], d["nombre"]
                v[4] = "" if d["codmoneda"] == r.moneda_local else d["codmoneda"]
                v[5] = d["tasac"]
                importe = {"saldo": d["diferencia"], "aplicado": d["aplicadoml"], "me": d["diferenciame"],
                           "proyeccion": d["diferencia"]}[monto]
                cantidad = abs(importe)
                columna = _lado_natural(sis, d["lado"])
                if importe < 0:
                    columna = "abono" if columna == "cargo" else "cargo"
                k = 6 if columna == "cargo" else 8
                v[k] = cantidad
                if monto == "me":                 # al lado: el saldo en moneda local
                    v[k + 1] = abs(d["diferencia"])
                elif d["tasac"] not in (0, 1):
                    v[k + 1] = round(cantidad / d["tasac"], 2)
                neto = signo * ((v[6] or 0) - (v[8] or 0))
                saldo = round(saldo + neto, 2)
                v[10] = saldo
                vencido = (r.fecha_fin - d["fechavenc"]).days if d["fechavenc"] else 0
                if monto == "proyeccion":
                    faltan = -vencido            # días que faltan para el vencimiento
                    tramo = 0 if faltan <= 0 else min(-(-faltan // intervalo), ncols + 1)
                elif monto in ("saldo", "me"):
                    tramo = 0 if vencido <= 0 else min(-(-vencido // intervalo), ncols)
                else:
                    tramo = 0 if vencido <= 0 else min(-(-vencido // intervalo) - 1, ncols)
                v[i_tramo + tramo] = round(neto, 2)
                v[i_total] = round(neto, 2)
                v[i_total + 1], v[i_total + 2] = _texto_fecha(d["fechavenc"]), d["docref"]
                v[i_limite] = float(d["limite_cr_cxc"] or 0) if sis == "CXC" else None
                v[i_pago] = pagos.get(d["keyref"]) or None
                if p.mostrar_contable:
                    v[i_cuenta] = d["cuenta_aux"] or cuentas.get(d["keytra"], "")
                v[i_cuenta + 1] = d["codclasifica"]
                fecha = d["fecha_trabajar"] if usa_trabajar else d["fecmov"]
                bloque.append(Fila(tipo="cuenta", nivel=2, codigo=d["keytra"], cuenta=_texto_fecha(fecha),
                                   nombre=d["codtipo"], valores=v))
            filas += bloque
            for k in numericas:
                cab.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
            cab.hasta = bloque[-1]
            directorios.append((clasif, cab))
        total = Fila(tipo="total", negrita=True, rango=(filas[inicio_clasif], filas[-1]), valores=[None] * n)
        total.valores[2] = f"TOTALES {clasif}"
        for k in numericas:
            total.valores[k] = round(sum(c.valores[k] or 0 for cl, c in directorios if cl == clasif), 2)
        filas += [total, Fila()]
        totales_clasif.append(total)
    gran = Fila(tipo="total", negrita=True, refs=[(1, t) for t in totales_clasif], valores=[None] * n)
    for k in numericas:
        gran.valores[k] = round(sum(t.valores[k] or 0 for t in totales_clasif), 2)
    gran.valores[2] = "TOTALES"
    filas.append(gran)

    sub = ("FECHA DE VENCIMIENTOS AL: " if usa_trabajar else "FECHA DE MOVIMIENTOS AL: ") + _texto_fecha(r.fecha_fin)
    moneda = ctacte.nombre_moneda(p.codmoneda) if p.codmoneda else r.nombre_moneda_local
    if monto == "me" and not p.codmoneda:
        moneda = "moneda extranjera"
    titulo_def = {"saldo": "INTEGRACION DE SALDOS", "aplicado": "DOCUMENTOS PAGADOS",
                  "me": "INTEGRACION DE SALDOS M.E.",
                  "proyeccion": "PROYECCION DE COBROS" if sis == "CXC" else "PROYECCION DE PAGOS"}[monto]
    if monto == "me":
        for k in (6, 8):
            columnas[k].subtitulo, columnas[k + 1].subtitulo = "M. EXTRANJERA", "M. LOCAL"
        columnas[10].titulo = "SALDO M. EXTRANJERA"
    detalle = r.reporte_cc(titulo_def, sub, columnas, filas, hoja=titulo_def)
    detalle.encabezado_a, detalle.encabezado_b = ("FECHA", 11), ("TIPO/DOC", 9)
    detalle.grupos_columnas = [(6, 7, "CARGO"), (8, 9, "ABONO"),
                               (i_tramo, i_total, "PROYECCIÓN" if monto == "proyeccion" else "ANTIGÜEDAD DE SALDOS")]
    ocultas = set()
    if not p.mostrar_me and monto != "me":
        ocultas |= {4, 5, 7, 9}
    if not p.mostrar_proveedor:
        ocultas.add(3)
    if sis == "CXP":
        ocultas.add(i_limite)
    if not any(f.valores[i_pago] for f in filas if f.tipo == "cuenta" and f.nivel == 2):
        ocultas.add(i_pago)
    if not p.mostrar_contable:
        ocultas.add(i_cuenta)
    detalle.columnas_ocultas = ocultas
    detalle.moneda_titulo = moneda
    resumen = _resumen_integracion(r, directorios, totales_clasif, tramos, i_tramo, i_total, sub, titulo_def)
    titulo = (p.nombre or titulo_def).strip()
    return Libro(titulo, [detalle, resumen],
                 f"{titulo} {sis} {p.codempresa} {r.fecha_fin:%Y-%m-%d}.xlsx".replace("/", "-"))


def _agrupa(items, clave):
    grupos = OrderedDict()
    for it in items:
        grupos.setdefault(clave(it), []).append(it)
    return grupos.items()


def _resumen_integracion(r, directorios, totales_clasif, tramos, i_tramo, i_total, subtitulo, titulo_def):
    """Hoja RESUMEN: antigüedad por directorio (fórmulas a la hoja de detalle) y porcentajes."""
    columnas = [Columna(t, ancho=15) for t in tramos] + [Columna("TOTAL")]
    m = len(columnas)
    filas, totales = [], []
    for clasif, cabs in _agrupa(directorios, lambda x: x[0]):
        filas.append(Fila(tipo="grupo", cuenta=clasif or "(SIN CLASIFICACIÓN)"))
        bloque = []
        for _, cab in cabs:
            f = Fila(tipo="cuenta", nivel=1, cuenta=cab.cuenta, nombre=cab.valores[2] or "",
                     valores=[cab.valores[i_tramo + k] for k in range(m)])
            f.formulas = {k: ("refs", [(1, cab, i_tramo + k)]) for k in range(m)}
            bloque.append(f)
        filas += bloque
        total = Fila(tipo="total", nombre=f"TOTAL {clasif}", negrita=True, rango=(bloque[0], bloque[-1]),
                     valores=[round(sum(b.valores[k] or 0 for b in bloque), 2) for k in range(m)])
        filas += [total, Fila()]
        totales.append(total)
    gran = Fila(tipo="total", nombre="TOTALES", negrita=True, refs=[(1, t) for t in totales],
                valores=[round(sum(t.valores[k] for t in totales), 2) for k in range(m)])
    pct = Fila(tipo="total", nombre="PORCENTAJES", valores=[
        round(gran.valores[k] / gran.valores[-1], 4) if gran.valores[-1] else 0.0 for k in range(m)])
    pct.formulas = {k: ("ratio_filas", gran, k, m - 1) for k in range(m)}
    filas += [gran, pct]
    rep = r.reporte_cc(f"RESUMEN {titulo_def}", subtitulo, columnas, filas, hoja="RESUMEN")
    rep.encabezado_a, rep.encabezado_b = ("CÓDIGO", 14), ("NOMBRE", 45)
    rep.grupos_columnas = [(0, m - 1, "ANTIGÜEDAD DE SALDOS")]
    rep.porcentaje_filas = {id(pct)}
    rep.fecha_hora = None
    return rep


# ------------------------------------------------------------ Estado de cuenta

def _contabilidad(codempresa, docs):
    """
    Datos contables del Estado de cuenta (VFP: método comparacion_saldos, 3 consultas por documento):
    cargos / abonos de la póliza del documento en su cuenta de provisión, diferencial cambiario
    contabilizado (pólizas keydif en las cuentas de ganancia / pérdida cambiaria) y lo aplicado.
    """
    keys = [d["keytra"] for d in docs]
    cont, dif, apl = {}, {}, {}
    for i in range(0, len(keys), 1000):
        lote = keys[i:i + 1000]
        marcas = ",".join(["%s"] * len(lote))
        for f in consulta(f"""
                SELECT t.keytra, SUM(d.cargo) AS cargo, SUM(d.abono) AS abono, MIN(d.codcuenta) AS codcuenta
                  FROM transacciones t
                  JOIN dettransacciones d ON (d.keytra = t.keytra OR d.keyref = t.keytra) AND d.codcuenta = t.cuenta_aux
                 WHERE t.codempresa = %s AND t.keytra IN ({marcas})
                 GROUP BY t.keytra""", (codempresa, *lote)):
            cont[f["keytra"]] = (float(f["cargo"] or 0), float(f["abono"] or 0), f["codcuenta"])
        for f in consulta(f"""
                SELECT ta.keytra_apl AS keytra, SUM(d.cargo) AS cargo, SUM(d.abono) AS abono
                  FROM transacciones_aplica ta
                  JOIN dettransacciones d ON d.keytra = ta.keydif AND d.codsistema = 'CNT'
                  JOIN empperiodos p ON p.codempresa = d.codempresa AND p.codperiodo = d.codperiodo
                   AND (p.gananciacambiaria = d.codcuenta OR p.perdidacambiaria = d.codcuenta)
                 WHERE ta.codempresa = %s AND ta.keytra_apl IN ({marcas})
                 GROUP BY ta.keytra_apl""", (codempresa, *lote)):
            dif[f["keytra"]] = (float(f["cargo"] or 0), float(f["abono"] or 0))
        for f in consulta(f"""
                SELECT keytra_apl AS keytra, SUM(montodocml) AS ml, SUM(montome) AS me
                  FROM transacciones_aplica WHERE codempresa = %s AND keytra_apl IN ({marcas})
                 GROUP BY keytra_apl""", (codempresa, *lote)):
            apl[f["keytra"]] = (float(f["ml"] or 0), float(f["me"] or 0))
    return cont, dif, apl


def estado_cuenta(p):
    """con_ctacte_estcta: por directorio, saldo inicial, cada documento del rango y saldo final."""
    r = _CtaCte(p)
    sis = r.sistema
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    if fecha_ini > r.fecha_fin:
        raise ErrorReporte("La fecha inicial es posterior a la fecha final.")
    en_me = bool(p.codmoneda) and p.codmoneda != r.moneda_local
    filtro = r.filtro(con_moneda=False)
    # Saldo anterior sumado en la base (una fila por directorio) + solo los documentos del rango
    anteriores = ctacte.saldos_anteriores(p.codempresa, sis, filtro, fecha_ini, "estado", en_me)
    en_rango = ctacte.documentos_del_rango(p.codempresa, sis, filtro, fecha_ini)
    if not anteriores and not en_rango:
        raise ErrorReporte("No se encontraron movimientos.")
    # Índice de VFP: fecha + serie + número; en empate (factura y su nota con la misma serie y número)
    # queda primero el documento y luego el pago, el orden en que _saldos_cuenta_corriente los arma.
    en_rango.sort(key=lambda d: (d["coddirectorio"], d["fecmov"] or date.min, d["serie"], str(d["numero"]),
                                 d["lado"] != "documentos", d["idtransac"] or 0))
    por_dir = defaultdict(list)
    for d in en_rango:
        por_dir[d["coddirectorio"]].append(d)
    nombres = {k: v["razonsocial"] for k, v in anteriores.items()}
    nombres.update({k: v[0]["razonsocial"] for k, v in por_dir.items()})
    orden = sorted(nombres, key=lambda k: (nombres[k][:50], k))
    signo = _signo(sis)
    cont, dif, apl = _contabilidad(p.codempresa, en_rango) if p.mostrar_contable else ({}, {}, {})

    columnas = [Columna("SERIE", ancho=8, texto=True), Columna("NÚMERO", ancho=12, texto=True),
                Columna("NOMBRE / DESCRIPCIÓN", ancho=40, texto=True),
                Columna("PROVEEDOR" if sis == "CXP" else "NOMBRE DOC.", ancho=30, texto=True),
                Columna("MONEDA EXTRANJERA", ancho=10, texto=True), Columna("T/C", ancho=9),
                Columna("SALDO INICIAL"), Columna("CARGOS", "ML"), Columna("CARGOS", "ME"),
                Columna("ABONOS", "ML"), Columna("ABONOS", "ME"), Columna("DIF. CAMBIARIO"), Columna("SALDO FINAL"),
                Columna("FECHA VENCIMIENTO", ancho=12, texto=True), Columna("DÍAS CRÉDITO", ancho=8),
                Columna("ACT.", ancho=6, texto=True),
                Columna("CONTABILIDAD", "CARGOS"), Columna("CONTABILIDAD", "ABONOS"),
                Columna("DIF. CAMBIARIO CONTAB.", "ABONO"), Columna("DIF. CAMBIARIO CONTAB.", "CARGO"),
                Columna("DIFERENCIA D. CAMBIARIO"), Columna("DIFERENCIA AUXILIAR VS. CONTABILIDAD"),
                Columna("CUENTA", ancho=16, texto=True), Columna("CLASIFICACIÓN", ancho=14, texto=True)]
    n = len(columnas)
    I_CONT = 16

    def movimiento(d):
        """(cargo, abono) del documento como en VFP: el lado del archivo, invertido si es ajuste."""
        valor = d["valorme"] if en_me else d["valordocml"]
        cargo_natural = (d["filectacorriente"].endswith("DE"))
        if d["esajuste"]:
            cargo_natural = not cargo_natural
        return (valor, 0.0) if cargo_natural else (0.0, valor)

    filas, cabeceras, saldo_ini_total = [], [], 0.0
    for coddir in orden:
        del_rango = por_dir.get(coddir, [])
        saldo = anteriores.get(coddir, {}).get("saldo", 0.0)
        if not del_rango and not saldo:
            continue
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=coddir, cuenta=coddir, valores=[None] * n)
        cab.valores[2] = nombres[coddir]
        cab.valores[6] = cab.valores[12] = saldo
        filas.append(cab)
        saldo_ini_total += saldo
        bloque = []
        for d in del_rango:
            v = [None] * n
            c, a = movimiento(d)
            v[0], v[1], v[2], v[3] = d["serie"], str(d["numero"]), d["descontable"], d["nombre"]
            v[4] = "" if d["codmoneda"] == r.moneda_local else d["codmoneda"]
            v[5] = d["tran_tasac_apl"] if d["filectacorriente"].endswith("HA") else d["tasac"]
            if c:
                v[7] = c
            if a:
                v[9] = a
            if not en_me and d["codmoneda"] != r.moneda_local and d["valorme"]:
                v[8 if d["filectacorriente"].endswith("DE") else 10] = d["valorme"]
            dc = 0.0 if en_me else d["dcambiario"]
            if d["keytra"] in apl and not en_me:       # diferencial pendiente de contabilizar (VFP: nDcambiarioTempo)
                ml, me = apl[d["keytra"]]
                vme = d["valorme"]
                if vme and abs(vme - me) < 0.005 and round(d["valordocml"] - (ml + d["dcambiario"]), 2) != 0:
                    dc = round(d["valordocml"] - ml, 2)
            v[11] = dc or None
            saldo = round(saldo + signo * ((v[7] or 0) - (v[9] or 0)) + (v[11] or 0), 2)
            v[12] = saldo
            v[13] = _texto_fecha(d["fechavenc"])
            v[14] = (d["fechavenc"] - d["fecmov"]).days if d["fechavenc"] and d["fecmov"] else None
            v[15] = "SI" if d["actualizadocnt"] == 1 else "NO"
            if p.mostrar_contable:
                cc, ca, cuenta = cont.get(d["keytra"], (0.0, 0.0, ""))
                neto = cc - ca
                v[I_CONT] = neto if neto > 0 else None
                v[I_CONT + 1] = abs(neto) if neto < 0 else None
                dcc, dca = dif.get(d["keytra"], (0.0, 0.0))
                v[I_CONT + 2], v[I_CONT + 3] = dca or None, dcc or None
                v[I_CONT + 4] = round(signo * ((v[11] or 0) + dca - dcc), 2)
                v[I_CONT + 5] = round(signo * (((v[7] or 0) + (v[9] or 0)) - ((v[I_CONT] or 0) + (v[I_CONT + 1] or 0))), 2)
                v[I_CONT + 6] = d["cuenta_aux"] or cuenta
            v[I_CONT + 7] = d["codclasifica"]
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=d["keytra"], cuenta=_texto_fecha(d["fecmov"]),
                               nombre=d["codtipo"], valores=v))
        filas += bloque
        if bloque:
            for k in (7, 8, 9, 10, 11) + tuple(range(I_CONT, I_CONT + 6)):
                cab.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
                cab.formulas[k] = ("subtotal", bloque[0], bloque[-1])
            cab.valores[12] = saldo
            cab.formulas[12] = ("lineal", [(1, 6), (signo, 7), (-signo, 9), (1, 11)])
        cabeceras.append(cab)
        filas.append(Fila())
    if not cabeceras:
        raise ErrorReporte("No se encontraron movimientos.")
    total = Fila(tipo="total", negrita=True, refs=[(1, c) for c in cabeceras], valores=[None] * n)
    for k in (6, 7, 8, 9, 10, 11, 12) + tuple(range(I_CONT, I_CONT + 6)):
        total.valores[k] = round(sum(c.valores[k] or 0 for c in cabeceras), 2)
    total.valores[2] = "TOTALES"
    filas.append(total)

    moneda = ctacte.nombre_moneda(p.codmoneda) if en_me else r.nombre_moneda_local
    rep = r.reporte_cc("ESTADO DE CUENTA", f"DEL {_texto_fecha(fecha_ini)} AL {_texto_fecha(r.fecha_fin)}",
                       columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("TIPO/DOC", 9)
    rep.grupos_columnas = [(7, 8, "CARGOS"), (9, 10, "ABONOS"), (I_CONT, I_CONT + 1, "REGISTRADO EN CONTABILIDAD"),
                           (I_CONT + 2, I_CONT + 3, "D. CAMBIARIO CONTABILIZADO")]
    ocultas = set()
    if not p.mostrar_me:
        ocultas |= {4, 5, 8, 10}
    if not p.mostrar_proveedor:
        ocultas.add(3)
    if not p.mostrar_actualizado:
        ocultas.add(15)
    if not p.mostrar_contable:
        ocultas |= set(range(I_CONT, I_CONT + 7))
    rep.columnas_ocultas = ocultas
    rep.moneda_titulo = moneda
    return rep


# ------------------------------------------------------------ Documentos pendientes

MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")


def documentos_pendientes(p):
    """
    con_ctacte_pendientes (Cobros / Pagos pendientes): documentos con saldo cuya fecha de movimiento
    (o de vencimiento) cae en el rango, agrupados por directorio o por mes de vencimiento.
    """
    r = _CtaCte(p)
    sis = r.sistema
    por_venc = p.fecha_base == 2
    fecha_ini = p.fecha_ini
    filtro = r.filtro()
    if por_venc:
        filtro.venc_desde, filtro.venc_hasta = fecha_ini, r.fecha_fin
    else:
        filtro.fecmov_desde = fecha_ini
    docs = ctacte.solo_saldos(ctacte.documentos(p.codempresa, sis, filtro, _lados(sis, p.tipo_saldos), solo_pendientes=True), p.codempresa,
                              usa_fecha_trabajar=por_venc, fecha_pago=r.fecha_fin)
    if not docs:
        raise ErrorReporte("No se encontraron movimientos.")
    por_mes = p.orden_cc == 2
    if por_mes:
        docs.sort(key=lambda d: (d["fechavenc"] or date.min, d["razonsocial"], d["fecmov"] or date.min,
                                 d["numerodoc"] or ""))
        grupo = (lambda d: (d["fechavenc"].year, d["fechavenc"].month) if d["fechavenc"] else (0, 0))
    else:
        docs.sort(key=lambda d: (d["razonsocial"], d["coddirectorio"], d["fecmov"] or date.min, d["numerodoc"] or ""))
        grupo = (lambda d: d["coddirectorio"])
    columnas = [Columna("NOMBRE DOC.", ancho=34, texto=True), Columna("TIPO DOC.", ancho=9, texto=True),
                Columna("No. DOC.", ancho=16, texto=True), Columna("MON", ancho=6, texto=True),
                Columna("CARGO"), Columna("ABONO"), Columna("SALDO"), Columna("CONCEPTO", ancho=45, texto=True)]
    n = len(columnas)
    signo = _signo(sis)
    filas, cabeceras = [], []
    for clave, docs_g in _agrupa(docs, grupo):
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=str(clave), valores=[None] * n)
        if por_mes:
            cab.valores[0] = f"{MESES[clave[1] - 1]} {clave[0]}" if clave[0] else "SIN VENCIMIENTO"
        else:
            cab.cuenta, cab.valores[0] = clave, docs_g[0]["razonsocial"]
        filas.append(cab)
        saldo, bloque = 0.0, []
        for d in docs_g:
            v = [None] * n
            v[0], v[1], v[2], v[3] = d["nombre"], d["codtipo"], d["numerodoc"] or str(d["numero"]), d["codmoneda"]
            columna = _lado_natural(sis, d["lado"])
            if d["diferencia"] < 0:
                columna = "abono" if columna == "cargo" else "cargo"
            v[4 if columna == "cargo" else 5] = abs(d["diferencia"])
            saldo = round(saldo + signo * ((v[4] or 0) - (v[5] or 0)), 2)
            v[6], v[7] = saldo, d["descontable"]
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=d["keytra"], cuenta=_texto_fecha(d["fecmov"]),
                               nombre=_texto_fecha(d["fechavenc"]), valores=v))
        filas += bloque + [Fila()]
        for k in (4, 5):
            cab.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
            cab.formulas[k] = ("subtotal", bloque[0], bloque[-1])
        cab.valores[6] = saldo
        cab.formulas[6] = ("lineal", [(signo, 4), (-signo, 5)])
        cabeceras.append(cab)
    total = Fila(tipo="total", negrita=True, refs=[(1, c) for c in cabeceras], valores=[None] * n)
    for k in (4, 5, 6):
        total.valores[k] = round(sum(c.valores[k] or 0 for c in cabeceras), 2)
    total.valores[0] = "TOTALES"
    filas.append(total)
    base = "VENCIMIENTOS" if por_venc else "MOVIMIENTOS"
    rango = f"DEL {_texto_fecha(fecha_ini)} " if fecha_ini else ""
    titulo_def = "COBROS PENDIENTES" if sis == "CXC" else "PAGOS PENDIENTES"
    rep = r.reporte_cc(titulo_def, f"FECHA DE {base} {rango}AL {_texto_fecha(r.fecha_fin)} · "
                       f"SALDOS EN MONEDA LOCAL [{r.moneda_local}]", columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("FECHA MOVIMIENTO", 12), ("FECHA VENCIMIENTO", 12)
    return rep


# ------------------------------------------------------------ Saldos positivos y negativos

def positivos_negativos(p):
    """
    con_ctacte_integracion_positivos_negativos: saldo neto de cada directorio (por clasificación) con
    antigüedad fija (corriente, 1-30, 31-60, 61-90, 91-120, más de 120), separando los saldos
    positivos (a favor) de los negativos.
    """
    r = _CtaCte(p)
    sis = r.sistema
    docs = ctacte.solo_saldos(ctacte.documentos(p.codempresa, sis, r.filtro(), _lados(sis, p.tipo_saldos), solo_pendientes=True),
                              p.codempresa, usa_fecha_trabajar=p.fecha_base == 2, fecha_pago=r.fecha_fin)
    if not docs:
        raise ErrorReporte("No se encontraron movimientos.")
    limites = (0, 30, 60, 90, 120)
    tramos = ["CORRIENTE", "1 - 30", "31 - 60", "61 - 90", "91 - 120", "MÁS DE 120"]
    saldos = OrderedDict()
    clave_dir = (lambda d: d["razonsocial"]) if p.orden_cc == 1 else (lambda d: d["coddirectorio"])
    docs.sort(key=lambda d: (d["codclasifica"].upper(), clave_dir(d), d["coddirectorio"]))
    for d in docs:
        # VFP: la diferencia de los pagos (CXCMOVHA / CXPMOVDE) se vuelve negativa
        neto = -d["diferencia"] if d["lado"] == "pagos" else d["diferencia"]
        k = (d["codclasifica"].upper(), d["coddirectorio"])
        s = saldos.setdefault(k, {"nombre": d["razonsocial"], "total": 0.0, "tramos": [0.0] * len(tramos)})
        dias = (r.fecha_fin - d["fechavenc"]).days if d["fechavenc"] else 0
        tramo = sum(1 for lim in limites if dias > lim)
        s["total"] += neto
        s["tramos"][tramo] += neto
    columnas = [Columna("SALDO")] + [Columna(t, ancho=15) for t in tramos] + [Columna("TOTAL")]
    n = len(columnas)
    filas, totales_signo = [], []
    for titulo, positivo in (("POSITIVOS", True), ("NEGATIVOS", False)):
        grupo = [(k, s) for k, s in saldos.items() if (round(s["total"], 2) > 0) == positivo and round(s["total"], 2)]
        if not grupo:
            continue
        filas.append(Fila(tipo="grupo", cuenta=titulo))
        subtotales = []
        for clasif, items in _agrupa(grupo, lambda x: x[0][0]):
            filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=clasif or "(SIN CLASIFICACIÓN)",
                              valores=[None] * n))
            bloque = []
            for (_, coddir), s in items:
                v = [round(s["total"], 2)] + [round(x, 2) for x in s["tramos"]]
                v.append(round(sum(v[1:]), 2))
                f = Fila(tipo="cuenta", nivel=2, cuenta=coddir, nombre=s["nombre"], valores=v)
                f.formulas[n - 1] = ("suma_cols", 1, n - 2)
                bloque.append(f)
            filas += bloque
            sub = Fila(tipo="total", nombre=f"SUBTOTAL - [{clasif}]", negrita=True, rango=(bloque[0], bloque[-1]),
                       valores=[round(sum(b.valores[k] for b in bloque), 2) for k in range(n)])
            filas += [sub, Fila()]
            subtotales.append(sub)
        tot = Fila(tipo="total", nombre=f"TOTAL {titulo}", negrita=True, refs=[(1, s_) for s_ in subtotales],
                   valores=[round(sum(s_.valores[k] for s_ in subtotales), 2) for k in range(n)])
        filas += [tot, Fila()]
        totales_signo.append(tot)
    gran = Fila(tipo="total", nombre="TOTAL GENERAL", negrita=True, refs=[(1, t) for t in totales_signo],
                valores=[round(sum(t.valores[k] for t in totales_signo), 2) for k in range(n)])
    filas.append(gran)
    sub = ("FECHA DE VENCIMIENTOS AL: " if p.fecha_base == 2 else "FECHA DE MOVIMIENTOS AL: ") + _texto_fecha(r.fecha_fin)
    rep = r.reporte_cc("SALDOS POSITIVOS Y NEGATIVOS", sub, columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("CÓDIGO", 14), ("NOMBRE", 45)
    rep.grupos_columnas = [(1, n - 2, "ANTIGÜEDAD DE SALDOS")]
    if p.codmoneda:
        rep.moneda_titulo = ctacte.nombre_moneda(p.codmoneda)
    return rep


# ------------------------------------------------------------ Detalle por cuenta (una hoja por directorio)

def detalle_por_cuenta(p):
    """
    con_ctacte_detalle: hoja RESUMEN + una hoja por directorio con sus movimientos del rango
    (cargos / abonos en moneda local y extranjera, diferencial cambiario y saldo corrido).

    VFP filtraba la consulta desde la fecha inicial y después calculaba el saldo anterior con los
    documentos ANTERIORES a esa fecha, que ya no estaban: el saldo anterior siempre salía en 0.
    Aquí se calcula con los documentos previos.
    """
    r = _CtaCte(p)
    sis = r.sistema
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    filtro = r.filtro()
    filtro.codtipo = p.codtipo
    # Solo los documentos del rango; el saldo anterior, sumado en la base por directorio
    docs = ctacte.documentos_del_rango(p.codempresa, sis, filtro, fecha_ini)
    if not docs:
        raise ErrorReporte("No se encontraron movimientos.")
    anteriores = ctacte.saldos_anteriores(p.codempresa, sis, filtro, fecha_ini, "lado")
    docs.sort(key=lambda d: (d["coddirectorio"], d["fecmov"] or date.min, d["numerodoc"] or ""))
    signo = _signo(sis)
    hoy = date.today()
    columnas = [Columna("TIPO", ancho=8, texto=True), Columna("SERIE", ancho=8, texto=True),
                Columna("NÚMERO", ancho=12, texto=True), Columna("DESCRIPCIÓN", ancho=45, texto=True),
                Columna("MONEDA", ancho=8, texto=True), Columna("T/C", ancho=9),
                Columna("CARGOS", "M. LOCAL"), Columna("CARGOS", "M. EXTRANJERA"),
                Columna("ABONOS", "M. LOCAL"), Columna("ABONOS", "M. EXTRANJERA"),
                Columna("DIF. CAMBIARIO"), Columna("SALDO"), Columna("DÍAS", ancho=7),
                Columna("FECHA VENCIMIENTO", ancho=12, texto=True)]
    n = len(columnas)
    hojas, resumen_filas, usados = [], [], set()
    for coddir, docs_d in _agrupa(docs, lambda d: d["coddirectorio"]):
        del_rango = [d for d in docs_d if d["fecmov"] and d["fecmov"] >= fecha_ini]
        if not del_rango:
            continue
        d0 = docs_d[0]
        # como VFP: valordocml - dcambiario, positivo en el archivo de los documentos
        saldo = anteriores.get(coddir, {}).get("saldo", 0.0)
        filas = []
        datos = [("NIT", d0["nit"]), ("DIRECCIÓN", d0["direccion"]), ("TELÉFONO", d0["telefono"]),
                 ("DÍAS DE CRÉDITO", int(d0["dias_cr_cxc"] if sis == "CXC" else d0["dias_cr_cxp"] or 0))]
        if sis == "CXC":
            datos.append(("LÍMITE DE CRÉDITO", float(d0["limite_cr_cxc"] or 0)))
        for etiqueta, valor in datos:
            v = [None] * n
            v[3] = valor if isinstance(valor, str) else f"{valor:,}"
            filas.append(Fila(tipo="cuenta", nivel=1, nombre=etiqueta, valores=v))
        filas.append(Fila())
        inicial = Fila(tipo="cuenta", nivel=1, negrita=True, nombre="SALDO ANTERIOR", valores=[None] * n)
        inicial.valores[11] = saldo
        filas.append(inicial)
        bloque = []
        for num, d in enumerate(del_rango, 1):
            v = [None] * n
            v[0], v[1], v[2], v[3] = d["codtipo"], d["serie"], str(d["numero"]), d["descontable"]
            v[4] = "" if d["codmoneda"] == r.moneda_local else d["codmoneda"]
            v[5] = d["tran_tasac"]
            cargo_lado = d["filectacorriente"].endswith("DE")
            v[6 if cargo_lado else 8] = d["valordocml"]
            if d["valorme"]:
                v[7 if cargo_lado else 9] = d["valorme"]
            if d["dcambiario"]:
                v[10] = d["dcambiario"]
            saldo = round(saldo + signo * ((v[6] or 0) - (v[8] or 0)) + (v[10] or 0), 2)
            v[11] = saldo
            v[12] = (hoy - d["fecmov"]).days if d["fecmov"] else None
            v[13] = _texto_fecha(d["fechavenc"])
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=d["keytra"], cuenta=str(num), nombre=_texto_fecha(d["fecmov"]),
                               valores=v))
        filas += bloque
        total = Fila(tipo="total", nombre="TOTALES", negrita=True, rango=(bloque[0], bloque[-1]), valores=[None] * n)
        for k in (6, 7, 8, 9, 10):
            total.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
        total.valores[11] = saldo
        total.formulas[11] = ("refs", [(1, bloque[-1], 11)])
        filas += [Fila(), total]
        nombre_hoja = _hoja_directorio(coddir, usados)
        hoja = r.reporte_cc("DETALLE POR CUENTA", f"[{coddir}] {d0['razonsocial']} · DEL {_texto_fecha(fecha_ini)} AL "
                            f"{_texto_fecha(r.fecha_fin)}", columnas, filas, hoja=nombre_hoja)
        hoja.encabezado_a, hoja.encabezado_b = ("No.", 6), ("FECHA", 11)
        hoja.grupos_columnas = [(6, 7, "CARGOS"), (8, 9, "ABONOS")]
        hoja.vinculo_regreso = "RESUMEN"
        hoja.fecha_hora = None
        hojas.append(hoja)
        fila_res = Fila(tipo="cuenta", nivel=1, cuenta=coddir, nombre=d0["razonsocial"], vinculo=nombre_hoja,
                        valores=[total.valores[6], total.valores[7], total.valores[8], total.valores[9], saldo])
        fila_res.formulas = {0: ("refs", [(1, total, 6)]), 1: ("refs", [(1, total, 7)]), 2: ("refs", [(1, total, 8)]),
                             3: ("refs", [(1, total, 9)]), 4: ("refs", [(1, total, 11)])}
        resumen_filas.append(fila_res)
    if not hojas:
        raise ErrorReporte("No se encontraron movimientos.")
    res_cols = [Columna("CARGOS", "M. LOCAL"), Columna("CARGOS", "M. EXTRANJERA"), Columna("ABONOS", "M. LOCAL"),
                Columna("ABONOS", "M. EXTRANJERA"), Columna("SALDO")]
    total = Fila(tipo="total", nombre="TOTALES", negrita=True, rango=(resumen_filas[0], resumen_filas[-1]),
                 valores=[round(sum(f.valores[k] or 0 for f in resumen_filas), 2) for k in range(5)])
    resumen = r.reporte_cc("DETALLE POR CUENTA - RESUMEN", f"DEL {_texto_fecha(fecha_ini)} AL {_texto_fecha(r.fecha_fin)}",
                           res_cols, resumen_filas + [Fila(), total], hoja="RESUMEN")
    resumen.encabezado_a, resumen.encabezado_b = ("CÓDIGO", 14), ("NOMBRE", 45)
    resumen.grupos_columnas = [(0, 1, "CARGOS"), (2, 3, "ABONOS")]
    titulo = (p.nombre or "DETALLE POR CUENTA").strip()
    return Libro(titulo, [resumen] + hojas, f"{titulo} {sis} {p.codempresa} {r.fecha_fin:%Y-%m-%d}.xlsx")


def _hoja_directorio(coddir, usados):
    """Nombre de hoja «DIR(código)» como VFP, sin caracteres inválidos y sin repetir."""
    base = "DIR(" + "".join("_" if c in '[]:*?/\\' else c for c in coddir.strip()) + ")"
    nombre, i = base[:31], 2
    while nombre.upper() in usados:
        nombre = f"{base[:27]}~{i}"
        i += 1
    usados.add(nombre.upper())
    return nombre


# ------------------------------------------------------------ Estado de cuenta para el cliente (Crystal)

SQL_ESTCTA_CLIENTE = """
SELECT t.keytra, t.fecmov, t.serie, t.numero, t.codtipo, t.descontable, t.filectacorriente,
       ROUND(t.valordocml - t.noaplica_ml, 2) AS monto, t.coddirectorio,
       IFNULL(g.razonsocial, '') AS razonsocial, IFNULL(g.codaca, '') AS codaca,
       IFNULL(a.nombre_codaca, '') AS nombre_codaca
  FROM transacciones t
  JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
  LEFT JOIN grucodaca a ON a.codgrupo = g.codgrupo AND a.codaca = g.codaca
 WHERE t.codempresa = %s AND t.anulado = 0
   AND LEFT(t.filectacorriente, 3) = %s
   AND t.fecmov <= %s
   {filtros}
 ORDER BY g.codaca, t.coddirectorio, t.fecmov, t.serie, t.numero
"""


def estado_cuenta_cliente(p):
    """
    con_ctacte_estcta_crystal (Crystal «Estado de Cuenta Cliente.rpt»), el estado de cuenta que se
    envía al cliente: por cliente base y directorio, saldo inicial, documentos del periodo con su
    monto (cargos positivos, pagos negativos), subtotal y «total a pagar».
    """
    r = _CtaCte(p)
    sis = r.sistema
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    partes, params = [], []
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s"),
                       (p.codaca, "g.codaca = %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    filas_bd = consulta(SQL_ESTCTA_CLIENTE.format(filtros=" ".join(partes)),
                        (p.codempresa, sis, r.fecha_fin, *params))
    if not filas_bd:
        raise ErrorReporte("No se encontraron movimientos.")
    positivo = {"CXC": "CXCMOVDE", "CXP": "CXPMOVHA"}[sis]
    columnas = [Columna("SERIE", ancho=8, texto=True), Columna("NÚMERO", ancho=12, texto=True),
                Columna("DESCRIPCIÓN", ancho=50, texto=True), Columna("CARGOS"), Columna("PAGOS"), Columna("MONTO")]
    n = len(columnas)
    filas, totales = [], []
    for codaca, de_aca in _agrupa(filas_bd, lambda f: f["codaca"]):
        anteriores = [f for f in de_aca if f["fecmov"] < fecha_ini]
        saldo_ini = round(sum(float(f["monto"]) * (1 if f["filectacorriente"] == positivo else -1) for f in anteriores), 2)
        del_periodo = [f for f in de_aca if f["fecmov"] >= fecha_ini]
        if not del_periodo and not saldo_ini:
            continue
        nombre = de_aca[0]["nombre_codaca"] or ("SIN CLIENTE BASE" if not codaca else codaca)
        filas.append(Fila(tipo="grupo", cuenta=codaca, nombre=nombre))
        ini = Fila(tipo="cuenta", nivel=1, negrita=True, valores=[None] * n)
        ini.valores[2], ini.valores[5] = "SALDO INICIAL", saldo_ini
        filas.append(ini)
        cargos = pagos = 0.0
        subtotales = []
        for coddir, docs in _agrupa(del_periodo, lambda f: f["coddirectorio"]):
            cab = Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=coddir, valores=[None] * n)
            cab.valores[2] = docs[0]["razonsocial"]
            filas.append(cab)
            bloque = []
            for f in docs:
                monto = float(f["monto"]) * (1 if f["filectacorriente"] == positivo else -1)
                v = [None] * n
                v[0], v[1], v[2] = (f["serie"] or "").strip(), str(int(f["numero"] or 0)), (f["descontable"] or "").strip()
                v[3 if monto >= 0 else 4] = abs(monto)
                v[5] = round(monto, 2)
                bloque.append(Fila(tipo="cuenta", nivel=2, codigo=f["keytra"], cuenta=_texto_fecha(f["fecmov"]),
                                   nombre=(f["codtipo"] or "").strip(), valores=v))
            filas += bloque
            sub = Fila(tipo="total", negrita=True, rango=(bloque[0], bloque[-1]), valores=[None] * n)
            for k in (3, 4, 5):
                sub.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
            sub.valores[2] = "SUB-TOTAL"
            filas.append(sub)
            subtotales.append(sub)
            cargos += sub.valores[3]
            pagos += sub.valores[4]
        total = Fila(tipo="total", negrita=True, refs=[(1, ini)] + [(1, s) for s in subtotales], valores=[None] * n)
        total.valores[2] = f"TOTAL A PAGAR · Saldo inicial {saldo_ini:,.2f} · Cargos del mes {cargos:,.2f} · " \
                           f"Pagos recibidos {pagos:,.2f}"
        total.valores[3], total.valores[4] = round(cargos, 2), round(pagos, 2)
        total.valores[5] = round(saldo_ini + cargos - pagos, 2)
        total.formulas = {3: ("refs", [(1, s, 3) for s in subtotales]), 4: ("refs", [(1, s, 4) for s in subtotales]),
                          5: ("refs", [(1, ini, 5)] + [(1, s, 5) for s in subtotales])}
        filas += [total, Fila()]
        totales.append(total)
    if not totales:
        raise ErrorReporte("No se encontraron movimientos.")
    rep = r.reporte_cc("ESTADO DE CUENTA",
                       f"PERIODO COMPRENDIDO DEL {_texto_fecha(fecha_ini)} AL {_texto_fecha(r.fecha_fin)}",
                       columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("TIPO", 8)
    return rep


# ------------------------------------------------------------ Estado de cuenta para moras (CXC)

def estado_cuenta_moras(p):
    """
    con_ctacte_estcta_moras: por cliente, saldo al día anterior a la fecha inicial, pagos del
    periodo y saldo en mora (saldo inicial no cubierto por los pagos); solo los que quedan en mora.

    VFP sumaba como «abonos» TODOS los pagos con fecha <= fecha inicial (que ya estaban restados
    en el saldo inicial) en lugar de los pagos del periodo; aquí son los pagos entre la fecha
    inicial y la final.
    """
    r = _CtaCte(p)
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    filtro = r.filtro(con_moneda=False)
    # Saldo anterior sumado en la base por cliente + solo los pagos del periodo
    anteriores = ctacte.saldos_anteriores(p.codempresa, r.sistema, filtro, fecha_ini, "lado")
    pagos = ctacte.documentos_del_rango(p.codempresa, r.sistema, filtro, fecha_ini, lados=("pagos",))
    if not anteriores and not pagos:
        raise ErrorReporte("No se encontraron movimientos.")
    datos = {k: {"nombre": v["razonsocial"], "ini": v["saldo"], "abonos": 0.0} for k, v in anteriores.items()}
    for d in pagos:
        x = datos.setdefault(d["coddirectorio"], {"nombre": d["razonsocial"], "ini": 0.0, "abonos": 0.0})
        x["abonos"] += d["valordocml"] - d["dcambiario"]
    datos = OrderedDict(sorted(datos.items()))
    columnas = [Columna(f"SALDO INICIAL AL {_texto_fecha(fecha_ini - timedelta(days=1))}"),
                Columna("PAGOS DEL PERIODO"), Columna(f"SALDO EN MORA AL {_texto_fecha(r.fecha_fin)}")]
    filas = []
    for coddir, x in datos.items():
        mora = round(x["ini"] - x["abonos"], 2)
        if mora > 0:
            f = Fila(tipo="cuenta", nivel=1, cuenta=coddir, nombre=x["nombre"],
                     valores=[round(x["ini"], 2), round(x["abonos"], 2), mora])
            f.formulas[2] = ("lineal", [(1, 0), (-1, 1)])
            filas.append(f)
    if not filas:
        raise ErrorReporte("No hay clientes con saldo en mora.")
    total = Fila(tipo="total", nombre="TOTALES", negrita=True, rango=(filas[0], filas[-1]),
                 valores=[round(sum(f.valores[k] for f in filas), 2) for k in range(3)])
    rep = r.reporte_cc("ESTADO DE CUENTA PARA MORAS", f"AL: {_texto_fecha(r.fecha_fin)}", columnas, filas + [Fila(), total])
    rep.encabezado_a, rep.encabezado_b = ("CÓDIGO", 14), ("NOMBRE", 50)
    return rep


# ------------------------------------------------------------ Documentos no aplicados

def no_aplicados(p):
    """
    con_documentos_no_aplicados: documentos con saldo por directorio, con el saldo anterior a la
    fecha inicial y antigüedad por fecha del documento (hasta 30, 31-60, 61-90, 91-120, más de 120).
    VFP ponía en la antigüedad la diferencia sin signo (los abonos sumaban como cargos); aquí va
    con signo (cargo - abono en CXC, abono - cargo en CXP), igual que el saldo.
    """
    r = _CtaCte(p)
    sis = r.sistema
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    docs = ctacte.solo_saldos(ctacte.documentos(p.codempresa, sis, r.filtro(), _lados(sis, p.tipo_saldos), solo_pendientes=True),
                              p.codempresa, usa_fecha_trabajar=p.fecha_base == 2, fecha_pago=r.fecha_fin)
    if not docs:
        raise ErrorReporte("No se encontraron movimientos.")
    en_me = bool(p.codmoneda) and p.codmoneda != r.moneda_local
    docs.sort(key=lambda d: (d["coddirectorio"], d["fecmov"] or date.min, d["numerodoc"] or ""))
    signo = _signo(sis)
    tramos = ["HASTA 30", "31 - 60", "61 - 90", "91 - 120", "MÁS DE 120"]
    columnas = [Columna("NOMBRE DOC.", ancho=34, texto=True), Columna("TIPO", ancho=8, texto=True),
                Columna("DOCUMENTO", ancho=16, texto=True), Columna("MON", ancho=6, texto=True),
                Columna("CARGO"), Columna("ABONO"), Columna("SALDO")] + [Columna(t, ancho=14) for t in tramos] \
        + [Columna("TOTAL"), Columna("CONCEPTO", ancho=45, texto=True)]
    n = len(columnas)
    filas, cabeceras = [], []
    for coddir, docs_d in _agrupa(docs, lambda d: d["coddirectorio"]):
        saldo = 0.0
        for d in (x for x in docs_d if x["fecmov"] and x["fecmov"] < fecha_ini):
            neto = d["valorme"] if en_me else d["valordocml"] - d["dcambiario"]
            saldo += neto if d["lado"] == "documentos" else -neto
        saldo = round(saldo * (1 if sis == "CXC" else 1), 2)
        rango = [d for d in docs_d if d["fecmov"] and d["fecmov"] >= fecha_ini]
        if not rango and not saldo:
            continue
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=coddir, valores=[None] * n)
        cab.valores[0], cab.valores[6] = docs_d[0]["razonsocial"], saldo
        filas.append(cab)
        bloque = []
        for d in rango:
            v = [None] * n
            v[0], v[1], v[2] = d["nombre"], d["codtipo"], f"{d['serie']}{d['numero']}"
            v[3] = p.codmoneda or ""
            importe = d["diferenciame"] if en_me else d["diferencia"]
            natural = _lado_natural(sis, d["lado"])
            v[4 if natural == "cargo" else 5] = importe
            neto = signo * ((v[4] or 0) - (v[5] or 0))
            saldo = round(saldo + neto, 2)
            v[6] = saldo
            dias = (r.fecha_fin - d["fecmov"]).days if d["fecmov"] else 0
            v[7 + sum(1 for lim in (30, 60, 90, 120) if dias > lim)] = round(neto, 2)
            v[12], v[13] = round(neto, 2), d["descontable"]
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=d["keytra"], cuenta=_texto_fecha(d["fecmov"]),
                               nombre=d["coddirectorio"], valores=v))
        filas += bloque
        if bloque:
            for k in (4, 5) + tuple(range(7, 13)):
                cab.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
                cab.formulas[k] = ("subtotal", bloque[0], bloque[-1])
            cab.valores[6] = saldo
        filas.append(Fila())
        cabeceras.append(cab)
    if not cabeceras:
        raise ErrorReporte("No se encontraron movimientos.")
    total = Fila(tipo="total", negrita=True, refs=[(1, c) for c in cabeceras], valores=[None] * n)
    for k in (4, 5, 6) + tuple(range(7, 13)):
        total.valores[k] = round(sum(c.valores[k] or 0 for c in cabeceras), 2)
    total.valores[0] = "TOTALES"
    filas.append(total)
    base = "VENCIMIENTOS" if p.fecha_base == 2 else "MOVIMIENTOS"
    rep = r.reporte_cc("DOCUMENTOS NO APLICADOS",
                       f"FECHA DE {base} DEL {_texto_fecha(fecha_ini)} AL {_texto_fecha(r.fecha_fin)}", columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    rep.grupos_columnas = [(7, 12, "ANTIGÜEDAD (FECHA DEL DOCUMENTO)")]
    if en_me:
        rep.moneda_titulo = ctacte.nombre_moneda(p.codmoneda)
    return rep


# ------------------------------------------------------------ Saldos para cobros / pagos electrónicos (TD)

def tabla_dinamica(p):
    """
    con_ctacte_tabla_dinamica: la misma base que «Saldos para pagos electrónicos» con las columnas
    de sucursal y los tramos fijos de VFP (000, 030, 060, 090, 120, 121 días). El método VFP tenía
    3,125 líneas, casi todas #DEFINE de constantes de Excel.
    """
    return saldos_pagos_electronicos(p, tabla=True)


def saldos_pagos_electronicos(p, tabla=False):
    """
    con_ctacte_td_programacion_pe: hoja «Datos» (Tabla de Excel con todos los documentos con saldo,
    como el cursor que VFP exportaba con COPY TO ... TYPE XLS) + hoja «Analisis» con el saldo por
    clasificación y directorio en columnas de días de crédito (la tabla dinámica de VFP).

    Los días se cuentan a la fecha de corte (VFP usaba la fecha del día, DATE()) y el tramo 91-120
    se rotula bien (VFP: «[91 - 90]»).
    """
    r = _CtaCte(p)
    sis = r.sistema
    docs = ctacte.solo_saldos(ctacte.documentos(p.codempresa, sis, r.filtro(), _lados(sis, p.tipo_saldos), solo_pendientes=True),
                              p.codempresa, usa_fecha_trabajar=p.fecha_base == 2, fecha_pago=r.fecha_fin)
    if not docs:
        raise ErrorReporte("No se encontraron movimientos.")
    intervalo = 30 if tabla else max(1, p.intervalo or 30)
    ncols = 5 if tabla else max(2, min(p.columnas_antiguedad or 5, 30))
    if tabla:
        tramos = ["000", "030", "060", "090", "120", "121"]
    else:
        tramos = ["CORRIENTE"] + [f"{1 + intervalo * i:03d} - {intervalo * (i + 1):03d}" for i in range(ncols - 1)] \
            + [f"{intervalo * (ncols - 1) + 1:03d} +"]
    negativo = {"CXC": "CXCMOVHA", "CXP": "CXPMOVDE"}[sis]
    campos = ["Directorio", "Nombre_Directorio", "Clasificacion", "Cuenta_Aux", "Codigo_Acaparador", "Nombre", "Nit",
              "Referencia", "Tipo_Doc", "Moneda", "Fecha", "Numero_Doc", "Fec_Venc", "Dias_Cr", "Tasa_Cambio",
              "TipoMov", "Valor_Ml", "Valor_Me", "Valor_Total_Ml", "Pagado_Ml", "Saldo_Ml", "Descripcion", "Llave",
              "Llave_ref"] + (["Sucursal", "Sucursal_Nombre", "Sucursal_Razonsocial"] if tabla else [])
    numericos = {"Tasa_Cambio", "Valor_Ml", "Valor_Me", "Valor_Total_Ml", "Pagado_Ml", "Saldo_Ml"}
    registros, suma = [], defaultdict(lambda: defaultdict(float))
    nombres = {}
    for d in sorted(docs, key=lambda d: (d["codclasifica"], d["razonsocial"], d["fecmov"] or date.min)):
        s = -1 if d["filectacorriente"] == negativo else 1
        dias = (r.fecha_fin - d["fechavenc"]).days if d["fechavenc"] else 0
        tramo = 0 if dias <= 0 else min(-(-dias // intervalo), ncols)
        saldo = round(s * d["diferencia"], 2)
        registros.append({
            "Directorio": d["coddirectorio"], "Nombre_Directorio": d["razonsocial"], "Clasificacion": d["codclasifica"],
            "Cuenta_Aux": d["cuenta_aux"], "Codigo_Acaparador": d["codaca"], "Nombre": d["nombre"], "Nit": d["nit"],
            "Referencia": d["docref"], "Tipo_Doc": d["codtipo"], "Moneda": d["codmoneda"], "Fecha": _texto_fecha(d["fecmov"]),
            "Numero_Doc": (d["serie"] + "-" if d["serie"] else "") + str(d["numero"]),
            "Fec_Venc": _texto_fecha(d["fechavenc"]), "Dias_Cr": tramos[tramo], "Tasa_Cambio": d["tasac"],
            "TipoMov": d["filectacorriente"], "Valor_Ml": round(s * d["valor"], 2), "Valor_Me": round(s * d["valorme"], 2),
            "Valor_Total_Ml": round(s * d["valordocml"], 2),
            "Pagado_Ml": round(s * (d["valordocml"] - d["diferencia"]), 2), "Saldo_Ml": saldo,
            "Descripcion": d["descontable"][:240], "Llave": d["keytra"], "Llave_ref": d["keyref"] or "",
            "Sucursal": str(d["sucursal"] or ""), "Sucursal_Nombre": d["sucursal_nombre"],
            "Sucursal_Razonsocial": d["sucursal_razonsocial"]})
        suma[(d["codclasifica"], d["coddirectorio"])][tramo] += saldo
        nombres[d["coddirectorio"]] = d["razonsocial"]
    titulo = (p.nombre or ("TABLA DINAMICA DE CUENTA CORRIENTE" if tabla else "SALDOS PARA PAGOS ELECTRONICOS")).strip()
    sub = ("POR VENCIMIENTOS AL: " if p.fecha_base == 2 else "POR MOVIMIENTOS AL: ") + _texto_fecha(r.fecha_fin)
    datos = r.reporte_cc(titulo, sub, [Columna(c.upper(), ancho=14 if c in numericos else 18, texto=c not in numericos)
                                       for c in campos[2:]],
                         [Fila(tipo="cuenta", cuenta=x["Directorio"], nombre=x["Nombre_Directorio"],
                               valores=[x[c] for c in campos[2:]]) for x in registros], hoja="Datos")
    datos.tabla_plana = True
    datos.encabezado_a, datos.encabezado_b = ("DIRECTORIO", 14), ("NOMBRE_DIRECTORIO", 34)
    # Análisis: clasificación > directorio, columnas por días de crédito
    columnas = [Columna(t, ancho=15) for t in tramos] + [Columna("SALDO")]
    m = len(columnas)
    filas, totales = [], []
    for clasif, items in _agrupa(sorted(suma.items(), key=lambda x: (x[0][0], nombres[x[0][1]])), lambda x: x[0][0]):
        filas.append(Fila(tipo="grupo", cuenta=clasif or "(SIN CLASIFICACIÓN)"))
        bloque = []
        for (_, coddir), por_tramo in items:
            v = [round(por_tramo.get(k, 0.0), 2) for k in range(len(tramos))]
            v.append(round(sum(v), 2))
            f = Fila(tipo="cuenta", nivel=1, cuenta=coddir, nombre=nombres[coddir], valores=v)
            f.formulas[m - 1] = ("suma_cols", 0, m - 2)
            bloque.append(f)
        filas += bloque
        tot = Fila(tipo="total", nombre=f"TOTAL {clasif}", negrita=True, rango=(bloque[0], bloque[-1]),
                   valores=[round(sum(b.valores[k] for b in bloque), 2) for k in range(m)])
        filas += [tot, Fila()]
        totales.append(tot)
    gran = Fila(tipo="total", nombre="TOTAL GENERAL", negrita=True, refs=[(1, t) for t in totales],
                valores=[round(sum(t.valores[k] for t in totales), 2) for k in range(m)])
    filas.append(gran)
    analisis = r.reporte_cc(titulo, f"{sub} · Suma de SALDO_ML por DÍAS DE CRÉDITO", columnas, filas, hoja="Analisis")
    analisis.encabezado_a, analisis.encabezado_b = ("DIRECTORIO", 14), ("NOMBRE", 45)
    analisis.grupos_columnas = [(0, m - 2, "DÍAS DE CRÉDITO")]
    analisis.fecha_hora = None
    return Libro(titulo, [analisis, datos], f"{titulo} {sis} {p.codempresa} {r.fecha_fin:%Y-%m-%d}.xlsx")


# ------------------------------------------------------------ Diagnóstico: aplicaciones incongruentes

SQL_INCONGRUENTES = """
SELECT t.keytra, t.fecmov, t.codtipo, t.serie, t.numero, t.coddirectorio, t.nombre, t.valordocml, t.filectacorriente,
       IFNULL(r.aplicado, 0) AS recibe, IFNULL(g.aplicado, 0) AS da
  FROM transacciones t
  LEFT JOIN (SELECT keytra, SUM(montodocml) AS aplicado FROM transacciones_aplica
              WHERE codempresa = %s GROUP BY keytra) r ON r.keytra = t.keytra
  LEFT JOIN (SELECT keytra_apl, SUM(montodocml) AS aplicado FROM transacciones_aplica
              WHERE codempresa = %s GROUP BY keytra_apl) g ON g.keytra_apl = t.keytra
 WHERE t.codempresa = %s AND t.anulado = 0 AND t.filectacorriente <> '' AND LEFT(t.filectacorriente, 3) = %s
   AND t.codproceso = '' AND t.fecmov BETWEEN %s AND %s {filtros}
   AND ((r.keytra IS NOT NULL AND g.keytra_apl IS NOT NULL)
        OR t.valordocml - (IFNULL(r.aplicado, 0) + IFNULL(g.aplicado, 0)) < 0)
 ORDER BY t.keytra
"""

SQL_APLICACIONES_DE = """
SELECT ta.keytra_apl AS doc, 1 AS orden, o.keytra, o.fecmov, o.codtipo, o.serie, o.numero, o.nombre,
       o.filectacorriente, ta.montodocml
  FROM transacciones_aplica ta JOIN transacciones o ON o.codempresa = ta.codempresa AND o.keytra = ta.keytra
 WHERE ta.codempresa = %s AND ta.keytra_apl IN ({marcas})
UNION ALL
SELECT ta.keytra AS doc, 2 AS orden, o.keytra, o.fecmov, o.codtipo, o.serie, o.numero, o.nombre,
       o.filectacorriente, ta.montodocml
  FROM transacciones_aplica ta JOIN transacciones o ON o.codempresa = ta.codempresa AND o.keytra = ta.keytra_apl
 WHERE ta.codempresa = %s AND ta.keytra IN ({marcas})
"""


def aplicaciones_incongruentes(p):
    """
    con_incongruencias_de_aplicaciones: documentos que reciben y dan aplicaciones a la vez, o cuyo
    valor es menor que lo aplicado, con el detalle de sus aplicaciones y la diferencia.
    VFP buscaba las aplicaciones con una consulta por documento y su filtro de directorio aplicaba
    DTOS() a un código (no funcionaba); aquí son dos consultas y el filtro sí aplica.
    """
    r = _CtaCte(p)
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    partes, params = [], []
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    docs = consulta(SQL_INCONGRUENTES.format(filtros=" ".join(partes)),
                    (p.codempresa, p.codempresa, p.codempresa, r.sistema, fecha_ini, r.fecha_fin, *params))
    if not docs:
        raise ErrorReporte("No se encontraron aplicaciones incongruentes.")
    detalle = defaultdict(list)
    keys = [d["keytra"] for d in docs]
    for i in range(0, len(keys), 1000):
        lote = keys[i:i + 1000]
        marcas = ",".join(["%s"] * len(lote))
        for a in consulta(SQL_APLICACIONES_DE.format(marcas=marcas), (p.codempresa, *lote, p.codempresa, *lote)):
            detalle[a["doc"]].append(a)
    columnas = [Columna("SERIE", ancho=8, texto=True), Columna("NÚMERO", ancho=12, texto=True),
                Columna("NOMBRE", ancho=40, texto=True), Columna("VALOR"), Columna("APLICADO"), Columna("DIFERENCIA"),
                Columna("ARCHIVO", ancho=10, texto=True), Columna("LLAVE", ancho=28, texto=True)]
    filas = []
    for d in docs:
        apls = sorted(detalle.get(d["keytra"], []), key=lambda a: (a["orden"], a["fecmov"] or date.min))
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=d["keytra"], cuenta=_texto_fecha(_fecha_ok(d["fecmov"])),
                   nombre=(d["codtipo"] or "").strip(), valores=[
                       (d["serie"] or "").strip(), str(int(d["numero"] or 0)), (d["nombre"] or "").strip(),
                       float(d["valordocml"] or 0), None, None, d["filectacorriente"], d["keytra"]])
        filas.append(cab)
        bloque = [Fila(tipo="cuenta", nivel=2, codigo=a["keytra"], cuenta=_texto_fecha(_fecha_ok(a["fecmov"])),
                       nombre=(a["codtipo"] or "").strip(),
                       valores=[(a["serie"] or "").strip(), str(int(a["numero"] or 0)), (a["nombre"] or "").strip(),
                                None, float(a["montodocml"] or 0), None, a["filectacorriente"], a["keytra"]])
                  for a in apls]
        filas += bloque
        cab.valores[4] = round(sum(b.valores[4] for b in bloque), 2)
        cab.valores[5] = round(cab.valores[3] - cab.valores[4], 2)
        if bloque:
            cab.formulas[4] = ("subtotal", bloque[0], bloque[-1])
        cab.formulas[5] = ("resta", 3, 4)
        filas.append(Fila())
    rep = r.reporte_cc("INCONGRUENCIAS EN APLICACIONES",
                       f"DEL {_texto_fecha(fecha_ini)} AL {_texto_fecha(r.fecha_fin)}", columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("TIPO", 8)
    if not p.mostrar_llaves:
        rep.columnas_ocultas = {6, 7}
    return rep


def _fecha_ok(v):
    return v if isinstance(v, date) else None


# ------------------------------------------------------------ Diagnóstico: registros con problemas

def registros_con_problemas(p):
    """
    con_registros_con_problemas: documentos en moneda extranjera registrados con tipo de cambio 1
    (fuera de contabilidad y activos fijos), con totales en moneda extranjera y local.
    """
    r = _CtaCte(p)
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    filas_bd = consulta("""
        SELECT t.keytra, t.fecmov, t.coddirectorio, t.nombre, t.codtipo, t.serie, t.numero, t.codmoneda, t.tasac,
               t.valorme, t.valordocml, t.descontable, t.filectacorriente
          FROM transacciones t JOIN empresas e ON e.codempresa = t.codempresa
         WHERE t.codempresa = %s AND t.codmoneda <> e.codmoneda AND t.codsistema NOT IN ('CNT', 'ACF')
           AND LEFT(t.filectacorriente, 3) = %s AND t.filectacorriente <> '' AND t.anulado = 0
           AND t.tasac = 1 AND t.fecmov BETWEEN %s AND %s
         ORDER BY t.fecmov, t.codtipo, t.serie, t.numero""", (p.codempresa, r.sistema, fecha_ini, r.fecha_fin))
    if not filas_bd:
        raise ErrorReporte("No se encontró información con los filtros ingresados.")
    datos = [Fila(tipo="cuenta", cuenta=_texto_fecha(_fecha_ok(f["fecmov"])), nombre=f["coddirectorio"], valores=[
        (f["nombre"] or "").strip(), (f["codtipo"] or "").strip(), (f["serie"] or "").strip(), int(f["numero"] or 0),
        f["codmoneda"], float(f["tasac"] or 0), float(f["valorme"] or 0), float(f["valordocml"] or 0),
        (f["descontable"] or "").strip(), f["filectacorriente"][:3]]) for f in filas_bd]
    columnas = [Columna("NOMBRE EN DOC.", ancho=34, texto=True), Columna("TIPO", ancho=8, texto=True),
                Columna("SERIE", ancho=8, texto=True), Columna("NÚMERO", ancho=12), Columna("MONEDA", ancho=8, texto=True),
                Columna("TASA DE CAMBIO", ancho=12), Columna("VALOR MONEDA EXTRANJERA"), Columna("VALOR MONEDA LOCAL"),
                Columna("DESCRIPCIÓN", ancho=45, texto=True), Columna("AUXILIAR QUE AFECTA", ancho=10, texto=True)]
    rep = r.reporte_cc("REGISTROS DE OPERACIONES EN DÓLARES CON PROBLEMAS",
                       f"FECHA DESDE {_texto_fecha(fecha_ini)} HASTA {_texto_fecha(r.fecha_fin)}", columnas, datos,
                       hoja="Problemas")
    rep.tabla_plana = True
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("CÓDIGO", 14)
    return rep


# ------------------------------------------------------------ Consulta de documentos (CXP)

ESTADOS_DOC = {
    "todos": ("Todos", ""),
    "actualizados": ("Actualizados", "AND t.actualizadocnt = 1"),
    "desactualizados": ("Desactualizados", "AND t.actualizadocnt = 0 AND t.usacnt = 1"),
    "anulados": ("Anulados", "AND t.anulado = 1"),
    "contables": ("Contables", "AND t.usacnt = 1"),
    "no_contables": ("No contables", "AND t.usacnt = 0"),
}


def consulta_documentos(p):
    """
    con_empdoctos_con_impuestos (Consulta de documentos): documentos del sistema con sus impuestos
    (IVA, retención ISR, timbre de prensa), agrupados por tipo de documento.
    En VFP la opción «solo sin número» no devolvía nada: la consulta pedía numero <> 0 y numero = 0.
    """
    r = _CtaCte(p)
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    partes, params = [], []
    for valor, sql in ((p.codtipo, "t.codtipo = %s"), (p.directorio_desde, "t.coddirectorio >= %s"),
                       (p.directorio_hasta, "t.coddirectorio <= %s"), (p.docref, "t.docref = %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    if p.codmoneda and p.codmoneda != r.moneda_local:
        partes.append("AND t.codmoneda = %s")
        params.append(p.codmoneda)
    if p.clasificaciones:
        partes.append(f"AND t.codclasifica IN ({','.join(['%s'] * len(p.clasificaciones))})")
        params += list(p.clasificaciones)
    partes.append(ESTADOS_DOC.get(p.estado_doc, ESTADOS_DOC["todos"])[1])
    filas_bd = consulta(f"""
        SELECT t.keytra, t.fecmov, t.coddirectorio, IFNULL(g.razonsocial, '') AS razonsocial, t.codtipo, t.serie,
               t.numero, t.codmoneda, t.deha, t.valordocml, t.valorme, t.tasac, t.iva,
               t.ret_facesp_comp_valorml + t.ret_facesp_serv_valorml AS ret_isr, t.timbre_prensa, t.usacnt,
               t.actualizadocnt, t.docref, t.sucursal, t.codsistema, t.descontable, t.anulado, t.creusr
          FROM transacciones t
          LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
         WHERE t.codempresa = %s AND t.codsistema = %s AND t.numero <> 0 AND t.fecmov BETWEEN %s AND %s
               {' '.join(partes)}
         ORDER BY t.fecmov, t.codsistema, t.codtipo, t.serie, t.numero""",
        (p.codempresa, r.sistema, fecha_ini, r.fecha_fin, *params))
    if not filas_bd:
        raise ErrorReporte("No se encontraron registros con los parámetros ingresados.")
    columnas = [Columna("NOMBRE", ancho=36, texto=True), Columna("SERIE", ancho=8, texto=True),
                Columna("NÚMERO", ancho=12, texto=True), Columna("MON", ancho=6, texto=True),
                Columna("DEBE", "M. LOCAL"), Columna("HABER", "M. LOCAL"), Columna("DEBE", "M. EXTRANJERA"),
                Columna("HABER", "M. EXTRANJERA"), Columna("T/C", ancho=9), Columna("IVA"), Columna("RET. ISR"),
                Columna("TIMBRE"), Columna("CNT", ancho=5, texto=True), Columna("ACT", ancho=5, texto=True),
                Columna("DOC. REF.", ancho=16, texto=True), Columna("SUCURSAL", ancho=8, texto=True),
                Columna("LLAVE", ancho=26, texto=True), Columna("DESCRIPCIÓN", ancho=50, texto=True),
                Columna("CREADO POR", ancho=12, texto=True)]
    n = len(columnas)
    filas, totales = [], []
    for tipo, docs in _agrupa(sorted(filas_bd, key=lambda f: ((f["codtipo"] or "").strip(), f["fecmov"], f["serie"],
                                                              f["numero"])), lambda f: (f["codtipo"] or "").strip()):
        filas.append(Fila(tipo="grupo", cuenta=tipo))
        bloque = []
        for f in docs:
            anulado = f["anulado"] == 1
            debe = (f["deha"] or "").strip() == "D"
            v = [None] * n
            v[0] = "*** A N U L A D O ***" if anulado else f["razonsocial"]
            v[1], v[2], v[3] = (f["serie"] or "").strip(), str(int(f["numero"] or 0)), f["codmoneda"]
            ml, me = float(f["valordocml"] or 0), float(f["valorme"] or 0)
            v[4 if debe else 5] = ml or None
            v[6 if debe else 7] = me or None
            v[8], v[9], v[10], v[11] = float(f["tasac"] or 0), float(f["iva"] or 0) or None, \
                float(f["ret_isr"] or 0) or None, float(f["timbre_prensa"] or 0) or None
            v[12], v[13] = "X" if f["usacnt"] == 1 else "", "X" if f["actualizadocnt"] == 1 else ""
            v[14], v[15], v[16] = (f["docref"] or "").strip(), str(f["sucursal"] or ""), f["keytra"]
            v[17] = "*** A N U L A D O ***" if anulado else (f["descontable"] or "").strip()[:300]
            v[18] = f["creusr"] or ""
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=f["keytra"], cuenta=_texto_fecha(_fecha_ok(f["fecmov"])),
                               nombre=f["coddirectorio"], valores=v))
        filas += bloque
        tot = Fila(tipo="total", negrita=True, rango=(bloque[0], bloque[-1]), valores=[None] * n)
        tot.valores[0] = f"TOTAL {tipo}"
        for k in (4, 5, 6, 7, 9, 10, 11):
            tot.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
        filas += [tot, Fila()]
        totales.append(tot)
    rep = r.reporte_cc(f"{r.sistema} - CONSULTA DE DOCUMENTOS", f"PERIODO: {_texto_fecha(fecha_ini)} - "
                       f"{_texto_fecha(r.fecha_fin)} · {ESTADOS_DOC.get(p.estado_doc, ESTADOS_DOC['todos'])[0]}",
                       columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("CÓDIGO", 14)
    rep.grupos_columnas = [(4, 5, "M. LOCAL"), (6, 7, "M. EXTRANJERA")]
    ocultas = set()
    if not p.mostrar_llaves:
        ocultas |= {12, 13, 14, 15, 16}
    if not p.mostrar_actualizado:
        ocultas.add(18)
    rep.columnas_ocultas = ocultas
    return rep


# ------------------------------------------------------------ Cuadre: auxiliar vs. contabilidad

def auxiliar_vs_contabilidad(p):
    """
    det_conta_vrs_auxiliar (pantalla de cuadre en VFP): por sistema y tipo de documento, cargos y
    abonos registrados en contabilidad en las cuentas de control (rango de cuentas) contra los
    registrados en el auxiliar (documentos de cuenta corriente), con la diferencia, más el saldo
    inicial y final de ambos lados.

    La pantalla VFP mezclaba en una sola consulta los documentos con cuenta de provisión y las
    líneas contables de sus pólizas, y en la parte contable tenía fijo CXCMOVDE / CXCMOVHA aun
    para CXP; aquí cada lado se calcula por separado.
    """
    r = _CtaCte(p)
    sis = r.sistema
    if not (p.cuenta_desde or p.cuenta_hasta):
        raise ErrorReporte("Indique el rango de cuentas de control (cuenta inicio y fin).")
    fecha_ini = p.fecha_ini or date(r.fecha_fin.year, r.fecha_fin.month, 1)
    cta_ini, cta_fin = p.cuenta_desde or p.cuenta_hasta, p.cuenta_hasta or p.cuenta_desde
    inicio_ej = r.corte.periodo.fecini
    # Contabilidad: movimientos en las cuentas de control, por sistema / tipo de documento
    cont = defaultdict(lambda: [0.0, 0.0])
    saldo_cnt = 0.0
    for f in consulta("""
            SELECT t.codsistema, t.codtipo, (t.fecmov < %s) AS previo, SUM(d.cargo) AS cargo, SUM(d.abono) AS abono
              FROM transacciones t JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
             WHERE t.codempresa = %s AND t.actualizadocnt = 1 AND t.anulado = 0
               AND t.fecmov BETWEEN %s AND %s AND d.codcuenta BETWEEN %s AND %s
             GROUP BY t.codsistema, t.codtipo, (t.fecmov < %s)""",
            (fecha_ini, p.codempresa, inicio_ej, r.fecha_fin, cta_ini, cta_fin, fecha_ini)):
        c, a = float(f["cargo"] or 0), float(f["abono"] or 0)
        if f["previo"]:
            saldo_cnt += c - a
        else:
            k = ((f["codsistema"] or "").strip(), (f["codtipo"] or "").strip())
            cont[k][0] += c
            cont[k][1] += a
    # Auxiliar: documentos de cuenta corriente
    filtro = r.filtro(con_moneda=False)
    aux = defaultdict(lambda: [0.0, 0.0])
    saldo_aux = 0.0
    for d in ctacte.documentos(p.codempresa, sis, filtro):
        if not d["fecmov"]:
            continue
        cargo = d["filectacorriente"].endswith("DE")
        if d["fecmov"] < fecha_ini:
            neto = d["valordocml"] - d["dcambiario"]
            saldo_aux += neto if cargo else -neto
        else:
            k = ((d["codsistema"] or "").strip(), d["codtipo"])
            aux[k][0 if cargo else 1] += d["valordocml"]
    if not cont and not aux:
        raise ErrorReporte("No se encontraron movimientos.")
    nombres = {(f["codsistema"].strip(), f["codtipo"].strip()): f["nomtipodoc"] for f in consulta(
        "SELECT codsistema, codtipo, MAX(nomtipodoc) AS nomtipodoc FROM empdoctos WHERE codempresa = %s "
        "GROUP BY codsistema, codtipo", (p.codempresa,))}
    columnas = [Columna("NOMBRE DOCUMENTO", ancho=34, texto=True), Columna("CONTABILIDAD", "CARGOS"),
                Columna("CONTABILIDAD", "ABONOS"), Columna("AUXILIAR", "CARGOS"), Columna("AUXILIAR", "ABONOS"),
                Columna("DIFERENCIA", "CARGOS", formula=("resta", 1, 3)),
                Columna("DIFERENCIA", "ABONOS", formula=("resta", 2, 4))]
    filas = []
    ini = Fila(tipo="cuenta", nivel=1, negrita=True, nombre="SALDO INICIAL",
               valores=["", round(saldo_cnt, 2), None, round(saldo_aux, 2), None, round(saldo_cnt - saldo_aux, 2), None])
    ini.formulas = {5: ("resta", 1, 3)}
    filas += [ini, Fila()]
    bloque = []
    for k in sorted(set(cont) | set(aux)):
        cc, ca = cont.get(k, (0.0, 0.0))
        ac, aa = aux.get(k, (0.0, 0.0))
        bloque.append(Fila(tipo="cuenta", nivel=1, cuenta=k[0], nombre=k[1], valores=[
            nombres.get(k, "NO DEFINIDO CORRECTAMENTE"), round(cc, 2), round(ca, 2), round(ac, 2), round(aa, 2),
            round(cc - ac, 2), round(ca - aa, 2)]))
    filas += bloque
    tot = Fila(tipo="total", nombre="TOTALES DEL PERIODO", negrita=True, rango=(bloque[0], bloque[-1]),
               valores=[None] + [round(sum(b.valores[i] for b in bloque), 2) for i in range(1, 7)])
    signo_cnt = 1  # contabilidad: cargo - abono
    fin_cnt = round(saldo_cnt + tot.valores[1] - tot.valores[2], 2)
    fin_aux = round(saldo_aux + tot.valores[3] - tot.valores[4], 2)
    fin = Fila(tipo="total", nombre="SALDO FINAL (cargo - abono)", negrita=True,
               valores=[None, fin_cnt * signo_cnt, None, fin_aux, None, round(fin_cnt - fin_aux, 2), None])
    filas += [Fila(), tot, fin]
    rep = r.reporte_cc("COMPARATIVO CONTABILIDAD VS. AUXILIAR",
                       f"CUENTAS {cta_ini} A {cta_fin} · DEL {_texto_fecha(fecha_ini)} AL {_texto_fecha(r.fecha_fin)}",
                       columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("SISTEMA", 9), ("TIPO DOC.", 10)
    rep.grupos_columnas = [(1, 2, "REGISTRADO EN CONTABILIDAD"), (3, 4, "REGISTRADO EN AUXILIAR"),
                           (5, 6, "DIFERENCIA (CONTA - AUX.)")]
    return rep
