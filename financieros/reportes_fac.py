"""
Reportes de Facturación (menús 50 y 55 del sistema FAC de VFP).

  con_empdoctos_correl_fac            Correlativo de facturación (retenciones calculadas, pagos, retenciones aplicadas)
  con_empdoctos_correl_fac_vendedor   Ídem con cliente base, vendedor y comisión
  con_empdoctos_correl_fac_detallado  Ídem con las líneas de cada factura
  con_empdoctos_correl_fac_gtl        Correlativo [ODS] (gastos por cuenta ajena; detalle por línea)
  con_facturas_retencion_isr          Facturas con detalle de retención de ISR (5 %)
  con_fac_mensual_por_cli             Facturación mensual por cliente (ene..mes)
  con_fac_anual_comparativo_cli       Facturación anual: año actual vs. anterior por cliente
  con_facturacion_concepto_x_cuenta   Facturación por cuenta contable del concepto (una columna por cuenta)
  con_consecutivo_facturacion         Consecutivo de facturación [Agencia] (producción / arte / medios)
  con_facturacion_medios_produccion   Cuadros finales: medios, producción, arte y otros por cliente y marca

VFP hacía, por cada factura, de 1 a 4 consultas (pagos, retenciones ISR, retenciones IVA, centro
de renta), además de 2 llamadas a _calcula_retenciones (2-3 consultas cada una) y, en el de
vendedor, 2 consultas de la tabla de comisiones. Aquí cada dato se pide una sola vez para todo el
reporte y se calcula en memoria.
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import ErrorReporte, fin_de_mes
from .modelo import Columna, Fila, Reporte

MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")
# Tipos de documento con numeración propia (no se marca el salto en el correlativo)
SIN_CORRELATIVO = ("FACT", "FCAM", "NCRE", "NDEB", "NABN")
LOTE = 500


def _f(v):
    return float(v or 0)


def _t(v):
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def _fecha(v):
    return v if isinstance(v, date) else None


def _txt_fecha(v):
    return v.strftime("%d/%m/%Y") if isinstance(v, date) else ""


def _num(v):
    try:
        return str(int(v or 0))
    except (TypeError, ValueError):
        return _t(v)


def _lotes(llaves):
    llaves = sorted({k for k in llaves if k})
    for i in range(0, len(llaves), LOTE):
        yield llaves[i:i + LOTE]


def _existe_tabla(nombre):
    return bool(consulta("SHOW TABLES LIKE %s", (nombre,)))


class _Fac:
    """Empresa, filtros comunes del formulario y salida."""

    def __init__(self, p):
        self.p = p
        self.emp = ctx.empresa(p.codempresa)
        e = consulta_uno("SELECT codpais, codgrupo, codmoneda, esagenteretiva FROM empresas WHERE codempresa = %s",
                         (p.codempresa,)) or {}
        self.codpais, self.codgrupo = _t(e.get("codpais")), _t(e.get("codgrupo"))
        self.moneda_local = _t(e.get("codmoneda"))
        self.agente_iva = int(e.get("esagenteretiva") or 0)
        self.fecha_fin = p.fecha_fin or date.today()
        self.fecha_ini = p.fecha_ini or self.fecha_fin.replace(day=1)
        self.sistema = "FAC"

    def filtros(self, alias="t", con_fmb=True, moneda="siempre", docref=True):
        """WHERE comunes: sistema (+FMB), fechas, tipo, directorio, moneda y documento de referencia."""
        p, a = self.p, alias
        partes = [f"{a}.codempresa = %s", f"{a}.fecmov BETWEEN %s AND %s"]
        params = [p.codempresa, self.fecha_ini, self.fecha_fin]
        partes.append(f"{a}.codsistema IN ('FAC', 'FMB')" if con_fmb else f"{a}.codsistema = 'FAC'")
        for valor, sql in ((p.tipo_desde, f"{a}.codtipo >= %s"), (p.tipo_hasta, f"{a}.codtipo <= %s"),
                           (p.directorio_desde, f"{a}.coddirectorio >= %s"),
                           (p.directorio_hasta, f"{a}.coddirectorio <= %s")):
            if valor:
                partes.append(sql)
                params.append(valor)
        if p.codmoneda and (moneda == "siempre" or p.codmoneda != self.moneda_local):
            partes.append(f"{a}.codmoneda = %s")
            params.append(p.codmoneda)
        if docref and p.docref:
            partes.append(f"{a}.docref = %s")
            params.append(p.docref)
        return " AND ".join(partes), params

    def filtro_estado(self, alias="t"):
        """Todas / pagadas / pendientes: lo aplicado por la factura en transacciones_aplica (como VFP)."""
        sub = f"IFNULL((SELECT SUM(apl.montodocml) FROM transacciones_aplica apl WHERE apl.keytra = {alias}.keytra), 0)"
        if self.p.estado_fac == "pagadas":
            return f" AND {sub} > 0 AND {alias}.anulado = 0"
        if self.p.estado_fac == "pendientes":
            return f" AND {sub} = 0 AND {alias}.anulado = 0"
        return ""

    def orden(self, alias="t"):
        a = alias
        if self.p.orden_fac == 2:
            return f"{a}.codsistema, {a}.fecmov, {a}.codtipo, {a}.serie, {a}.numero"
        if self.p.orden_fac == 3:
            return f"{a}.codsistema, {a}.fecmov DESC, {a}.codtipo, {a}.serie, {a}.numero"
        return f"{a}.codsistema, {a}.codtipo, {a}.serie, {a}.numero, {a}.fecmov"

    def periodo(self):
        return f"PERIODO: {_txt_fecha(self.fecha_ini)} - {_txt_fecha(self.fecha_fin)}"

    def leyenda_filtros(self):
        p, partes = self.p, []
        if p.tipo_desde or p.tipo_hasta:
            partes.append(f"TIPO: {p.tipo_desde or '…'} - {p.tipo_hasta or '…'}")
        if p.directorio_desde or p.directorio_hasta:
            partes.append(f"DIRECTORIO: {p.directorio_desde or '…'} - {p.directorio_hasta or '…'}")
        if p.codmoneda:
            partes.append(f"MONEDA: {p.codmoneda}")
        if p.docref:
            partes.append(f"DOC. REF.: {p.docref}")
        return " · ".join(partes)

    def reporte(self, titulo_def, subtitulo, columnas, filas, hoja="", con_filtros=True):
        titulo = (self.p.nombre or titulo_def).strip()
        filtros = self.leyenda_filtros() if con_filtros else ""
        archivo = re.sub(r"[^\w\- ]", "", f"{titulo} {self.emp.codempresa} {self.fecha_fin:%Y-%m-%d}")
        return Reporte(titulo=titulo, subtitulo=subtitulo + (f" · {filtros}" if filtros else ""), empresa=self.emp,
                       columnas=columnas, filas=filas, nombre_archivo=archivo.strip() + ".xlsx", hoja=hoja,
                       fecha_hora=datetime.now() if self.p.fecha_hora else None)

    def documentos(self, extra_select="", extra_join=""):
        where, params = self.filtros()
        filas = consulta(
            f"""
            SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir, g.dias_cr_cxc, g.usaexencioniva, g.codaca{extra_select}
              FROM transacciones t
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
              {extra_join}
             WHERE {where}{self.filtro_estado()}
             ORDER BY {self.orden()}
            """, params)
        if not filas:
            raise ErrorReporte("No hay documentos de facturación con los parámetros indicados.")
        return filas


# ------------------------------------------------------------------ retenciones (_calcula_retenciones.prg)

class Retenciones:
    """
    Equivalente de sistema\\prgs\\_calcula_retenciones.prg en modo consulta: tablas de retención,
    datos del directorio y de la empresa leídos una vez. Donde VFP mostraba un MESSAGEBOX (sin
    código de retención, montos negativos) devuelve 0.
    """

    def __init__(self, r, directorios):
        self.r = r
        self.dirs = {}
        for bloque in _lotes(directorios):
            for d in consulta(
                    f"""SELECT coddirectorio, esagenteretiva, pagaretiva, codretiva, esagenteretisr, pagaretisr, codretisr
                          FROM grudirectorio WHERE codgrupo = %s AND coddirectorio IN ({','.join(['%s'] * len(bloque))})""",
                    (r.codgrupo, *bloque)):
                self.dirs[_t(d["coddirectorio"])] = d
        self.tabla = {(_t(c["tiporet"]), _t(c["codret"]).upper()): c for c in consulta(
            "SELECT * FROM comretenciones WHERE codpais = %s", (r.codpais,))}
        self.rangos = defaultdict(list)
        for c in consulta("SELECT * FROM comretenciones_tabla WHERE codpais = %s", (r.codpais,)):
            self.rangos[(_t(c["tiporet"]), _t(c["codret"]).upper())].append(c)
        self.emp = consulta_uno("SELECT * FROM empresas WHERE codempresa = %s", (r.p.codempresa,)) or {}
        self.automatica = {_t(f["parametro"]): _t(f["valor"]) for f in consulta(
            "SELECT parametro, valor FROM parametros WHERE codempresa = %s AND parametro LIKE %s",
            (r.p.codempresa, "gen_genera_automaticamente_retencion_%"))}

    def calcula(self, coddirectorio, tiporet, total, neto, iva, agente_retenedor=0):
        if neto < 0 or total < 0 or iva < 0 or not coddirectorio:
            return 0.0
        if not agente_retenedor:
            d = self.dirs.get(_t(coddirectorio))
            if not d:
                return 0.0
            if tiporet == "IVA":
                if d["esagenteretiva"] == 1 or d["pagaretiva"] == 0:
                    return 0.0
                codret = _t(d["codretiva"]).upper()
            elif tiporet == "ISR":
                if d["esagenteretisr"] == 1 or d["pagaretisr"] == 0:
                    return 0.0
                codret = _t(d["codretisr"]).upper()
            else:
                codret = "100"
        else:
            d = self.dirs.get(_t(coddirectorio))
            if d and d["esagenteretiva"] == 1:
                return 0.0
            e = self.emp
            if tiporet == "IVA":
                if not e.get("esagenteretiva") or \
                        self.automatica.get("gen_genera_automaticamente_retencion_iva_emp_agente_retenedor") == "0":
                    return 0.0
                codret = _t(e.get("codretiva_normal")).upper()
            elif tiporet == "ISR":
                return 0.0              # VFP deja el código vacío: siempre termina en 0
            else:
                if not e.get("usaexencioniva"):
                    return 0.0
                codret = "100"
        if not codret:
            return 0.0
        cab = self.tabla.get((tiporet, codret))
        if cab is None:
            return 0.0
        if not cab.get("usa_tabla_detalle"):
            minimo = _f(cab["rango_minimo"])
            if minimo > 0 and (total if cab["limit_inc_impto"] == 1 else neto) < minimo:
                return 0.0
            pct = _f(cab["porcentaje"]) / 100
            if tiporet == "ISR":
                return round((total if cab["calcula_inc_impto"] == 1 else neto) * pct, 2)
            return round(iva * pct, 2)
        valor = 0.0
        for c in self.rangos.get((tiporet, codret), []):
            if (total if c["limit_inc_impto"] == 1 else neto) < _f(c["rango_minimo"]):
                continue
            base = total if c["calcula_inc_impto"] == 1 else neto
            maximo = min(_f(c["calcula_maximo"]), base) if _f(c["calcula_maximo"]) > 0 else base
            tramo = maximo - _f(c["calcula_minimo"])
            if tramo > 0:
                valor += round(tramo * _f(c["porcentaje"]) / 100, 2)
        return round(valor, 2)


def _aplicaciones(keytras):
    """
    Documentos relacionados con cada factura en transacciones_aplica, en ambos sentidos:
    «recibidos» (aplicados A la factura) y «dados» (a los que la factura se aplica).
    """
    salida = defaultdict(list)
    for bloque in _lotes(keytras):
        marcas = ",".join(["%s"] * len(bloque))
        for f in consulta(
                f"""
                SELECT apl.keytra_apl AS doc, 'R' AS lado, tra.codtipo, tra.serie, tra.numero, tra.fecmov,
                       tra.valordocml, tra.valorme, tra.codtipodoctos
                  FROM transacciones_aplica apl JOIN transacciones tra ON tra.keytra = apl.keytra
                 WHERE apl.keytra_apl IN ({marcas})
                UNION ALL
                SELECT apl.keytra AS doc, 'D' AS lado, tra.codtipo, tra.serie, tra.numero, tra.fecmov,
                       tra.valordocml, tra.valorme, tra.codtipodoctos
                  FROM transacciones_aplica apl JOIN transacciones tra ON tra.keytra = apl.keytra_apl
                 WHERE apl.keytra IN ({marcas})
                """, (*bloque, *bloque)):
            salida[f["doc"]].append(f)
    return salida


def _resume(apls, condicion):
    docs = [a for a in apls if condicion(a["lado"], _t(a["codtipodoctos"]).upper())]
    if not docs:
        return None
    docs.sort(key=lambda a: (a["fecmov"] or date.min, a["numero"] or 0))
    return {
        "documentos": ", ".join(f"{_t(a['codtipo'])} {_t(a['serie'])} {_num(a['numero'])}" for a in docs),
        "fechas": ", ".join(dict.fromkeys(_txt_fecha(a["fecmov"]) for a in docs)),
        "ml": round(sum(_f(a["valordocml"]) for a in docs), 2),
        "me": round(sum(_f(a["valorme"]) for a in docs), 2),
    }


def _vencimiento(f):
    v = _fecha(f.get("fechavenc"))
    if v:
        return v
    dias = int(f.get("diascr") or 0) or int(_f(f.get("dias_cr_cxc")))
    return f["fecmov"] + timedelta(days=dias) if _fecha(f.get("fecmov")) else None


def _marca_saltos(filas_doc, docs):
    """Rojo cuando el número no sigue al anterior (misma serie) en tipos con numeración correlativa."""
    previo = None
    for fila, d in zip(filas_doc, docs):
        actual = (_t(d["serie"]), int(_f(d["numero"])))
        if previo is not None and actual != (previo[0], previo[1] + 1) and \
                _t(d.get("codtipodoctos")).upper() not in SIN_CORRELATIVO:
            fila.alerta = True
        previo = actual


def _agrupa_por_tipo(docs):
    grupos = defaultdict(list)
    for d in docs:
        grupos[(_t(d["codsistema"]), _t(d["codtipo"]))].append(d)
    return grupos.items()


def _bloques(r, docs, columnas, arma_fila, cols_total, nombre_total=None):
    """Filas por sistema / tipo de documento con subtotal por tipo y total general."""
    n = len(columnas)
    filas, subtotales = [], []
    orden = list(dict.fromkeys((_t(d["codsistema"]), _t(d["codtipo"])) for d in docs))
    grupos = dict(_agrupa_por_tipo(docs))
    for clave in orden:
        grupo = grupos[clave]
        filas.append(Fila(tipo="grupo", cuenta=f"[{clave[0]}] {clave[1]}"))
        bloque = []
        for d in grupo:
            bloque += arma_fila(d)
        _marca_saltos([b for b in bloque if b.codigo], [d for d in grupo])
        filas += bloque
        sub = Fila(tipo="total", negrita=True, nombre=f"TOTAL {clave[1]}", rango=(bloque[0], bloque[-1]),
                   valores=[None] * n)
        for k in cols_total:
            sub.valores[k] = round(sum(b.valores[k] for b in bloque if b.codigo and isinstance(b.valores[k], float)), 2)
        filas += [sub, Fila()]
        subtotales.append(sub)
    total = Fila(tipo="total", negrita=True, nombre=nombre_total or "TOTALES", valores=[None] * n,
                 refs=[(1, s) for s in subtotales])
    for k in cols_total:
        total.valores[k] = round(sum(s.valores[k] or 0 for s in subtotales), 2)
    return filas + [total]


def _montos_dh(d):
    """Cargo / abono en moneda local y extranjera según deha (vacío si es cero)."""
    debe = _t(d["deha"]) == "D"
    ml, me = _f(d["valordocml"]), _f(d["valorme"])
    return (ml or None) if debe else None, None if debe else (ml or None), \
        (me or None) if debe else None, None if debe else (me or None)


def _sig(d):
    return 1 if _t(d["deha"]) == "D" else -1


def _concepto(d, largo=300):
    return "*** A N U L A D O ***" if d.get("anulado") == 1 else _t(d.get("descontable"))[:largo]


# ------------------------------------------------------------------ Correlativo de facturación

def correlativo(p):
    """con_empdoctos_correl_fac: cada documento con retenciones calculadas, pagos y retenciones aplicadas."""
    r = _Fac(p)
    docs = r.documentos()
    ret = Retenciones(r, [_t(d["coddirectorio"]) for d in docs])
    apl = _aplicaciones([d["keytra"] for d in docs])
    centros = {}
    if _existe_tabla("cdr_rentas_contratos_facturas"):
        for bloque in _lotes([d["keytra"] for d in docs]):
            for c in consulta(
                    f"""SELECT DISTINCT fac.keytra, contra.codcentro
                          FROM cdr_rentas_contratos_facturas fac
                          JOIN transacciones tra ON tra.codempresa = fac.codempresa AND tra.keytra = fac.keytra
                          JOIN cdr_rentas_contratos contra ON contra.codempresa = fac.codempresa
                               AND contra.codgrupo = fac.codgrupo AND contra.coddirectorio = fac.coddirectorio
                               AND contra.no_contrato = fac.no_contrato
                         WHERE fac.keytra IN ({','.join(['%s'] * len(bloque))}) AND tra.anulado = 0""", bloque):
                centros.setdefault(c["keytra"], _t(c["codcentro"]))

    cols = [Columna("DIRECTORIO", ancho=14, texto=True), Columna("NOMBRE", ancho=36, texto=True),
            Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=10, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True),
            Columna("DOC. REF.", ancho=14, texto=True), Columna("MON", ancho=6, texto=True),
            Columna("CARGO", "M. LOCAL"), Columna("ABONO", "M. LOCAL"), Columna("CARGO", "M. EXTRANJERA"),
            Columna("ABONO", "M. EXTRANJERA"), Columna("TASA CAMBIO", ancho=10), Columna("CNT", ancho=5, texto=True),
            Columna("ACT. CNT", ancho=5, texto=True), Columna("ORDEN COMPRA", ancho=12, texto=True),
            Columna("LOTE", ancho=8, texto=True), Columna("SUCURSAL", ancho=8, texto=True),
            Columna("VALOR NETO"), Columna("IVA"), Columna("SIS.", ancho=5, texto=True), Columna("TOTAL"),
            Columna("RET. IVA"), Columna("RET. ISR"), Columna("EXENCIÓN IVA"), Columna("NETO A PAGAR"),
            Columna("CONCEPTO", ancho=50, texto=True),
            Columna("DOCUMENTO", "PAGOS", ancho=30, texto=True), Columna("FECHA", "PAGOS", ancho=12, texto=True),
            Columna("M.L.", "PAGOS"), Columna("M.E.", "PAGOS"),
            Columna("I.S.R.", "RETENCIONES", ancho=24, texto=True), Columna("VALOR", "RET. ISR"),
            Columna("I.V.A.", "RETENCIONES", ancho=24, texto=True), Columna("VALOR", "RET. IVA"),
            Columna("CENTRO", ancho=12, texto=True)]
    n = len(cols)

    def fila(d):
        v = [None] * n
        s = _sig(d)
        ml_d, ml_h, me_d, me_h = _montos_dh(d)
        valordoc, iva = _f(d["valordocml"]), _f(d["iva"])
        v[0], v[1], v[2], v[3], v[4] = _t(d["coddirectorio"]), _t(d["nombre_dir"]), _t(d["codtipo"]), \
            _t(d["serie"]), _num(d["numero"])
        v[5], v[6], v[7] = _t(d["keytra"]), _t(d["docref"]), _t(d["codmoneda"])
        v[8], v[9], v[10], v[11] = ml_d, ml_h, me_d, me_h
        v[12] = _f(d["tasac"])
        v[13], v[14] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
        v[15], v[16], v[17] = _t(d.get("ocompra")), _t(d.get("lote")), _t(d.get("sucursal"))
        v[18], v[19] = round((valordoc - iva) * s, 2), round(iva * s, 2)
        v[20], v[21] = _t(d["codsistema"]), round(valordoc * s, 2)
        neto = valordoc - iva - _f(d["timbre_prensa"])
        v[22] = ret.calcula(_t(d["coddirectorio"]), "IVA", valordoc, neto, iva, r.agente_iva)
        v[23] = round(v[18] * 0.05, 2) if v[18] >= 2500 else 0.0
        v[24] = round(iva, 2) if d.get("usaexencioniva") == 1 else 0.0
        v[25] = round(v[21] - v[22] - v[23] - v[24], 2)
        v[26] = _concepto(d, 200)
        ap = apl.get(d["keytra"], [])
        pagos = _resume(ap, lambda lado, tipo: (lado == "R" and tipo not in ("RET", "RI")) or
                        (lado == "D" and tipo not in ("RET", "RTC", "RIA", "RI")))
        isr = _resume(ap, lambda lado, tipo: (lado == "R" and tipo == "RET") or (lado == "D" and tipo in ("RET", "RTC")))
        iva_r = _resume(ap, lambda lado, tipo: tipo in ("RI", "RIA"))
        if pagos:
            v[27], v[28], v[29], v[30] = pagos["documentos"], pagos["fechas"], pagos["ml"], pagos["me"]
        if isr:
            v[31], v[32] = isr["documentos"], isr["ml"]
        if iva_r:
            v[33], v[34] = iva_r["documentos"], iva_r["ml"]
        v[35] = centros.get(d["keytra"], "")
        f = Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                 nombre=_txt_fecha(_vencimiento(d)), valores=v)
        f.formulas = {25: ("lineal", [(1, 21), (-1, 22), (-1, 23), (-1, 24)])}
        return [f]

    filas = _bloques(r, docs, cols, fila, [8, 9, 10, 11, 18, 19, 21, 22, 23, 24, 25, 29, 30, 32, 34])
    rep = r.reporte("CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN", r.periodo(), cols, filas, "Correlativo")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("VENCIMIENTO", 11)
    rep.grupos_columnas = [(8, 9, "TOTAL MONEDA LOCAL"), (10, 11, "TOTAL MONEDA EXTRANJERA"),
                           (22, 24, "VALORES CALCULADOS"), (27, 30, "PAGOS"), (31, 34, "RETENCIONES APLICADAS")]
    if not centros:
        rep.columnas_ocultas = {35}
    return rep


def correlativo_vendedor(p):
    """con_empdoctos_correl_fac_vendedor: correlativo con cliente base, vendedor y comisión."""
    r = _Fac(p)
    docs = r.documentos(extra_select=""",
            IFNULL((SELECT GROUP_CONCAT(DISTINCT li.codtablacomi) FROM transacciones_linea li
                     WHERE li.keytra = t.keytra), '') AS tabla_comision""")
    ret = Retenciones(r, [_t(d["coddirectorio"]) for d in docs])
    tablas = defaultdict(list)
    for c in consulta(
            """SELECT d.codtablacomi, d.monto_minimo, d.valor, d.porcentaje, d.usaporcentaje
                 FROM emp_tabla_comision_det d
                 JOIN emp_tabla_comision c ON c.codempresa = d.codempresa AND c.codtablacomi = d.codtablacomi
                WHERE d.codempresa = %s ORDER BY d.monto_minimo DESC""", (p.codempresa,)):
        tablas[_t(c["codtablacomi"])].append(c)

    def comision(tabla, base):
        for c in tablas.get(tabla, []):
            if _f(c["monto_minimo"]) <= round(base, 2):
                if c["usaporcentaje"] == 1:
                    return _f(c["porcentaje"]), round(base * _f(c["porcentaje"]) / 100, 2)
                return _f(c["valor"]), _f(c["valor"])
        return 0.0, 0.0

    cols = [Columna("CLIENTE BASE", ancho=12, texto=True), Columna("DIRECTORIO", ancho=14, texto=True),
            Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
            Columna("SERIE", ancho=10, texto=True), Columna("NÚMERO", ancho=12, texto=True),
            Columna("LLAVE INTERNA", ancho=26, texto=True), Columna("MON", ancho=6, texto=True),
            Columna("CARGO", "M. LOCAL"), Columna("ABONO", "M. LOCAL"), Columna("CARGO", "M. EXTRANJERA"),
            Columna("ABONO", "M. EXTRANJERA"), Columna("CNT", ancho=5, texto=True),
            Columna("ACT. CNT", ancho=5, texto=True), Columna("DOC. REF.", ancho=14, texto=True),
            Columna("ORDEN COMPRA", ancho=12, texto=True), Columna("LOTE", ancho=8, texto=True),
            Columna("SUCURSAL", ancho=8, texto=True), Columna("VALOR NETO"), Columna("IVA"), Columna("TOTAL"),
            Columna("RET. IVA"), Columna("RET. ISR"), Columna("EXENCIÓN IVA"), Columna("NETO A PAGAR"),
            Columna("SIS.", ancho=5, texto=True), Columna("CONCEPTO", ancho=50, texto=True),
            Columna("VENDEDOR", ancho=12, texto=True), Columna("TABLA", "COMISIÓN", ancho=10, texto=True),
            Columna("%", "COMISIÓN", ancho=8), Columna("VALOR", "COMISIÓN"),
            Columna("DESCRIPCIÓN ADICIONAL", ancho=40, texto=True)]
    n = len(cols)

    def fila(d):
        v = [None] * n
        ml_d, ml_h, me_d, me_h = _montos_dh(d)
        valordoc, iva = _f(d["valordocml"]), _f(d["iva"])
        v[0], v[1], v[2], v[3], v[4], v[5] = _t(d["codaca"]), _t(d["coddirectorio"]), _t(d["nombre_dir"]), \
            _t(d["codtipo"]), _t(d["serie"]), _num(d["numero"])
        v[6], v[7] = _t(d["keytra"]), _t(d["codmoneda"])
        v[8], v[9], v[10], v[11] = ml_d, ml_h, me_d, me_h
        v[12], v[13] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
        v[14], v[15], v[16], v[17] = _t(d["docref"]), _t(d.get("ocompra")), _t(d.get("lote")), _t(d.get("sucursal"))
        v[18], v[19], v[20] = round(valordoc - iva, 2), round(iva, 2), round(valordoc, 2)
        neto = valordoc - iva - _f(d["timbre_prensa"])
        v[21] = ret.calcula(_t(d["coddirectorio"]), "IVA", valordoc, neto, iva, r.agente_iva)
        v[22] = round(v[18] * 0.05, 2) if v[18] >= 2500 else 0.0
        v[23] = round(iva, 2) if d.get("usaexencioniva") == 1 else 0.0
        v[24] = round(v[20] - v[21] - v[22] - v[23], 2)
        v[25], v[26] = _t(d["codsistema"]), _concepto(d)
        v[27], v[28] = _t(d.get("codvendedor")), _t(d["tabla_comision"])
        v[29], v[30] = comision(v[28], neto)
        v[31] = _t(d.get("descripcion_adicional"))
        f = Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                 nombre=_txt_fecha(_vencimiento(d)), valores=v)
        f.formulas = {24: ("lineal", [(1, 20), (-1, 21), (-1, 22), (-1, 23)])}
        return [f]

    filas = _bloques(r, docs, cols, fila, [8, 9, 10, 11, 18, 19, 20, 21, 22, 23, 24, 30])
    rep = r.reporte("CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN [VENTAS]", r.periodo(), cols, filas, "Correlativo")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("VENCIMIENTO", 11)
    rep.grupos_columnas = [(8, 9, "TOTAL MONEDA LOCAL"), (10, 11, "TOTAL MONEDA EXTRANJERA"),
                           (21, 23, "VALORES CALCULADOS"), (28, 30, "COMISIÓN")]
    return rep


def _lineas(keytras, orden="li.linea"):
    salida = defaultdict(list)
    for bloque in _lotes(keytras):
        for li in consulta(f"SELECT li.* FROM transacciones_linea li WHERE li.keytra IN ({','.join(['%s'] * len(bloque))})"
                           f" ORDER BY li.keytra, {orden}", bloque):
            salida[li["keytra"]].append(li)
    return salida


def correlativo_detallado(p):
    """con_empdoctos_correl_fac_detallado: correlativo con las líneas (cantidad, código, precio) de cada factura."""
    r = _Fac(p)
    docs = r.documentos()
    lineas = _lineas([d["keytra"] for d in docs])
    cols = [Columna("DIRECTORIO", ancho=14, texto=True), Columna("NOMBRE", ancho=36, texto=True),
            Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=10, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("NÚMERO DOC.", ancho=14, texto=True),
            Columna("MON", ancho=6, texto=True), Columna("CARGO", "M. LOCAL"), Columna("ABONO", "M. LOCAL"),
            Columna("CARGO", "M. EXTRANJERA"), Columna("ABONO", "M. EXTRANJERA"), Columna("CNT", ancho=5, texto=True),
            Columna("ACT. CNT", ancho=5, texto=True), Columna("DOC. REF.", ancho=14, texto=True),
            Columna("ORDEN COMPRA", ancho=12, texto=True), Columna("LOTE", ancho=8, texto=True),
            Columna("SUCURSAL", ancho=8, texto=True), Columna("VALOR NETO"), Columna("IVA"),
            Columna("SIS.", ancho=5, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True),
            Columna("CONCEPTO", ancho=50, texto=True), Columna("TASA C.", ancho=9),
            Columna("CANTIDAD", "DETALLE", ancho=9), Columna("CÓDIGO", "DETALLE", ancho=12, texto=True),
            Columna("DESCRIPCIÓN", "DETALLE", ancho=40, texto=True), Columna("PRECIO UNIT.", "M. EXTRANJERA"),
            Columna("TOTAL", "M. EXTRANJERA"), Columna("PRECIO UNIT.", "M. LOCAL"), Columna("TOTAL", "M. LOCAL"),
            Columna("NETO", "M. LOCAL")]
    n = len(cols)

    def fila(d):
        v = [None] * n
        ml_d, ml_h, me_d, me_h = _montos_dh(d)
        v[0], v[1], v[2], v[3], v[4], v[5] = _t(d["coddirectorio"]), _t(d["nombre_dir"]), _t(d["codtipo"]), \
            _t(d["serie"]), _num(d["numero"]), _t(d.get("numerodoc"))
        v[6], v[7], v[8], v[9], v[10] = _t(d["codmoneda"]), ml_d, ml_h, me_d, me_h
        v[11], v[12] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
        v[13], v[14], v[15], v[16] = _t(d["docref"]), _t(d.get("ocompra")), _t(d.get("lote")), _t(d.get("sucursal"))
        v[17], v[18] = round(_f(d["valordocml"]) - _f(d["iva"]), 2), _f(d["iva"])
        v[19], v[20], v[21], v[22] = _t(d["codsistema"]), _t(d["keytra"]), _concepto(d, 200), _f(d["tasac"])
        salida = [Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]), valores=v)]
        tasa = _f(d["tasac"])
        for li in lineas.get(d["keytra"], []):
            w = [None] * n
            w[23], w[24], w[25] = _f(li["cantidad"]), _t(li["plu"]), _t(li["descripcion"])
            if tasa != 1:
                w[26], w[27] = _f(li["precio_u"]), _f(li["totalme"])
                w[28], w[29] = round(_f(li["precio_u"]) * tasa, 2), _f(li["total"])
            else:
                w[28], w[29] = _f(li["precio_u"]), _f(li["total"])
            w[30] = _f(li["subtotal"])
            salida.append(Fila(tipo="cuenta", nivel=2, valores=w))
        return salida

    filas = _bloques(r, docs, cols, fila, [7, 8, 9, 10, 17, 18])
    for f in filas:               # los totales de las líneas también suman
        if f.tipo == "total" and f.rango:
            for k in (27, 29, 30):
                f.valores[k] = 0.0
    _suma_lineas(filas, (27, 29, 30))
    rep = r.reporte("CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN - DETALLADO", r.periodo(), cols, filas, "Correlativo")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("", 2)
    rep.grupos_columnas = [(7, 8, "TOTAL MONEDA LOCAL"), (9, 10, "TOTAL MONEDA EXTRANJERA"),
                           (23, 30, "DETALLE DE LA FACTURA")]
    return rep


def _suma_lineas(filas, cols):
    """Totales de columnas que solo llevan las filas de detalle (nivel 2)."""
    actual = []
    subtotales = []
    for f in filas:
        if f.tipo == "cuenta":
            actual.append(f)
        elif f.tipo == "total" and f.rango:
            for k in cols:
                f.valores[k] = round(sum(x.valores[k] or 0 for x in actual if x.nivel == 2), 2)
            subtotales.append(f)
            actual = []
        elif f.tipo == "total":
            for k in cols:
                f.valores[k] = round(sum(s.valores[k] or 0 for s in subtotales), 2)


def correlativo_ods(p):
    """
    con_empdoctos_correl_fac_gtl (Correlativo [ODS]): valor neto, IVA y gastos por cuenta ajena; con
    «detallado», las líneas de cada factura con cuenta ajena separada. Las columnas de orden de servicio,
    carta de porte, manifiesto y factura del cliente salen de gtl_mov si la tabla existe.
    """
    r = _Fac(p)
    gtl = _existe_tabla("gtl_mov")
    where, params = r.filtros(con_fmb=False, moneda="extranjera")
    extra = (", IFNULL(m.factura_cliente, '') AS factura_cliente, IFNULL(m.carta_porte, '') AS carta_porte"
             ", IFNULL(m.manifiesto, '') AS manifiesto, IFNULL(m.oservicio, '') AS oservicio"
             ", IFNULL(m.nomvendedor, '') AS nomvendedor") if gtl else ""
    docs = consulta(
        f"""
        SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir{extra}
          FROM transacciones t
          LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
          {'LEFT JOIN gtl_mov m ON m.keytra = t.keytra' if gtl else ''}
         WHERE {where}{r.filtro_estado()}
         ORDER BY t.codsistema, t.codtipo, t.serie, t.numero, t.fecmov
        """, params)
    if not docs:
        raise ErrorReporte("No hay documentos de facturación con los parámetros indicados.")

    if p.detallado:
        lineas = _lineas([d["keytra"] for d in docs])
        cols = [Columna("SERIE", ancho=10, texto=True), Columna("NÚMERO", ancho=12, texto=True),
                Columna("ORDEN SERVICIO", ancho=12, texto=True), Columna("CÓDIGO", ancho=14, texto=True),
                Columna("DESCRIPCIÓN", ancho=40, texto=True), Columna("SUBTOTAL ML"), Columna("CTA. AJENA"),
                Columna("IVA"), Columna("TOTAL"), Columna("COSTO"), Columna("UTILIDAD BRUTA"),
                Columna("% UTILIDAD", formula=("pct", 10, 5))]
        n = len(cols)

        def fila(d):
            cab = [None] * n
            cab[0], cab[1], cab[3], cab[4] = _t(d["serie"]), _num(d["numero"]), _t(d["coddirectorio"]), \
                _t(d["nombre_dir"])
            cab[2] = _t(d.get("oservicio")) if gtl else ""
            salida = [Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]), valores=cab,
                           negrita=True)]
            for li in lineas.get(d["keytra"], []):
                w = [None] * n
                if d["anulado"] == 1:
                    w[4] = "*** A N U L A D O ***"
                else:
                    ajena = li["terminal"] == -5
                    w[3], w[4] = _t(li["codcuenta"]), _t(li["descripcion"])
                    w[5] = None if ajena else _f(li["subtotal"])
                    w[6] = _f(li["total"]) if ajena else 0.0
                    w[7], w[8] = _f(li["iva"]), _f(li["total"])
                    w[10] = round((w[5] or 0) + w[6] - (w[9] or 0), 2)
                    w[11] = None
                salida.append(Fila(tipo="cuenta", nivel=2, valores=w))
            return salida

        filas = _bloques(r, docs, cols, fila, [])
        _suma_lineas(filas, (5, 6, 7, 8, 10))
        rep = r.reporte("CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN [ODS] - DETALLE", r.periodo(), cols, filas,
                        "Correlativo")
        rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("", 2)
        return rep

    cols = [Columna("DIRECTORIO", ancho=14, texto=True), Columna("NOMBRE", ancho=36, texto=True),
            Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=10, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("MONEDA", ancho=7, texto=True),
            Columna("TASA/C", ancho=9), Columna("CARGO", "M. LOCAL"), Columna("ABONO", "M. LOCAL"),
            Columna("CARGO", "M. EXTRANJERA"), Columna("ABONO", "M. EXTRANJERA"), Columna("VALOR NETO"),
            Columna("IVA"), Columna("GASTOS POR CTA. AJENA"), Columna("ORDEN SERVICIO", ancho=12, texto=True),
            Columna("CARTA DE PORTE", ancho=12, texto=True), Columna("MANIFIESTO", ancho=12, texto=True),
            Columna("FACTURA CLIENTE", ancho=14, texto=True), Columna("VENDEDOR", ancho=20, texto=True),
            Columna("CONCEPTO", ancho=50, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True)]
    n = len(cols)

    def fila(d):
        v = [None] * n
        s = _sig(d)
        ml_d, ml_h, me_d, me_h = _montos_dh(d)
        v[0], v[1], v[2], v[3], v[4] = _t(d["coddirectorio"]), _t(d["nombre_dir"]), _t(d["codtipo"]), \
            _t(d["serie"]), _num(d["numero"])
        v[5], v[6], v[7], v[8], v[9], v[10] = _t(d["codmoneda"]), _f(d["tasac"]), ml_d, ml_h, me_d, me_h
        ajena = _f(d.get("gtos_cuenta_ajena"))
        v[11] = round((_f(d["valordocml"]) - (ajena + _f(d["iva"]))) * s, 2)
        v[12], v[13] = round(_f(d["iva"]) * s, 2), ajena
        if gtl:
            v[14], v[15], v[16], v[17], v[18] = _t(d["oservicio"]), _t(d["carta_porte"]), _t(d["manifiesto"]), \
                _t(d["factura_cliente"]), _t(d["nomvendedor"])
        v[19], v[20] = _concepto(d), _t(d["keytra"])
        return [Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]), valores=v)]

    filas = _bloques(r, docs, cols, fila, [7, 8, 9, 10, 11, 12, 13])
    rep = r.reporte("CORRELATIVO DE DOCUMENTOS DE FACTURACIÓN [ODS]", r.periodo(), cols, filas, "Correlativo")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("", 2)
    rep.grupos_columnas = [(7, 8, "TOTAL MONEDA LOCAL"), (9, 10, "TOTAL MONEDA EXTRANJERA")]
    if not gtl:
        rep.columnas_ocultas = {14, 15, 16, 17, 18}
    return rep


# ------------------------------------------------------------------ Facturas con retención de ISR

def retencion_isr(p):
    """
    con_facturas_retencion_isr: facturas (FAC / FMB) y documentos de venta de CXC con IVA, con el 5 %
    de ISR, quién lo retiene y la retención efectivamente recibida (pagos «RET ISR» o, si el
    documento viene de un aviso de cobro, las líneas «RET» de sus recibos).
    """
    r = _Fac(p)
    filtro, params = [], []
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s"),
                       (p.tipo_desde, "t.codtipo >= %s"), (p.tipo_hasta, "t.codtipo <= %s")):
        if valor:
            filtro.append("AND " + sql)
            params.append(valor)
    base = f"""
        SELECT t.fecmov, t.coddirectorio, t.numerodoc, t.codmoneda, t.codtipo, t.descontable, t.keytra, t.keyref,
               t.tasac, IFNULL(g.razonsocial, '') AS razonsocial, IFNULL(g.esagenteretisr, 0) AS esagenteretisr,
               {{valor}} AS valor, {{valorme}} AS valorme,
               ((t.base_gravada_bienes + t.base_gravada_servicios) * t.porcentaje) / 100 AS iva
          FROM transacciones t
          LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
         WHERE t.codempresa = %s AND t.codgrupo = %s AND t.anulado = 0 AND t.numero > 0
               AND t.fecmov BETWEEN %s AND %s {' '.join(filtro)} {{extra}}"""
    comunes = [p.codempresa, r.codgrupo, r.fecha_ini, r.fecha_fin, *params]
    docs = consulta(
        base.format(valor="t.valordocml / ((100 + t.porcentaje) / 100)",
                    valorme="t.valorme / ((100 + t.porcentaje) / 100)",
                    extra="AND t.codsistema IN ('FAC', 'FMB')")
        + " UNION ALL " +
        base.format(valor="(t.valordocml - t.iva) * -1", valorme="(t.valorme - t.porcentaje) * -1",
                    extra="AND t.codsistema = 'CXC' AND t.numerodoc > '' AND t.clasificacion_iva = 'V' "
                          "AND t.tipo_doc_iva <> ''"),
        (*comunes, *comunes))
    if not docs:
        raise ErrorReporte("No existe detalle con los parámetros indicados.")
    docs.sort(key=lambda d: (_t(d["coddirectorio"]), d["fecmov"] or date.min, _t(d["numerodoc"])))

    # Retenciones recibidas: por aplicación directa o por los recibos del aviso de cobro
    pagos_directos = defaultdict(list)
    for bloque in _lotes(d["keytra"] for d in docs if not _t(d["keyref"])):
        for a in consulta(
                f"""SELECT a.keytra AS doc, t.codtipo, t.fecmov, t.serie, t.numero, a.montodocml
                      FROM transacciones_aplica a LEFT JOIN transacciones t ON t.keytra = a.keytra_apl
                     WHERE a.keytra IN ({','.join(['%s'] * len(bloque))})""", bloque):
            pagos_directos[a["doc"]].append(a)
    pagos_aviso, lineas_rc = defaultdict(list), defaultdict(list)
    if _existe_tabla("cdc_ndt_avisocobro_pagos"):
        for bloque in _lotes(_t(d["keyref"]) for d in docs if _t(d["keyref"])):
            for a in consulta(
                    f"""SELECT DISTINCT a.keytra_avisocobro AS aviso, t.keytra, t.codtipo, t.fecmov, t.serie, t.numero,
                               t.valordocml AS montodocml
                          FROM cdc_ndt_avisocobro_pagos a LEFT JOIN transacciones t ON t.keytra = a.keytra_pago
                         WHERE a.keytra_avisocobro IN ({','.join(['%s'] * len(bloque))})""", bloque):
                pagos_aviso[a["aviso"]].append(a)
        for bloque in _lotes(a["keytra"] for ps in pagos_aviso.values() for a in ps):
            for li in consulta(
                    f"""SELECT keytra, total, forma_de_pago_rc, no_documento_rc FROM transacciones_linea
                         WHERE keytra IN ({','.join(['%s'] * len(bloque))})""", bloque):
                lineas_rc[li["keytra"]].append(li)

    monedas = list(dict.fromkeys(_t(d["codmoneda"]) or r.moneda_local for d in docs))
    if p.detalle:
        cols = [Columna("DIRECTORIO", ancho=14, texto=True), Columna("NOMBRE", ancho=36, texto=True),
                Columna("TIPO", ancho=7, texto=True), Columna("NÚMERO", ancho=14, texto=True),
                Columna("AVISO DE COBRO", ancho=26, texto=True), Columna("MONEDA EMISIÓN", ancho=8, texto=True),
                Columna("SUB-TOTAL"), Columna("IVA"), Columna("TOTAL"), Columna("5% ISR"), Columna("RETIENE"),
                Columna("AUTO-RETENCIÓN"), Columna("CONCEPTO", ancho=50, texto=True), Columna("PAGADO TOTAL"),
                Columna("DATOS RETENCIÓN", ancho=24, texto=True), Columna("FECHA", ancho=11, texto=True),
                Columna("VALOR RETENCIÓN")]
    else:
        cols = [Columna("NOMBRE", ancho=40, texto=True), Columna("SUB-TOTAL"), Columna("IVA")] + \
               [Columna(f"MONTO {m}") for m in monedas]
    n = len(cols)
    filas, subtotales = [], []
    grupos = defaultdict(list)
    for d in docs:
        grupos[_t(d["coddirectorio"])].append(d)
    for coddir, lista in grupos.items():
        if not p.detalle:
            v = [None] * n
            v[0] = _t(lista[0]["razonsocial"])
            v[1] = round(sum(_f(d["valor"]) for d in lista), 2)
            v[2] = round(sum(_f(d["iva"]) for d in lista), 2)
            for i, m in enumerate(monedas):
                v[3 + i] = round(sum(_f(d["valorme"]) if len(monedas) > 1 and m != r.moneda_local else _f(d["valor"])
                                     for d in lista if (_t(d["codmoneda"]) or r.moneda_local) == m), 2)
            filas.append(Fila(tipo="cuenta", nivel=1, cuenta=coddir, nombre=_txt_fecha(lista[0]["fecmov"]), valores=v))
            continue
        filas.append(Fila(tipo="grupo", cuenta=coddir, nombre=_t(lista[0]["razonsocial"])))
        bloque = []
        for d in lista:
            v = [None] * n
            valor, iva = round(_f(d["valor"]), 2), round(_f(d["iva"]), 2)
            ret5 = round(_f(d["valor"]) * 0.05, 2)
            v[0], v[1], v[2], v[3], v[4] = coddir, _t(d["razonsocial"]), _t(d["codtipo"]), _t(d["numerodoc"]), \
                _t(d["keyref"])
            v[5], v[6], v[7], v[8], v[9] = _t(d["codmoneda"]), valor, iva, round(valor + iva, 2), ret5
            if d["esagenteretisr"] == 1:
                v[10] = ret5
            else:
                v[11] = ret5
            v[12] = _t(d["descontable"])[:300]
            numeros, valret, fecha_ret = [], 0.0, None
            if not _t(d["keyref"]):
                ps = pagos_directos.get(d["keytra"], [])
                if ps:
                    v[13] = round(sum(_f(a["montodocml"]) for a in ps), 2)
                    fecha_ret = ps[0]["fecmov"]
                    for a in ps:
                        if _t(a["codtipo"]) == "RET ISR":
                            numeros.append(f"{_t(a['codtipo'])} {_t(a['serie'])} {_num(a['numero'])}")
                            valret += _f(a["montodocml"])
            else:
                ps = pagos_aviso.get(_t(d["keyref"]), [])
                if ps:
                    v[13] = round(sum(_f(a["montodocml"]) for a in ps), 2)
                    for a in ps:
                        for li in lineas_rc.get(a["keytra"], []):
                            if _t(li["forma_de_pago_rc"]) == "RET":
                                numeros.append(f"RET {_t(li['no_documento_rc'])}")
                                valret += _f(li["total"])
                                fecha_ret = a["fecmov"]
            if ps:
                v[14] = ", ".join(numeros)
                if valret:
                    v[15], v[16] = _txt_fecha(fecha_ret), round(valret, 2)
            if valret >= ret5 or ret5 < 0:
                v[10], v[11] = ret5, 0.0
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                               valores=v))
        filas += bloque
        sub = Fila(tipo="total", negrita=True, nombre="TOTAL", rango=(bloque[0], bloque[-1]), valores=[None] * n)
        for k in (6, 7, 8, 9, 10, 11, 13, 16):
            sub.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
        filas += [sub, Fila()]
        subtotales.append(sub)
    if p.detalle:
        tot = Fila(tipo="total", negrita=True, nombre="TOTALES", refs=[(1, s) for s in subtotales], valores=[None] * n)
        for k in (6, 7, 8, 9, 10, 11, 13, 16):
            tot.valores[k] = round(sum(s.valores[k] or 0 for s in subtotales), 2)
    else:
        tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[0], filas[-1]), valores=[None] * n)
        for k in range(1, n):
            tot.valores[k] = round(sum(f.valores[k] or 0 for f in filas if f.tipo == "cuenta"), 2)
    filas.append(tot)
    rep = r.reporte("REPORTE DE FACTURACIÓN CON DETALLE DE RETENCIÓN DE ISR", r.periodo(), cols, filas, "Retencion ISR")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11) if p.detalle else ("DIRECTORIO", 14), \
        ("", 2) if p.detalle else ("FECHA", 11)
    return rep


# ------------------------------------------------------------------ Mensual y anual por cliente

def _filtro_conceptos(p, partes, params):
    for valor, campo in ((p.codcc, "concept.codcc"), (p.codci, "concept.codci"), (p.codca, "concept.codca")):
        if valor:
            partes.append(f"AND {campo} = %s")
            params.append(valor)


def mensual_por_cliente(p):
    """con_fac_mensual_por_cli: subtotal de las líneas facturadas por cliente, de enero al mes indicado."""
    r = _Fac(p)
    ini, fin = date(p.anio, 1, 1), fin_de_mes(p.anio, p.mes)
    partes, params = [], []
    for valor, sql in ((p.directorio_desde, "tra.coddirectorio >= %s"), (p.directorio_hasta, "tra.coddirectorio <= %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    _filtro_conceptos(p, partes, params)
    filas_bd = consulta(
        f"""
        SELECT tra.coddirectorio, IFNULL(dir.razonsocial, '') AS razonsocial, tra.codmoneda, MONTH(tra.fecmov) AS mes,
               SUM(IF(tra.codmoneda = %s, li.subtotal, li.subtotal_me) * IF(tra.deha = 'D', 1, -1)) AS valor
          FROM transacciones_linea li
          JOIN transacciones tra ON tra.codempresa = li.codempresa AND tra.keytra = li.keytra
          JOIN grudirectorio dir ON dir.codgrupo = tra.codgrupo AND dir.coddirectorio = tra.coddirectorio
          LEFT JOIN empconceptos concept ON concept.codempresa = li.codempresa AND concept.codsistema = li.codsistema
               AND concept.codconcepto = li.codconcepto
         WHERE tra.codempresa = %s AND tra.codsistema = 'FAC' AND tra.anulado = 0 AND tra.fecmov BETWEEN %s AND %s
               {' '.join(partes)}
         GROUP BY tra.codmoneda, tra.coddirectorio, razonsocial, MONTH(tra.fecmov)
        """, (r.moneda_local, p.codempresa, ini, fin, *params))
    if not filas_bd:
        raise ErrorReporte("No existe ningún registro con los parámetros especificados.")
    cols = [Columna(MESES[m][:3] if p.mes > 6 else MESES[m], ancho=13) for m in range(p.mes)] + \
           [Columna("TOTAL", formula=("suma", 0, p.mes - 1)), Columna("%", ancho=8, porcentaje=True)]
    n = len(cols)
    datos = defaultdict(lambda: defaultdict(float))
    nombres = {}
    for f in filas_bd:
        clave = (_t(f["codmoneda"]), _t(f["coddirectorio"]))
        datos[clave][f["mes"]] += _f(f["valor"])
        nombres[clave] = _t(f["razonsocial"])
    filas = []
    for moneda in sorted({c[0] for c in datos}, reverse=True):
        claves = sorted((c for c in datos if c[0] == moneda), key=lambda c: (-sum(datos[c].values()), c[1]))
        filas.append(Fila(tipo="grupo", cuenta=f"MONEDA [{moneda}]" + (" A [" + r.moneda_local + "]"
                                                                   if p.ver_moneda_local else "")))
        bloque = []
        for c in claves:
            v = [round(datos[c].get(m + 1, 0.0), 2) for m in range(p.mes)]
            v.append(round(sum(v), 2))
            v.append(None)
            bloque.append(Fila(tipo="cuenta", nivel=1, cuenta=c[1], nombre=nombres[c], valores=v))
        tot = Fila(tipo="total", negrita=True, nombre=f"TOTALES EN MONEDA [{moneda}]", rango=(bloque[0], bloque[-1]),
                   valores=[round(sum(b.valores[k] for b in bloque), 2) for k in range(p.mes + 1)] + [None])
        for b in bloque:
            b.valores[-1] = (b.valores[-2] / tot.valores[-2]) if tot.valores[-2] else 0.0
        tot.valores[-1] = 1.0 if tot.valores[-2] else 0.0
        filas += bloque + [tot, Fila()]
    rep = r.reporte("FACTURACIÓN MENSUAL POR CLIENTE", f"AL {_txt_fecha(fin)}", cols, filas, "Facturacion mensual",
                    con_filtros=False)
    rep.encabezado_a, rep.encabezado_b = ("CÓDIGO", 14), ("CLIENTE", 40)
    return rep


def anual_comparativo(p):
    """con_fac_anual_comparativo_cli: facturación (sin IVA ni timbre) del año actual contra el anterior, por cliente."""
    r = _Fac(p)
    actual, anterior = p.anio, (p.anio2 or p.anio - 1)
    partes, params = [], []
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    filas_bd = consulta(
        f"""
        SELECT t.codmoneda, t.coddirectorio, IFNULL(g.razonsocial, '') AS razonsocial,
               SUM(IF(YEAR(t.fecmov) = %s, IF(t.codmoneda = %s, t.valor - t.iva - t.timbre_prensa, t.valorme), 0)) AS actual,
               SUM(IF(YEAR(t.fecmov) = %s, IF(t.codmoneda = %s, t.valor - t.iva - t.timbre_prensa, t.valorme), 0)) AS anterior
          FROM transacciones t
          LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
         WHERE t.codempresa = %s AND t.codsistema IN ('FAC', 'FMB') AND t.anulado = 0
               AND t.fecmov BETWEEN %s AND %s {' '.join(partes)}
         GROUP BY t.codmoneda, t.coddirectorio, razonsocial
         ORDER BY t.codmoneda DESC, t.coddirectorio
        """, (actual, r.moneda_local, anterior, r.moneda_local, p.codempresa,
              date(min(actual, anterior), 1, 1), date(max(actual, anterior), 12, 31), *params))
    if not filas_bd:
        raise ErrorReporte("No existe ningún registro con los parámetros especificados.")
    cols = [Columna(f"PERIODO ACTUAL ({actual})"), Columna(f"PERIODO ANTERIOR ({anterior})"),
            Columna("VARIACIÓN", formula=("resta", 0, 1)), Columna("%", formula=("ratio", 2, 0), ancho=9)]
    filas = []
    for moneda in list(dict.fromkeys(_t(f["codmoneda"]) for f in filas_bd)):
        filas.append(Fila(tipo="grupo", cuenta=f"MONEDA [{moneda}]"))
        bloque = []
        for f in (x for x in filas_bd if _t(x["codmoneda"]) == moneda):
            v = [round(_f(f["actual"]), 2), round(_f(f["anterior"]), 2), None, None]
            v[2] = round(v[0] - v[1], 2)
            v[3] = v[2] / v[0] if v[0] else None
            bloque.append(Fila(tipo="cuenta", nivel=1, cuenta=_t(f["coddirectorio"]), nombre=_t(f["razonsocial"]),
                               valores=v))
        tot = Fila(tipo="total", negrita=True, nombre=f"TOTALES EN MONEDA [{moneda}]", rango=(bloque[0], bloque[-1]),
                   valores=[round(sum(b.valores[k] for b in bloque), 2) for k in range(2)] + [None, None])
        tot.valores[2] = round(tot.valores[0] - tot.valores[1], 2)
        tot.valores[3] = tot.valores[2] / tot.valores[0] if tot.valores[0] else None
        filas += bloque + [tot, Fila()]
    rep = r.reporte("FACTURACIÓN ANUAL POR CLIENTE (COMPARATIVO)",
                    f"(ACTUAL) 01/01/{actual} - 31/12/{actual} VRS. (ANTERIOR) 01/01/{anterior} - 31/12/{anterior}",
                    cols, filas, "Facturacion anual", con_filtros=False)
    rep.encabezado_a, rep.encabezado_b = ("CÓDIGO", 14), ("CLIENTE", 40)
    return rep


# ------------------------------------------------------------------ Facturación por cuenta contable del concepto

def concepto_x_cuenta(p):
    """
    con_facturacion_concepto_x_cuenta: una fila por factura y una columna por cuenta contable del
    concepto facturado (subtotal de las líneas), con los datos del documento a la derecha y una fila
    final con el porcentaje de cada cuenta.
    """
    r = _Fac(p)
    where, params = r.filtros(alias="tra", moneda="siempre")
    partes = []
    _filtro_conceptos(p, partes, params)
    codperiodo = None
    try:
        codperiodo = ctx.periodo(p.codempresa, r.fecha_fin.year, r.fecha_fin.month).codperiodo
    except ErrorReporte:
        pass
    filas_bd = consulta(
        f"""
        SELECT tra.keytra, IFNULL(concept.codcuenta, 'N/A') AS cuenta,
               IFNULL(LEFT(nom.nomcta, 200), 'NO EXISTE') AS nomcta,
               SUM(li.subtotal * IF(tra.deha = 'D', 1, -1)) AS subtotal
          FROM transacciones_linea li
          JOIN transacciones tra ON tra.codempresa = li.codempresa AND tra.keytra = li.keytra
          LEFT JOIN empconceptos concept ON concept.codempresa = li.codempresa AND concept.codconcepto = li.codconcepto
          LEFT JOIN empnomenclatura nom ON nom.codempresa = concept.codempresa AND nom.codperiodo = %s
               AND nom.codcuenta = concept.codcuenta
         WHERE {where} {' '.join(partes)}
         GROUP BY tra.keytra, cuenta, nomcta
        """, (codperiodo, *params))
    if not filas_bd:
        raise ErrorReporte("No hay facturas con conceptos con los parámetros indicados.")
    keytras = list(dict.fromkeys(f["keytra"] for f in filas_bd))
    docs = {}
    for bloque in _lotes(keytras):
        for d in consulta(
                f"""SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir FROM transacciones t
                      LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
                     WHERE t.keytra IN ({','.join(['%s'] * len(bloque))})""", bloque):
            docs[d["keytra"]] = d
    cuentas = {}
    for f in filas_bd:
        cuentas.setdefault(f["cuenta"], " ".join(_t(f["nomcta"]).split()))
    orden_cuentas = sorted(cuentas)
    pos = {c: i for i, c in enumerate(orden_cuentas)}
    nc = len(orden_cuentas)
    cols = [Columna(cuentas[c] or c, f"[{c}]", ancho=15) for c in orden_cuentas]
    cols.append(Columna("TOTAL", formula=("suma", 0, nc - 1)))
    extra = [Columna("DIRECTORIO", ancho=14, texto=True), Columna("NOMBRE", ancho=36, texto=True),
             Columna("TIPO", ancho=7, texto=True), Columna("DOCUMENTO", ancho=16, texto=True),
             Columna("MON", ancho=6, texto=True), Columna("CARGO", "M. LOCAL"), Columna("ABONO", "M. LOCAL"),
             Columna("CARGO", "M. EXTRANJERA"), Columna("ABONO", "M. EXTRANJERA"), Columna("CNT", ancho=5, texto=True),
             Columna("ACT. CNT", ancho=5, texto=True), Columna("DOC. REF.", ancho=14, texto=True),
             Columna("ORDEN COMPRA", ancho=12, texto=True), Columna("LOTE", ancho=8, texto=True),
             Columna("SUCURSAL", ancho=8, texto=True), Columna("VALOR NETO"), Columna("IVA"),
             Columna("SIS.", ancho=5, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True),
             Columna("CONCEPTO", ancho=50, texto=True)]
    cols += extra
    n = len(cols)
    b = nc + 1
    por_doc = defaultdict(dict)
    for f in filas_bd:
        por_doc[f["keytra"]][f["cuenta"]] = por_doc[f["keytra"]].get(f["cuenta"], 0.0) + _f(f["subtotal"])
    lista = sorted((docs[k] for k in keytras if k in docs),
                   key=lambda d: (_t(d["codsistema"]), _t(d["codtipo"]), _t(d["serie"]), _f(d["numero"])))
    filas = []
    for d in lista:
        v = [None] * n
        for c, valor in por_doc[d["keytra"]].items():
            v[pos[c]] = round(valor, 2)
        v[nc] = round(sum(x or 0 for x in v[:nc]), 2)
        s = _sig(d)
        ml_d, ml_h, me_d, me_h = _montos_dh(d)
        v[b], v[b + 1], v[b + 2] = _t(d["coddirectorio"]), _t(d["nombre_dir"]), _t(d["codtipo"])
        v[b + 3], v[b + 4] = f"{_t(d['serie'])} {_num(d['numero'])}".strip(), _t(d["codmoneda"])
        v[b + 5], v[b + 6], v[b + 7], v[b + 8] = ml_d, ml_h, me_d, me_h
        v[b + 9], v[b + 10] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
        v[b + 11], v[b + 12], v[b + 13], v[b + 14] = _t(d["docref"]), _t(d.get("ocompra")), _t(d.get("lote")), \
            _t(d.get("sucursal"))
        v[b + 15] = round((_f(d["valordocml"]) - _f(d["iva"])) * s, 2)
        v[b + 16] = round(_f(d["iva"]) * s, 2)
        v[b + 17], v[b + 18], v[b + 19] = _t(d["codsistema"]), _t(d["keytra"]), _concepto(d)
        filas.append(Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                          nombre="", valores=v))
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[0], filas[-1]), valores=[None] * n)
    for k in list(range(nc + 1)) + [b + 5, b + 6, b + 7, b + 8, b + 15, b + 16]:
        tot.valores[k] = round(sum(f.valores[k] or 0 for f in filas), 2)
    pct = Fila(tipo="cuenta", nivel=1, nombre="% DEL TOTAL", negrita=True, valores=[None] * n)
    for k in range(nc + 1):
        pct.valores[k] = (tot.valores[k] / tot.valores[nc]) if tot.valores[nc] else 0.0
        pct.formulas[k] = ("ratio_filas", tot, k, nc)
    filas += [tot, pct]
    rep = r.reporte("FACTURACIÓN POR CUENTA CONTABLE DE CONCEPTO FACTURABLE", r.periodo(), cols, filas, "Facturacion")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("", 2)
    rep.grupos_columnas = [(b + 5, b + 6, "TOTAL MONEDA LOCAL"), (b + 7, b + 8, "TOTAL MONEDA EXTRANJERA")]
    rep.porcentaje_filas = {id(pct)}
    return rep


# ------------------------------------------------------------------ Agencia: consecutivo y cuadros finales

def _filtro_agencia(r, alias="tra"):
    p = r.p
    where, params = r.filtros(alias=alias, con_fmb=False, moneda="extranjera", docref=False)
    if not p.incluye_anulados:
        where += f" AND {alias}.anulado = 0"
    if not p.pendiente_numero:
        where += f" AND {alias}.numero > 0"
    me = bool(p.codmoneda and p.codmoneda != r.moneda_local)
    where += " AND li.totalme > 0" if me else " AND li.total > 0"
    return where, params, me


def consecutivo(p):
    """con_consecutivo_facturacion: líneas facturadas por tipo de medio (producción, arte, medio) y proveedor."""
    r = _Fac(p)
    where, params, me = _filtro_agencia(r)
    orden = "tra.keytra" if p.orden_fac == 2 else "tra.codsistema, tra.codtipo, tra.serie, tra.numero"
    filas_bd = consulta(
        f"""
        SELECT DISTINCT tra.keytra, tra.codtipo, tra.serie, tra.numero, tra.codmoneda, tra.coddirectorio AS codcliente,
               IFNULL(clt.razonsocial, '') AS cliente, li.liq_tipmedinv AS tipo_medio, li.coddirectorio AS codproveedor,
               IFNULL(prv.razonsocial, '') AS proveedor, tra.descontable, li.liq_codpresup AS presupuesto, li.total,
               li.totalme, li.subtotal, li.iva, li.timbre_prensa, li.tasac, tra.valordocml, tra.valorme, tra.anulado,
               tp.codtipo AS codtipo_prov, tp.serie AS serie_prov, tp.numero AS numero_prov, li.linea, tra.fecmov
          FROM transacciones_linea li
          JOIN transacciones tra ON tra.codempresa = li.codempresa AND tra.keytra = li.keytra
          LEFT JOIN transacciones tp ON tp.keytra = li.keyref AND tp.keyref = li.keytra
          LEFT JOIN grudirectorio clt ON clt.codgrupo = tra.codgrupo AND clt.coddirectorio = tra.coddirectorio
          LEFT JOIN grudirectorio prv ON prv.codgrupo = li.codgrupo AND prv.coddirectorio = li.coddirectorio
         WHERE {where}
         ORDER BY {orden}, tra.fecmov, li.liq_tipmedinv DESC, li.coddirectorio DESC
        """, params)
    if not filas_bd:
        raise ErrorReporte("No hay líneas de facturación con los parámetros indicados.")
    cols = [Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=10, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("MON", ancho=6, texto=True),
            Columna("CÓDIGO", "CLIENTE", ancho=14, texto=True), Columna("NOMBRE", "CLIENTE", ancho=36, texto=True),
            Columna("PRODUCCIÓN"), Columna("ARTE"), Columna("MEDIO"), Columna("SUB-TOTAL", "DESGLOSE MEDIOS"),
            Columna("I.V.A.", "DESGLOSE MEDIOS"), Columna("TIMBRE", "DESGLOSE MEDIOS"),
            Columna("CÓDIGO", "PROVEEDOR", ancho=14, texto=True), Columna("NOMBRE", "PROVEEDOR", ancho=36, texto=True),
            Columna("DOCUMENTO", "PROVEEDOR", ancho=18, texto=True), Columna("CONCEPTO", ancho=50, texto=True),
            Columna("PRESUPUESTO", ancho=12, texto=True), Columna("VALOR TOTAL")]
    n = len(cols)
    filas = []
    for f in filas_bd:
        v = [None] * n
        v[0], v[1], v[2], v[3], v[4], v[5] = _t(f["codtipo"]), _t(f["serie"]), _num(f["numero"]), \
            _t(f["codmoneda"]), _t(f["codcliente"]), _t(f["cliente"])
        monto = _f(f["totalme"]) if me else _f(f["total"])
        tipo = _t(f["tipo_medio"])
        v[6] = v[7] = v[8] = 0.0
        if tipo == "1":
            v[6] = monto
        elif tipo == "4":
            v[7] = monto
        elif tipo in ("2", "3"):
            v[8] = monto
        factor = (1 / _f(f["tasac"])) if me and _f(f["tasac"]) else 1
        v[9], v[10], v[11] = round(_f(f["subtotal"]) * factor, 2), round(_f(f["iva"]) * factor, 2), \
            round(_f(f["timbre_prensa"]) * factor, 2)
        v[12], v[13] = _t(f["codproveedor"]), _t(f["proveedor"])
        if f["codtipo_prov"] is not None:
            v[14] = f"{_t(f['codtipo_prov'])}  {_t(f['serie_prov'])}  {_num(f['numero_prov'])}"
        v[15] = _concepto(f, 250)
        v[16] = _t(f["presupuesto"])
        v[17] = _f(f["valorme"]) if me else _f(f["valordocml"])
        filas.append(Fila(tipo="cuenta", nivel=1, codigo=f["keytra"], cuenta=_t(f["keytra"])[-12:], valores=v))
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[0], filas[-1]), valores=[None] * n)
    for k in range(6, 12):
        tot.valores[k] = round(sum(f.valores[k] or 0 for f in filas), 2)
    filas.append(tot)
    rep = r.reporte("CONSECUTIVO DE FACTURACIÓN", r.periodo(), cols, filas, "Consecutivo")
    rep.encabezado_a, rep.encabezado_b = ("CONSECUTIVO", 14), ("", 2)
    rep.grupos_columnas = [(4, 5, "CLIENTE"), (6, 8, "TIPO DE MEDIO"), (9, 11, "DESGLOSE DE TIPO DE MEDIOS"),
                           (12, 14, "PROVEEDOR O MEDIO")]
    if not p.desglosa_medios:
        rep.columnas_ocultas = {9, 10, 11}
    return rep


def medios_produccion(p):
    """
    con_facturacion_medios_produccion (cuadros finales): por cliente y marca, medios y producción
    (total, impuestos y neto), arte, otros, total facturado y porcentaje por marca.
    """
    r = _Fac(p)
    where, params, _ = _filtro_agencia(r)
    vista = _existe_tabla("v_liq_tipsubmedio")
    filas_bd = consulta(
        f"""
        SELECT TRIM(tra.coddirectorio) AS coddirectorio, TRIM(IFNULL(g.razonsocial, '')) AS razonsocial,
               IF(TRIM(li.liq_marca) = '', 'MARCA VACIA', TRIM(li.liq_marca)) AS marca, TRIM(li.liq_tipmedinv) AS medio,
               SUM(li.iva) AS iva, SUM(li.timbre_prensa) AS timbre, SUM(li.total) AS total
          FROM transacciones_linea li
          JOIN transacciones tra ON tra.codempresa = li.codempresa AND tra.keytra = li.keytra
          LEFT JOIN grudirectorio g ON g.codgrupo = tra.codgrupo AND g.coddirectorio = tra.coddirectorio
         WHERE {where}
         GROUP BY tra.coddirectorio, razonsocial, marca, medio
         ORDER BY tra.coddirectorio, marca, medio
        """, params)
    if not filas_bd:
        raise ErrorReporte("No hay líneas de facturación con los parámetros indicados.")
    cols = [Columna("TOTAL", "MEDIOS"), Columna("IMPTOS. Y TIMBRES", "MEDIOS"), Columna("NETO", "MEDIOS"),
            Columna("TOTAL", "PRODUCCIÓN"), Columna("IMPTOS. Y TIMBRES", "PRODUCCIÓN"), Columna("NETO", "PRODUCCIÓN"),
            Columna("TOTAL ARTE"), Columna("OTROS"),
            Columna("TOTAL FACTURADO"), Columna("% POR MARCA", ancho=9, porcentaje=True)]
    n = len(cols)
    filas, cabeceras = [], []
    por_cliente = defaultdict(lambda: defaultdict(lambda: [0.0] * 8))
    nombres = {}
    for f in filas_bd:
        acc = por_cliente[f["coddirectorio"]][f["marca"]]
        nombres[f["coddirectorio"]] = f["razonsocial"]
        medio, total, iva, timbre = _t(f["medio"]), _f(f["total"]), _f(f["iva"]), _f(f["timbre"])
        tipo = "ARTE" if medio == "T31" else "MEDIOS" if medio[:1] == "M" else "PRODUCCION" if medio[:1] == "T" \
            else "OTROS"
        if tipo == "MEDIOS":
            acc[0] += total
            acc[1] += iva
            acc[2] += total - timbre - iva
        elif tipo == "PRODUCCION":
            acc[3] += total
            acc[4] += iva
            acc[5] += total - timbre - iva
        elif tipo == "ARTE":
            acc[6] += total
        else:
            acc[7] += total
    for cod, marcas in por_cliente.items():
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=cod, nombre=nombres[cod], valores=[None] * n)
        filas.append(cab)
        bloque = []
        for marca, acc in marcas.items():
            v = [round(x, 2) for x in acc] + [None, None]
            v[8] = round(v[0] + v[3] + v[6] + v[7], 2)
            f = Fila(tipo="cuenta", nivel=2, nombre=marca, valores=v)
            f.formulas = {8: ("lineal", [(1, 0), (1, 3), (1, 6), (1, 7)])}
            bloque.append(f)
        for k in range(9):
            cab.valores[k] = round(sum(b.valores[k] for b in bloque), 2)
        cab.hasta = bloque[-1]
        for b in bloque:
            b.valores[9] = b.valores[8] / cab.valores[8] if cab.valores[8] else 0.0
        cab.valores[9] = 1.0 if cab.valores[8] else 0.0
        filas += bloque
        cabeceras.append(cab)
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", refs=[(1, c) for c in cabeceras], valores=[None] * n)
    for k in range(9):
        tot.valores[k] = round(sum(c.valores[k] for c in cabeceras), 2)
    filas.append(tot)
    rep = r.reporte("CUADROS FINALES DE FACTURACIÓN", r.periodo(), cols, filas, "Reporte")
    rep.encabezado_a, rep.encabezado_b = ("CÓDIGO", 14), ("CLIENTE / MARCA", 40)
    rep.grupos_columnas = [(0, 2, "MEDIOS"), (3, 5, "PRODUCCIÓN")]
    if not vista:
        rep.subtitulo += " · (sin catálogo v_liq_tipsubmedio: medios clasificados por el código)"
    return rep
