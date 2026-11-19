"""
Contabilidad, menú 70 «Reportes varios» (formularios de conta\\forms de VFP):

  con_detalle_aplicacion_a_docs   Detalle de aplicación a documentos: cada documento con lo que aplica o le aplican
  con_detalle_poliza_de_docs      Detalle de póliza de documentos: cada documento con sus partidas contables
  con_cuadre_impuestos            Cuadre de impuestos: impuesto del documento contra lo registrado en la póliza

VFP hacía una o dos consultas por documento (aplicaciones, póliza, documento que amarra); aquí cada
dato se pide una vez para todo el reporte, en lotes de llaves. `con_detalle_poliza_de_docs_td` está en
vfmenuc pero su formulario no existe en las copias de VFP revisadas.
"""
from collections import defaultdict
from datetime import date

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import ErrorReporte
from .modelo import Columna, Fila, Reporte
from .reportes_chq import _Chq, _concepto, _f, _lotes, _num, _sin_datos, _t, _txt_fecha


def _filtros(r, p, alias="t", sistema_aux=False):
    """
    Condiciones comunes de los dos «detalle de documentos»: sistema, cuenta de caja / bancos, fechas,
    rangos de tipo, serie, directorio y número, clasificaciones y moneda (solo si no es la local, como VFP).
    `sistema_aux`: «Incluir registros auxiliares», el sistema también por los tres primeros caracteres
    de filectacorriente.
    """
    partes, params = [f"{alias}.codempresa = %s", f"{alias}.fecmov BETWEEN %s AND %s"], \
        [p.codempresa, r.fecha_ini, r.fecha_fin]
    if p.codsistema_doc:
        if sistema_aux:
            partes.append(f"(UPPER(LEFT({alias}.filectacorriente, 3)) = %s OR {alias}.codsistema = %s)")
            params += [p.codsistema_doc.upper(), p.codsistema_doc]
        else:
            partes.append(f"{alias}.codsistema = %s")
            params.append(p.codsistema_doc)
    if p.codctachq:
        partes.append(f"{alias}.codctachq = %s")
        params.append(p.codctachq)
    for valor, sql in ((p.tipo_desde, "codtipo >= %s"), (p.tipo_hasta, "codtipo <= %s"),
                       (p.serie_desde, "serie >= %s"), (p.serie_hasta, "serie <= %s"),
                       (p.directorio_desde, "coddirectorio >= %s"), (p.directorio_hasta, "coddirectorio <= %s"),
                       (p.numero_desde, "numero >= %s"), (p.numero_hasta, "numero <= %s")):
        if valor not in (None, ""):
            partes.append(f"{alias}.{sql}")
            params.append(valor.strip() if isinstance(valor, str) else valor)
    if p.clasificaciones:
        partes.append(f"{alias}.codclasifica IN ({','.join(['%s'] * len(p.clasificaciones))})")
        params += list(p.clasificaciones)
    if p.codmoneda and p.codmoneda != r.moneda_local:
        partes.append(f"{alias}.codmoneda = %s")
        params.append(p.codmoneda)
    return " AND ".join(partes), params


def _leyenda(p, r):
    partes = [r.periodo()]
    if p.codsistema_doc:
        partes.append(f"SISTEMA {p.codsistema_doc}")
    if r.cuenta:
        partes.append(r.nombre_cuenta())
    return " · ".join(partes)


# ------------------------------------------------------------------ Detalle de aplicación a documentos

def _aplicaciones(keytras):
    """
    Documentos aplicados con cada documento, en los dos sentidos de transacciones_aplica (el documento
    como keytra_apl y como keytra), sin el propio documento. Una consulta por lote de llaves; VFP
    hacía dos por documento.
    """
    salida = defaultdict(list)
    for bloque in _lotes(keytras):
        marcas = ",".join(["%s"] * len(bloque))
        for f in consulta(
                f"""SELECT apl.keytra_apl AS origen, apl.montodocml, apl.difcambiario_ml, tran.*
                      FROM transacciones_aplica apl
                      JOIN transacciones tran ON tran.codempresa = apl.codempresa AND tran.keytra = apl.keytra
                     WHERE apl.keytra_apl IN ({marcas})
                    UNION ALL
                    SELECT apl.keytra AS origen, apl.montodocml, apl.difcambiario_ml, tran.*
                      FROM transacciones_aplica apl
                      JOIN transacciones tran ON tran.codempresa = apl.codempresa AND tran.keytra = apl.keytra_apl
                     WHERE apl.keytra IN ({marcas})""", (*bloque, *bloque)):
            if f["keytra"] != f["origen"]:
                salida[f["origen"]].append(f)
    return salida


def _aplicado_por_lado(codempresa, keytras):
    """Total aplicado a cada documento como keytra_apl (1) y como keytra (2): {keytra: [ap1, ap2]}."""
    salida = defaultdict(lambda: [None, None])
    for bloque in _lotes(keytras):
        marcas = ",".join(["%s"] * len(bloque))
        for lado, campo in ((0, "keytra_apl"), (1, "keytra")):
            for f in consulta(f"""SELECT {campo} AS llave, SUM(montodocml) AS aplicado FROM transacciones_aplica
                                   WHERE codempresa = %s AND {campo} IN ({marcas}) GROUP BY {campo}""",
                              (codempresa, *bloque)):
                salida[f["llave"]][lado] = _f(f["aplicado"])
    return salida


def detalle_aplicacion(p):
    """
    con_detalle_aplicacion_a_docs: por documento (en negrita), los documentos que aplica o que le
    aplican, con el valor aplicado y el saldo que va quedando. En rojo, el documento cuyas
    aplicaciones tienen otra clasificación.
    """
    r = _Chq(p, cuenta_obligatoria=False)
    where, params = _filtros(r, p)
    if p.solo_no_aplicados and p.codsistema_doc == "CHQ":     # solo los que afectan auxiliar (VFP)
        where += " AND t.cuenta_aux <> '' AND FIND_IN_SET(TRIM(t.usactacorriente), 'C,P')"
    if p.solo_incongruentes:
        # Documentos con alguna aplicación de otra clasificación, en cualquiera de los dos sentidos
        where += """ AND EXISTS (SELECT 1 FROM transacciones_aplica a
                                   JOIN transacciones t1 ON t1.codempresa = a.codempresa AND t1.keytra = a.keytra
                                   JOIN transacciones t2 ON t2.codempresa = a.codempresa AND t2.keytra = a.keytra_apl
                                  WHERE a.codempresa = t.codempresa AND (a.keytra = t.keytra OR a.keytra_apl = t.keytra)
                                    AND t1.codclasifica <> t2.codclasifica)"""
    docs = consulta(f"SELECT t.* FROM transacciones t WHERE {where} ORDER BY t.fecmov, t.numerodoc, t.keytra", params)
    if p.solo_no_aplicados:
        lados = _aplicado_por_lado(p.codempresa, [d["keytra"] for d in docs])

        def pendiente(d):
            valor = _f(d["valordocml"])
            return all(ap is None or round(valor - ap, 2) != 0 for ap in lados[d["keytra"]])
        docs = [d for d in docs if pendiente(d)]
    _sin_datos(docs)
    apls = {} if p.solo_no_aplicados else _aplicaciones([d["keytra"] for d in docs])

    cols = [Columna("NOMBRE", ancho=34, texto=True), Columna("CLASIFICACIÓN", ancho=14, texto=True),
            Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=9, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("MON", ancho=6, texto=True),
            Columna("VALOR DOC."), Columna("VALOR APLICADO"), Columna("SALDO"),
            Columna("VALOR DOC. APLICADO"), Columna("DIFERENCIAL CAMBIARIO"),
            Columna("SIS.", ancho=5, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True),
            Columna("CONCEPTO", ancho=50, texto=True)]
    n = len(cols)

    def renglon(d):
        v = [None] * n
        v[0], v[1], v[2], v[3] = _t(d["nombre"]), _t(d["codclasifica"]), _t(d["codtipo"]), _t(d["serie"])
        v[4], v[5], v[11], v[12], v[13] = _num(d["numero"]), _t(d["codmoneda"]), _t(d["codsistema"]), \
            _t(d["keytra"]), _concepto(d)
        return v

    filas, cabeceras, aplicados = [], [], []
    for d in docs:
        v = renglon(d)
        valor = _f(d["valordocml"])
        v[6], v[8], v[10] = valor, valor, _f(d["dcambiario"])
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                   nombre=_t(d["coddirectorio"]), valores=v, formulas={8: ("lineal", [(1, 6), (-1, 7)])})
        filas.append(cab)
        cabeceras.append(cab)
        if p.solo_no_aplicados:
            continue
        previa, saldo = cab, valor
        for a in apls.get(d["keytra"], []):
            w = renglon(a)
            monto = _f(a["montodocml"])
            saldo = round(saldo - monto, 2)
            w[7], w[8], w[9], w[10] = monto, saldo, _f(a["valordocml"]), _f(a["difcambiario_ml"])
            fila = Fila(tipo="cuenta", nivel=2, cuenta=_txt_fecha(a["fecmov"]), nombre=_t(a["coddirectorio"]),
                        valores=w, formulas={8: ("corrido", previa, 8, 6, 7)})
            filas.append(fila)
            aplicados.append(fila)
            previa = fila
            if _t(a["codclasifica"]) != _t(d["codclasifica"]):
                cab.alerta = True
        filas.append(Fila())
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n, rango=(filas[0], filas[-1]))
    tot.valores[6] = round(sum(f.valores[6] for f in cabeceras), 2)
    tot.valores[7] = round(sum(f.valores[7] for f in aplicados), 2)
    tot.valores[9] = round(sum(f.valores[9] for f in aplicados), 2)
    tot.valores[10] = round(sum((f.valores[10] or 0) for f in cabeceras + aplicados), 2)
    filas.append(tot)
    leyenda = [_leyenda(p, r)]
    if p.solo_no_aplicados:
        leyenda.append("SOLO DOCUMENTOS NO APLICADOS")
    if p.solo_incongruentes:
        leyenda.append("SOLO CLASIFICACIONES INCONGRUENTES")
    rep = r.reporte("DETALLE DE APLICACIÓN A DOCUMENTOS", " · ".join(leyenda), cols, filas, "Aplicaciones")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    rep.grupos_columnas = [(2, 4, "DOCUMENTO")]
    return rep


# ------------------------------------------------------------------ Detalle de póliza de documentos

def _polizas(codempresa, keytras):
    """Partidas contables de cada documento con el nombre de la cuenta en su periodo."""
    salida = defaultdict(list)
    for bloque in _lotes(keytras):
        marcas = ",".join(["%s"] * len(bloque))
        for f in consulta(
                f"""SELECT d.keytra, d.codcuenta, d.cargo, d.abono, d.keyref, d.keyaux, d.descripcioncont,
                           n.nomcta
                      FROM dettransacciones d
                      LEFT JOIN empnomenclatura n ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo
                           AND n.codcuenta = d.codcuenta
                     WHERE d.codempresa = %s AND d.keytra IN ({marcas})
                     ORDER BY d.keytra, d.iddettransacciones""", (codempresa, *bloque)):
            salida[f["keytra"]].append(f)
    return salida


def _por_llave(codempresa, keytras):
    salida = {}
    for bloque in _lotes(keytras):
        marcas = ",".join(["%s"] * len(bloque))
        for f in consulta(f"SELECT * FROM transacciones WHERE codempresa = %s AND keytra IN ({marcas})",
                          (codempresa, *bloque)):
            salida[f["keytra"]] = f
    return salida


def detalle_poliza(p):
    """
    con_detalle_poliza_de_docs: cada documento (en negrita, con el total de cargos y abonos de su
    póliza) seguido de sus partidas contables. En rojo, el documento contable sin póliza o con la
    póliza descuadrada. Los movimientos no contables muestran el documento que los generó (keyref).
    """
    r = _Chq(p, cuenta_obligatoria=False)
    where, params = _filtros(r, p, sistema_aux=p.incluir_regs_aux)
    where += " AND t.anulado = 0"
    if not p.incluye_no_contables:
        where += " AND t.usacnt = 1"
        if p.solo_desactualizados:
            where += " AND t.actualizadocnt = 0"
    docs = consulta(f"""SELECT t.* FROM transacciones t WHERE {where}
                        ORDER BY t.codsistema, t.codtipo, t.serie, t.numero, t.fecmov, t.keytra""", params)
    _sin_datos(docs)
    contables = [d["keytra"] for d in docs if d["usacnt"] == 1]
    polizas = _polizas(p.codempresa, contables)
    refs = _por_llave(p.codempresa, [d["keyref"] for d in docs if d["usacnt"] != 1 and _t(d["keyref"])])

    cols = [Columna("NOMBRE", ancho=34, texto=True), Columna("CLASIFICACIÓN", ancho=14, texto=True),
            Columna("CARGO"), Columna("ABONO"),
            Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=9, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("MON", ancho=6, texto=True),
            Columna("ACT.", ancho=5, texto=True), Columna("USA CNT", ancho=7, texto=True),
            Columna("VALOR DOC."), Columna("DIFERENCIAL CAMBIARIO"), Columna("SIS.", ancho=5, texto=True),
            Columna("LLAVE PRINCIPAL", ancho=26, texto=True), Columna("LLAVE REFERENCIA", ancho=26, texto=True),
            Columna("LLAVE AUXILIAR", ancho=26, texto=True), Columna("CONCEPTO", ancho=50, texto=True),
            Columna("FECHA", "MOV. QUE GENERÓ", ancho=11, texto=True),
            Columna("DIRECTORIO", "MOV. QUE GENERÓ", ancho=14, texto=True),
            Columna("CLASIFICACIÓN", "MOV. QUE GENERÓ", ancho=14, texto=True),
            Columna("TIPO", "MOV. QUE GENERÓ", ancho=7, texto=True),
            Columna("SERIE", "MOV. QUE GENERÓ", ancho=9, texto=True),
            Columna("NÚMERO", "MOV. QUE GENERÓ", ancho=12, texto=True),
            Columna("SIS.", "MOV. QUE GENERÓ", ancho=5, texto=True),
            Columna("LLAVE PRINCIPAL", "MOV. QUE GENERÓ", ancho=26, texto=True)]
    n = len(cols)
    filas, cabeceras, partidas, hay_no_contables = [], [], [], False
    for d in docs:
        v = [None] * n
        v[0], v[1], v[4], v[5], v[6] = _t(d["nombre"]), _t(d["codclasifica"]), _t(d["codtipo"]), _t(d["serie"]), \
            _num(d["numero"])
        v[7], v[8], v[9] = _t(d["codmoneda"]), "Sí" if d["actualizadocnt"] == 1 else "No", \
            "Sí" if d["usacnt"] == 1 else "No"
        v[10], v[11], v[12] = _f(d["valordocml"]), _f(d["dcambiario"]), _t(d["codsistema"])
        v[13], v[14], v[15], v[16] = _t(d["keytra"]), _t(d["keyref"]), _t(d["keyaux"]), _concepto(d)
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                   nombre=_t(d["coddirectorio"]), valores=v)
        filas.append(cab)
        cabeceras.append(cab)
        if d["usacnt"] == 1:
            lineas = []
            for x in polizas.get(d["keytra"], []):
                w = [None] * n
                w[0], w[2], w[3] = _t(x["nomcta"]) or "*** CUENTA NO EXISTE EN LA NOMENCLATURA ***", \
                    _f(x["cargo"]), _f(x["abono"])
                w[13], w[14], w[15], w[16] = _t(x["keytra"]), _t(x["keyref"]), _t(x["keyaux"]), \
                    " ".join(_t(x["descripcioncont"])[:60].split())
                linea = Fila(tipo="cuenta", nivel=2, nombre=_t(x["codcuenta"]), valores=w)
                lineas.append(linea)
            filas += lineas
            partidas += lineas
            cargo = round(sum(x.valores[2] for x in lineas), 2)
            abono = round(sum(x.valores[3] for x in lineas), 2)
            v[2], v[3] = cargo, abono
            if lineas:
                cab.formulas = {2: ("subtotal", lineas[0], lineas[-1]), 3: ("subtotal", lineas[0], lineas[-1])}
            cab.alerta = not lineas or cargo != abono
            filas.append(Fila())
        elif _t(d["keyref"]):
            hay_no_contables = True
            ref = refs.get(d["keyref"])
            if ref:
                v[17], v[18], v[19], v[20] = _txt_fecha(ref["fecmov"]), _t(ref["coddirectorio"]), \
                    _t(ref["codclasifica"]), _t(ref["codtipo"])
                v[21], v[22], v[23], v[24] = _t(ref["serie"]), _num(ref["numero"]), _t(ref["codsistema"]), \
                    _t(ref["keytra"])
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n, rango=(filas[0], filas[-1]))
    tot.valores[2] = round(sum(f.valores[2] for f in partidas), 2)
    tot.valores[3] = round(sum(f.valores[3] for f in partidas), 2)
    tot.valores[10] = round(sum(f.valores[10] for f in cabeceras), 2)
    tot.valores[11] = round(sum(f.valores[11] for f in cabeceras), 2)
    filas.append(tot)
    leyenda = [_leyenda(p, r)]
    if p.incluye_no_contables:
        leyenda.append("INCLUYE MOVIMIENTOS NO CONTABLES")
    elif p.solo_desactualizados:
        leyenda.append("SOLO MOVIMIENTOS DESACTUALIZADOS")
    rep = r.reporte("DETALLE DE PÓLIZA DE DOCUMENTOS", " · ".join(leyenda), cols, filas, "Pólizas")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO / CUENTA", 16)
    rep.grupos_columnas = [(4, 6, "DOCUMENTO"), (17, 24, "DATOS DEL MOVIMIENTO QUE GENERÓ LA TRANSACCIÓN NO CONTABLE")]
    ocultas = set()
    if not p.mostrar_llaves:
        ocultas |= {13, 14, 15}
    if not hay_no_contables:
        ocultas |= set(range(17, 25))
    rep.columnas_ocultas = ocultas
    return rep


# ------------------------------------------------------------------ Cuadre de impuestos

# impuesto -> (campo de transacciones, campo de empperiodos con la cuenta, sistemas por defecto)
IMPUESTOS = {
    "iva_pagar": ("iva", "ivadb", ("CXC", "FAC")),
    "iva_cobrar": ("iva", "ivacr", ("CXP",)),
    "timbre_prensa": ("timbre_prensa", "timbreprensa", ()),
    "combustible": ("iva_galon", "ivacombustible", ()),
}
ETIQUETAS_IMPUESTO = {"iva_pagar": "IVA por pagar", "iva_cobrar": "IVA por cobrar",
                      "timbre_prensa": "Timbre de prensa", "combustible": "IVA combustible"}


def _nombre_impuesto(codempresa, impuesto):
    g = consulta_uno("SELECT nomiva, nomtimbreprensa FROM empparametrosgenerales WHERE codempresa = %s",
                     (codempresa,)) or {}
    iva = _t(g.get("nomiva")) or "IVA"
    return {"iva_pagar": f"{iva} POR PAGAR", "iva_cobrar": f"{iva} POR COBRAR",
            "timbre_prensa": _t(g.get("nomtimbreprensa")) or "TIMBRE DE PRENSA",
            "combustible": "IVA COMBUSTIBLE"}[impuesto]


def cuadre_impuestos(p):
    """
    con_cuadre_impuestos: documentos con el impuesto elegido en los meses pedidos, frente a lo que
    registró la póliza en la cuenta del impuesto del periodo (empperiodos), con la diferencia y si
    está contabilizado.
    """
    emp = ctx.empresa(p.codempresa)
    impuesto = p.impuesto or "iva_pagar"
    campo, campo_cuenta, sistemas = IMPUESTOS[impuesto]
    per = ctx.periodo(p.codempresa, p.anio, p.mes)
    cuenta = _t((consulta_uno(f"SELECT {campo_cuenta} AS cuenta FROM empperiodos WHERE codempresa = %s AND codperiodo = %s",
                              (p.codempresa, per.codperiodo)) or {}).get("cuenta"))
    if not cuenta:
        raise ErrorReporte(f"El periodo {per.codperiodo} no tiene configurada la cuenta de {ETIQUETAS_IMPUESTO[impuesto]}.")
    meses = max(1, min(p.meses or 1, 12))
    mes_ini = p.anio * 12 + p.mes - 1 - (meses - 1)
    fecha_ini = date(mes_ini // 12, mes_ini % 12 + 1, 1)
    sig = p.anio * 12 + p.mes
    fecha_fin = date.fromordinal(date(sig // 12, sig % 12 + 1, 1).toordinal() - 1)

    if p.codsistema_doc:
        sistemas = (p.codsistema_doc,)
    filtro_sis = f" AND t.codsistema IN ({','.join(['%s'] * len(sistemas))})" if sistemas else ""
    agencia = ""
    if p.solo_agencia and consulta("SHOW TABLES LIKE 'liq_quedan'"):
        agencia = """JOIN liq_quedan liq ON liq.codempresa = t.codempresa AND liq.keytra_quedan = t.keyref
                          AND liq.agencia = 1"""
    # Las partidas de la cuenta del impuesto, sumadas por documento (VFP traía una fila por partida)
    docs = consulta(
        f"""
        SELECT t.keytra, t.fecmov, t.coddirectorio, t.nombre, t.codclasifica, t.codtipo, t.serie, t.numero,
               t.codmoneda, t.valordocml, t.{campo} AS impuesto, t.usacnt, t.actualizadocnt, t.codsistema,
               t.cuenta_aux, t.keyref, t.keyaux, t.descontable, t.anulado,
               IFNULL(x.lineas, 0) AS lineas, IFNULL(x.cargo, 0) AS cargo, IFNULL(x.abono, 0) AS abono,
               x.dir_poliza, IFNULL(g.razonsocial, '') AS nombre_poliza, x.clasif_poliza, x.keyref_poliza,
               x.keyaux_poliza, x.concepto_poliza
          FROM transacciones t
          LEFT JOIN (SELECT d.keytra, COUNT(*) AS lineas, SUM(d.cargo) AS cargo, SUM(d.abono) AS abono,
                            MIN(d.codgrupo) AS codgrupo, MIN(d.coddirectorio) AS dir_poliza,
                            MIN(d.codclasifica) AS clasif_poliza, MIN(d.keyref) AS keyref_poliza,
                            MIN(d.keyaux) AS keyaux_poliza, MIN(d.descripcioncont) AS concepto_poliza
                       FROM dettransacciones d
                       JOIN transacciones t2 ON t2.codempresa = d.codempresa AND t2.keytra = d.keytra
                      WHERE d.codempresa = %s AND d.codcuenta = %s AND t2.fecmov BETWEEN %s AND %s
                      GROUP BY d.keytra) x ON x.keytra = t.keytra
          LEFT JOIN grudirectorio g ON g.codgrupo = x.codgrupo AND g.coddirectorio = x.dir_poliza
          {agencia}
         WHERE t.codempresa = %s AND t.fecmov BETWEEN %s AND %s AND t.{campo} > 0 AND t.anulado = 0{filtro_sis}
        """, (p.codempresa, cuenta, fecha_ini, fecha_fin, p.codempresa, fecha_ini, fecha_fin, *sistemas))
    if p.solo_no_contabilizados:
        docs = [d for d in docs if not d["lineas"]]
    _sin_datos(docs)

    def dato(d, poliza, propio):
        return _t(d[poliza]) or _t(d[propio])

    claves = {
        1: lambda d: (d["fecmov"], _t(d["codtipo"]), _t(d["serie"]), d["numero"] or 0),
        2: lambda d: (dato(d, "dir_poliza", "coddirectorio"), d["fecmov"], _t(d["codtipo"]), _t(d["serie"]), d["numero"] or 0),
        3: lambda d: (dato(d, "nombre_poliza", "nombre"), d["fecmov"], _t(d["codtipo"]), _t(d["serie"]), d["numero"] or 0),
        4: lambda d: (_t(d["codtipo"]), _t(d["serie"]), d["numero"] or 0, d["fecmov"]),
    }
    docs.sort(key=claves.get(p.orden or 1, claves[1]))

    cols = [Columna("NOMBRE", ancho=34, texto=True), Columna("CLASIFICACIÓN", ancho=14, texto=True),
            Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=9, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("MON", ancho=6, texto=True),
            Columna("VALOR DOC."), Columna("CARGO"), Columna("ABONO"), Columna("VALOR IMPUESTO"),
            Columna("DIFERENCIA CON PÓLIZA"), Columna("CONTABILIZADO", ancho=8, texto=True),
            Columna("CNT.", ancho=5, texto=True), Columna("ACT.", ancho=5, texto=True),
            Columna("SIS.", ancho=5, texto=True), Columna("CUENTA AUX.", ancho=16, texto=True),
            Columna("LLAVE PRINCIPAL", ancho=26, texto=True), Columna("LLAVE REF.", ancho=26, texto=True),
            Columna("LLAVE AUX.", ancho=26, texto=True), Columna("CONCEPTO", ancho=50, texto=True)]
    n = len(cols)
    filas = []
    for d in docs:
        imp, cargo, abono = _f(d["impuesto"]), _f(d["cargo"]), _f(d["abono"])
        registrado = imp - (abono if abono else cargo) if d["lineas"] else imp
        v = [None] * n
        v[0], v[1] = dato(d, "nombre_poliza", "nombre"), dato(d, "clasif_poliza", "codclasifica")
        v[2], v[3], v[4], v[5] = _t(d["codtipo"]), _t(d["serie"]), _num(d["numero"]), _t(d["codmoneda"])
        v[6], v[9], v[10] = _f(d["valordocml"]), imp, round(registrado, 2)
        if cargo:
            v[7] = cargo
        if abono:
            v[8] = abono
        v[11] = "SI" if d["lineas"] else "NO"
        v[12], v[13] = "SI" if d["usacnt"] == 1 else "NO", "SI" if d["actualizadocnt"] == 1 else "NO"
        v[14], v[15], v[16] = _t(d["codsistema"]), _t(d["cuenta_aux"]), _t(d["keytra"])
        v[17], v[18] = dato(d, "keyref_poliza", "keyref"), dato(d, "keyaux_poliza", "keyaux")
        v[19] = " ".join(_t(d["concepto_poliza"]).split()) or _concepto(d, 60)
        filas.append(Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                          nombre=dato(d, "dir_poliza", "coddirectorio"), valores=v,
                          alerta=round(registrado, 2) != 0))
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n, rango=(filas[0], filas[-1]))
    for k in (6, 7, 8, 9, 10):
        tot.valores[k] = round(sum(f.valores[k] or 0 for f in filas), 2)
    filas.append(tot)
    titulo = (p.nombre or "CUADRE DE IMPUESTOS").strip()
    sub = f"{_nombre_impuesto(p.codempresa, impuesto)} · CUENTA {cuenta} · " \
          f"PERIODO: {_txt_fecha(fecha_ini)} - {_txt_fecha(fecha_fin)}"
    if sistemas:
        sub += f" · SISTEMA {', '.join(sistemas)}"
    if p.solo_no_contabilizados:
        sub += " · SOLO NO CONTABILIZADOS"
    rep = Reporte(titulo=titulo, subtitulo=sub, empresa=emp, columnas=cols, filas=filas,
                  nombre_archivo=f"{titulo} {p.codempresa} {fecha_fin:%Y-%m}.xlsx".replace("/", "-"),
                  hoja="Cuadre")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("CÓDIGO", 14)
    rep.grupos_columnas = [(2, 4, "DOCUMENTO")]
    if not p.mostrar_llaves:
        rep.columnas_ocultas = {16, 17, 18}
    return rep
