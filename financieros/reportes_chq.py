"""
Reportes de Cheques: caja y bancos (sistema CHQ de VFP).

Bancos (menús 12 y 50, cuentas DM / AH de chqdefctas):
  con_dm_estcta / con_dm_estcta_detallado  Estado de cuenta con saldo inicial y saldo corrido (M.L. y M.E.)
  con_dm_tipodoc                           Correlativo de documentos por tipo, con saltos de numeración
  con_dm_dispo                             Disponibilidad: saldo anterior, cargos, abonos y saldo por cuenta
  con_chq_autorizado_xls / _entregados     Cheques autorizados / entregados (o pendientes) y su aplicación
  con_chq_pendientes_numero_xls            Registros pendientes de número (no impresos)
Cajas (menú 55):
  con_cc_estcta / con_cl_estcta            Estado de cuenta de caja chica / caja de liquidación
  con_cl_detalle_cc                        Cajas: detalle por documento de referencia (Crystal en VFP)
  con_liquidacion_caja_chica               Documentos de la caja con su centro de costo
  con_cc_resumen_ctas_doc_apl              Cuentas contables de los documentos aplicados a cada liquidación

VFP calcula el saldo inicial con 2 a 4 consultas por cuenta (cargos y abonos por separado, en M.L. y
M.E.) y, en los reportes con detalle, hace una o dos consultas por documento (documento de
referencia, aplicaciones). Aquí cada dato se pide una vez para todo el reporte.
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import ErrorReporte
from .modelo import Columna, Fila, Reporte

SISTEMA = "CHQ"
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


def _concepto(d, largo=200):
    if d.get("anulado") == 1:
        return "*** A N U L A D O ***"
    return " ".join(_t(d.get("descontable"))[:largo].split())


def cuentas_permitidas(codempresa, coduser, tipos=()):
    """Cuentas de chqdefctas del tipo pedido a las que el usuario tiene acceso (usa_control_de_usuarios)."""
    filtro, params = "", [codempresa, codempresa, coduser]
    if tipos:
        filtro = f" AND c.codtipocta IN ({','.join(['%s'] * len(tipos))})"
        params += list(tipos)
    return consulta(
        f"""
        SELECT c.*, IFNULL(m.nommoneda, '') AS nommoneda
          FROM chqdefctas c
          LEFT JOIN commonedas m ON m.codmoneda = c.codmoneda
         WHERE c.codempresa = %s
               AND (c.usa_control_de_usuarios = 0 OR EXISTS (
                    SELECT 1 FROM chqdefctas_users u
                     WHERE u.codempresa = %s AND u.codctachq = c.codctachq AND u.coduser = %s AND u.autorizado = 1)){filtro}
         ORDER BY c.codtipocta, c.codmoneda, c.nomctachq
        """, params)


class _Chq:
    """Empresa, fechas y cuenta de caja / banco elegida."""

    def __init__(self, p, tipos=(), cuenta_obligatoria=True):
        self.p = p
        self.emp = ctx.empresa(p.codempresa)
        e = consulta_uno("SELECT codmoneda, codgrupo FROM empresas WHERE codempresa = %s", (p.codempresa,)) or {}
        self.moneda_local, self.codgrupo = _t(e.get("codmoneda")), _t(e.get("codgrupo"))
        self.fecha_fin = p.fecha_fin or date.today()
        self.fecha_ini = p.fecha_ini or self.fecha_fin.replace(day=1)
        self.cuenta = None
        if p.codctachq:
            permitidas = {_t(c["codctachq"]): c for c in cuentas_permitidas(p.codempresa, p.coduser, tipos)}
            self.cuenta = permitidas.get(p.codctachq)
            if self.cuenta is None:
                raise ErrorReporte("La cuenta no existe, no es de este tipo o no tiene acceso a ella.")
        elif cuenta_obligatoria:
            raise ErrorReporte("Seleccione la cuenta.")

    @property
    def me(self):
        return bool(self.cuenta) and _t(self.cuenta["codmoneda"]) != self.moneda_local

    def nombre_cuenta(self):
        c = self.cuenta
        return f"[{_t(c['codtipocta'])} {_t(c['noctachq'])}] {_t(c['nomctachq'])}" if c else ""

    def cuenta_contable(self):
        """Cuenta contable de la caja / banco en el periodo de la fecha inicial (chqdetctanomencla)."""
        try:
            per = ctx.periodo(self.p.codempresa, self.fecha_ini.year, self.fecha_ini.month).codperiodo
        except ErrorReporte:
            return "", ""
        f = consulta_uno(
            """SELECT d.codcuenta, IFNULL(n.nomcta, '') AS nomcta FROM chqdetctanomencla d
                 LEFT JOIN empnomenclatura n ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo
                      AND n.codcuenta = d.codcuenta
                WHERE d.codempresa = %s AND d.codctachq = %s AND d.codperiodo = %s""",
            (self.p.codempresa, self.p.codctachq, per)) or {}
        return _t(f.get("codcuenta")), _t(f.get("nomcta"))

    def saldos_previos(self, cuentas, antes_de, solo_numerados=True):
        """
        Saldo de cada cuenta antes de una fecha: saldo inicial de la cuenta + cargos - abonos desde
        su inicio de operaciones, en su moneda (y en M.L.). Una consulta para todas las cuentas.
        """
        if not cuentas:
            return {}
        por_cod = {_t(c["codctachq"]): c for c in cuentas}
        filas = consulta(
            f"""
            SELECT t.codctachq,
                   SUM(IF(t.deha = 'D', t.valordocml, -t.valordocml)) AS ml,
                   SUM(IF(t.deha = 'D', t.valorme, -t.valorme)) AS me
              FROM transacciones t
              JOIN chqdefctas c ON c.codempresa = t.codempresa AND c.codctachq = t.codctachq
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.codctachq IN ({','.join(['%s'] * len(por_cod))})
                   AND t.fecmov >= c.feciniopera AND t.fecmov < %s {'AND t.numero > 0' if solo_numerados else ''}
             GROUP BY t.codctachq
            """, (self.p.codempresa, SISTEMA, *por_cod, antes_de))
        mov = {f["codctachq"]: f for f in filas}
        salida = {}
        for cod, c in por_cod.items():
            m = mov.get(cod, {})
            ini = _f(c["saldoiniconta"])
            me_cta = _t(c["codmoneda"]) != self.moneda_local
            salida[cod] = {
                "propia": round(ini + (_f(m.get("me")) if me_cta else _f(m.get("ml"))), 2),
                "ml": round(ini * (_f(c["tasac"]) if me_cta else 1) + _f(m.get("ml")), 2),
            }
        return salida

    def reporte(self, titulo_def, subtitulo, columnas, filas, hoja=""):
        titulo = (self.p.nombre or titulo_def).strip()
        archivo = re.sub(r"[^\w\- ]", "", f"{titulo} {self.p.codctachq or self.emp.codempresa} {self.fecha_fin:%Y-%m-%d}")
        rep = Reporte(titulo=titulo, subtitulo=subtitulo, empresa=self.emp, columnas=columnas, filas=filas,
                      nombre_archivo=archivo.strip() + ".xlsx", hoja=hoja,
                      fecha_hora=datetime.now() if self.p.fecha_hora else None)
        if self.me:      # cuenta en moneda extranjera: los saldos van en su moneda (VFP: «CIFRAS EXPRESADAS EN DOLARES»)
            from .ctacte import nombre_moneda
            rep.moneda_titulo = nombre_moneda(_t(self.cuenta["codmoneda"]))
        return rep

    def periodo(self):
        return f"PERIODO: {_txt_fecha(self.fecha_ini)} - {_txt_fecha(self.fecha_fin)}"


def _sin_datos(filas):
    if not filas:
        raise ErrorReporte("No existen datos con los parámetros indicados.")


# ------------------------------------------------------------------ Estado de cuenta (bancos y cajas)

_COLS_ESTADO = ("NOMBRE", "TIPO", "SERIE", "NÚMERO", "M")


def _estado_cuenta(r, docs, saldo_ini, titulo, detallado=False):
    """
    Filas de un estado de cuenta: SALDO INICIAL, un renglón por documento con saldo corrido y
    SALDO FINAL. `detallado`: además columnas en moneda extranjera con su propio saldo.
    """
    me = r.me
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
            Columna("SERIE", ancho=10, texto=True), Columna("NÚMERO", ancho=12, texto=True),
            Columna("M", ancho=5, texto=True)]
    if detallado:
        cols += [Columna("CARGO", "M. LOCAL"), Columna("ABONO", "M. LOCAL"), Columna("SALDO", "M. LOCAL"),
                 Columna("CARGO", "M. EXTRANJERA"), Columna("ABONO", "M. EXTRANJERA"),
                 Columna("SALDO", "M. EXTRANJERA"), Columna("TASA CAMBIO", ancho=10)]
        k_cargo, k_abono, k_saldo = 5, 6, 7
    else:
        cols += [Columna("CARGO"), Columna("ABONO"), Columna("SALDO")]
        k_cargo, k_abono, k_saldo = 5, 6, 7
    base = len(cols)
    cols += [Columna("CNT", ancho=5, texto=True), Columna("ACT", ancho=5, texto=True),
             Columna("DOC. REF.", ancho=14, texto=True), Columna("SUCURSAL", ancho=8, texto=True),
             Columna("SIS.", ancho=5, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True),
             Columna("CONCEPTO", ancho=50, texto=True), Columna("MONTO M.L.")]
    n = len(cols)
    ini = Fila(tipo="cuenta", nivel=1, negrita=True, nombre="SALDO INICIAL", valores=[None] * n)
    ini.valores[0] = "SALDO INICIAL"
    if detallado:
        ini.valores[7] = saldo_ini["ml"]
        if me:
            ini.valores[10] = saldo_ini["propia"]
            ini.valores[11] = _f(r.cuenta["tasac"])
    else:
        ini.valores[7] = saldo_ini["propia"]
    filas, previa = [ini], ini
    saldo, saldo_me = ini.valores[7], ini.valores[10] if detallado and me else 0.0
    for d in docs:
        v = [None] * n
        v[0], v[1], v[2], v[3], v[4] = _t(d.get("nombre_dir") or d.get("nombre")), _t(d["codtipo"]), \
            _t(d["serie"]), _num(d["numero"]), _t(d["codmoneda"])
        debe = _t(d["deha"]) == "D"
        ml, mex = _f(d["valordocml"]), _f(d["valorme"])
        formulas = {}
        if detallado:
            v[5], v[6] = (ml, None) if debe else (None, ml)
            saldo = round(saldo + (ml if debe else -ml), 2)
            v[7] = saldo
            formulas[7] = ("corrido", previa, 7, 5, 6)
            if me:
                v[8], v[9] = (mex, None) if debe else (None, mex)
                saldo_me = round(saldo_me + (mex if debe else -mex), 2)
                v[10] = saldo_me
                formulas[10] = ("corrido", previa, 10, 8, 9)
            v[11] = _f(d["tasac"])
        else:
            monto = mex if me else ml
            v[5], v[6] = (monto, None) if debe else (None, monto)
            saldo = round(saldo + (monto if debe else -monto), 2)
            v[7] = saldo
            formulas[7] = ("corrido", previa, 7, 5, 6)
        v[base], v[base + 1] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
        v[base + 2], v[base + 3], v[base + 4] = _t(d["docref"]), _t(d.get("sucursal")), _t(d["codsistema"])
        v[base + 5], v[base + 6] = _t(d["keytra"]), _concepto(d)
        if me:
            v[base + 7] = ml
        f = Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                 nombre=_t(d["coddirectorio"]), valores=v, formulas=formulas)
        filas.append(f)
        previa = f
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n)
    movs = filas[1:]
    for k in ((5, 6, 8, 9) if detallado else (5, 6)):
        tot.valores[k] = round(sum(f.valores[k] or 0 for f in movs), 2)
    if movs:
        tot.rango = (movs[0], movs[-1])
    tot.valores[7] = saldo
    tot.formulas = {7: ("refs", [(1, previa, 7)])}
    if detallado and me:
        tot.valores[10] = saldo_me
        tot.formulas[10] = ("refs", [(1, previa, 10)])
    filas += [tot]
    rep = r.reporte(titulo, f"{r.periodo()} · {r.nombre_cuenta()}", cols, filas, "Estado de cuenta")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    if detallado:
        rep.grupos_columnas = [(5, 7, "MONEDA LOCAL"), (8, 10, "MONEDA EXTRANJERA")]
        if not me:
            rep.columnas_ocultas = {8, 9, 10}
    if not me:
        rep.columnas_ocultas = set(rep.columnas_ocultas) | {n - 1}
    return rep


def _movimientos_cuenta(r, solo_numerados=True, orden="t.fecmov, t.serie, t.numero"):
    return consulta(
        f"""
        SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir
          FROM transacciones t
          LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
         WHERE t.codempresa = %s AND t.codsistema = %s AND t.codctachq = %s AND t.fecmov BETWEEN %s AND %s
               {'AND t.numero > 0' if solo_numerados else ''}
         ORDER BY {orden}
        """, (r.p.codempresa, SISTEMA, r.p.codctachq, r.fecha_ini, r.fecha_fin))


def estado_cuenta_banco(p):
    """con_dm_estcta: movimientos numerados de la cuenta con saldo inicial y saldo corrido en su moneda."""
    r = _Chq(p, ("DM", "AH"))
    saldo = r.saldos_previos([r.cuenta], r.fecha_ini)[p.codctachq]
    return _estado_cuenta(r, _movimientos_cuenta(r), saldo, "D.M. - ESTADO DE CUENTA")


def estado_cuenta_banco_detallado(p):
    """con_dm_estcta_detallado: ídem con moneda local y extranjera, cada una con su saldo corrido."""
    r = _Chq(p, ("DM", "AH"))
    saldo = r.saldos_previos([r.cuenta], r.fecha_ini)[p.codctachq]
    return _estado_cuenta(r, _movimientos_cuenta(r), saldo, "D.M. - ESTADO DE CUENTA - DETALLADO", detallado=True)


def _liquidaciones_caja(r, antes_de=None):
    """
    Documentos de la caja liquidados (transacciones_doc): cada liquidación del sistema CHQ aparece
    como un abono por lo que liquida de esta caja. Con `antes_de`, solo la suma anterior a la fecha.
    """
    p = r.p
    if antes_de is not None:
        f = consulta_uno(
            f"""SELECT SUM(IF(t.deha = 'D', {'d.montome' if r.me else 'd.montodocml'}, 0)) AS total
                  FROM transacciones t
                  JOIN transacciones_doc d ON d.keytra_apl = t.keytra
                  LEFT JOIN transacciones t1 ON t1.keytra = d.keytra
                 WHERE t.codempresa = %s AND t.codsistema = %s AND t1.codctachq = %s
                       AND t.fecmov >= %s AND t.fecmov < %s""",
            (p.codempresa, SISTEMA, p.codctachq, r.cuenta["feciniopera"], antes_de)) or {}
        return _f(f.get("total"))
    return consulta(
        """SELECT t.fecmov, t.coddirectorio, t.nombre, t.codtipo, t.serie, t.numero, t.codmoneda,
                  SUM(d.montodocml) AS valordocml, SUM(d.montome) AS valorme, 'H' AS deha, t.usacnt,
                  t.actualizadocnt, t.docref, t.sucursal, t.codsistema, t.keytra, t.keyref, t.descontable, t.anulado,
                  IFNULL(g.razonsocial, '') AS nombre_dir, t.tasac
             FROM transacciones t
             JOIN transacciones_doc d ON d.keytra_apl = t.keytra
             LEFT JOIN transacciones t1 ON t1.keytra = d.keytra
             LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
            WHERE t.codempresa = %s AND t.codsistema = %s AND t1.codctachq = %s AND t.fecmov BETWEEN %s AND %s
            GROUP BY d.keytra_apl""",
        (p.codempresa, SISTEMA, p.codctachq, r.fecha_ini, r.fecha_fin))


def _estado_caja(p, tipo, titulo, sustituye_referencia):
    r = _Chq(p, (tipo,))
    saldo = r.saldos_previos([r.cuenta], r.fecha_ini, solo_numerados=False)[p.codctachq]
    liquidado = _liquidaciones_caja(r, antes_de=r.fecha_ini)
    saldo = {"propia": round(saldo["propia"] - liquidado, 2), "ml": round(saldo["ml"] - liquidado, 2)}
    docs = _movimientos_cuenta(r, solo_numerados=False) + _liquidaciones_caja(r)
    docs.sort(key=lambda d: (d["fecmov"] or date.min, _t(d.get("nombre_dir")), _t(d["codtipo"])))
    if sustituye_referencia:            # caja chica: tipo y número del documento de referencia
        refs = {}
        for bloque in _lotes(_t(d.get("keyref")) for d in docs):
            for f in consulta(f"SELECT keytra, codtipo, numero FROM transacciones WHERE keytra IN "
                              f"({','.join(['%s'] * len(bloque))})", bloque):
                refs[f["keytra"]] = f
        for d in docs:
            ref = refs.get(_t(d.get("keyref")))
            if ref:
                d["codtipo"], d["numero"] = ref["codtipo"], ref["numero"]
    rep = _estado_cuenta(r, docs, saldo, titulo)
    codcuenta, nomcta = r.cuenta_contable()
    if codcuenta:
        rep.subtitulo += f" · CUENTA [{codcuenta}] {nomcta}"
    return rep


def estado_cuenta_caja_chica(p):
    """con_cc_estcta: movimientos de la caja chica y sus liquidaciones, con saldo corrido."""
    return _estado_caja(p, "CC", "CAJA CHICA - ESTADO DE CUENTA", sustituye_referencia=True)


def estado_cuenta_caja_liquidacion(p):
    """con_cl_estcta: movimientos de la caja de liquidación y sus liquidaciones, con saldo corrido."""
    return _estado_caja(p, "CL", "CAJA LIQUIDACIÓN - ESTADO DE CUENTA", sustituye_referencia=False)


# ------------------------------------------------------------------ Correlativo y disponibilidad

def correlativo_documentos(p):
    """con_dm_tipodoc: documentos de la cuenta por tipo, en rojo cuando el número no sigue al anterior."""
    r = _Chq(p, ("DM", "AH"))
    partes, params = [], []
    # VFP tenía el rango de tipos invertido (codtipo <= inicio y >= fin): solo servía con un tipo
    for valor, sql in ((p.tipo_desde, "t.codtipo >= %s"), (p.tipo_hasta, "t.codtipo <= %s"),
                       (p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    if p.clasificaciones:
        partes.append(f"AND t.codclasifica IN ({','.join(['%s'] * len(p.clasificaciones))})")
        params += list(p.clasificaciones)
    docs = consulta(
        f"""SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir FROM transacciones t
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.codctachq = %s AND t.fecmov BETWEEN %s AND %s
                   {' '.join(partes)}
             ORDER BY t.codtipo, t.serie, t.numero, t.fecmov""",
        (p.codempresa, SISTEMA, p.codctachq, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    me = r.me
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
            Columna("DOCUMENTO", ancho=18, texto=True), Columna("M", ancho=5, texto=True), Columna("CARGO"),
            Columna("ABONO"), Columna("CNT", ancho=5, texto=True), Columna("ACT", ancho=5, texto=True),
            Columna("DOC. REF.", ancho=14, texto=True), Columna("SUCURSAL", ancho=8, texto=True),
            Columna("SIS.", ancho=5, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True),
            Columna("CONCEPTO", ancho=50, texto=True), Columna("MONTO M.L.")]
    n = len(cols)
    filas, subtotales = [], []
    grupos = defaultdict(list)
    for d in docs:
        grupos[_t(d["codtipo"])].append(d)
    for tipo, lista in grupos.items():
        filas.append(Fila(tipo="grupo", cuenta=tipo))
        bloque, previo = [], None
        for d in lista:
            v = [None] * n
            debe = _t(d["deha"]) == "D"
            monto = _f(d["valorme"] if me else d["valordocml"])
            v[0], v[1], v[2], v[3] = _t(d["nombre_dir"]), tipo, f"{_t(d['serie'])}  {_num(d['numero'])}".strip(), \
                _t(d["codmoneda"])
            v[4], v[5] = (monto, None) if debe else (None, monto)
            v[6], v[7] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
            v[8], v[9], v[10], v[11] = _t(d["docref"]), _t(d.get("sucursal")), _t(d["codsistema"]), _t(d["keytra"])
            v[12] = _concepto(d, 300)
            if me:
                v[13] = _f(d["valordocml"])
            f = Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                     nombre=_t(d["coddirectorio"]), valores=v)
            actual = (_t(d["serie"]), int(_f(d["numero"])))
            if previo is not None and actual != (previo[0], previo[1] + 1):
                f.alerta = True
            previo = actual
            bloque.append(f)
        sub = Fila(tipo="total", negrita=True, nombre=f"TOTAL {tipo}", rango=(bloque[0], bloque[-1]),
                   valores=[None] * n)
        for k in (4, 5, 13):
            sub.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
        filas += bloque + [sub, Fila()]
        subtotales.append(sub)
    rep = r.reporte("D.M. - CORRELATIVO DE DOCUMENTOS", f"{r.periodo()} · {r.nombre_cuenta()}", cols, filas,
                    "Correlativo")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    if not me:
        rep.columnas_ocultas = {13}
    return rep


def disponibilidad(p):
    """con_dm_dispo: por cuenta del tipo elegido, saldo anterior, cargos, abonos y saldo actual."""
    r = _Chq(p, cuenta_obligatoria=False)
    tipo = p.codtipocta or "DM"
    cuentas = [c for c in cuentas_permitidas(p.codempresa, p.coduser, (tipo,))
               if _fecha(c["feciniopera"]) and c["feciniopera"] <= r.fecha_ini
               and (not p.codmoneda or _t(c["codmoneda"]) == p.codmoneda)]
    _sin_datos(cuentas)
    previos = r.saldos_previos(cuentas, r.fecha_ini, solo_numerados=False)
    movs = {f["codctachq"]: f for f in consulta(
        f"""SELECT codctachq, SUM(IF(deha = 'D', valordocml, 0)) AS cargo_ml, SUM(IF(deha = 'H', valordocml, 0)) AS abono_ml,
                   SUM(IF(deha = 'D', valorme, 0)) AS cargo_me, SUM(IF(deha = 'H', valorme, 0)) AS abono_me
              FROM transacciones
             WHERE codempresa = %s AND codsistema = %s AND fecmov BETWEEN %s AND %s
                   AND codctachq IN ({','.join(['%s'] * len(cuentas))})
             GROUP BY codctachq""",
        (p.codempresa, SISTEMA, r.fecha_ini, r.fecha_fin, *[_t(c["codctachq"]) for c in cuentas]))}
    cols = [Columna("BANCO", ancho=40, texto=True), Columna("SALDO ANTERIOR"), Columna("CARGOS"), Columna("ABONOS"),
            Columna("SALDO ACTUAL"), Columna("MONEDA", ancho=7, texto=True)]
    filas, subtotales = [], []
    monedas = list(dict.fromkeys(_t(c["codmoneda"]) for c in cuentas))
    for moneda in monedas:
        bloque = []
        for c in (x for x in cuentas if _t(x["codmoneda"]) == moneda):
            cod = _t(c["codctachq"])
            me = moneda != r.moneda_local
            m = movs.get(cod, {})
            cargo, abono = _f(m.get("cargo_me" if me else "cargo_ml")), _f(m.get("abono_me" if me else "abono_ml"))
            ant = previos[cod]["propia"]
            v = [_t(c["nomctachq"]), ant, round(cargo, 2), round(abono, 2), round(ant + cargo - abono, 2), moneda]
            f = Fila(tipo="cuenta", nivel=1, cuenta=_t(c["noctachq"]), nombre=cod, valores=v)
            f.formulas = {4: ("lineal", [(1, 1), (1, 2), (-1, 3)])}
            bloque.append(f)
        filas += bloque
        if not p.codmoneda:
            sub = Fila(tipo="total", negrita=True, nombre=f"SUB-TOTAL MONEDA [{moneda}]",
                       rango=(bloque[0], bloque[-1]), valores=[None] * 6)
            for k in range(1, 5):
                sub.valores[k] = round(sum(b.valores[k] for b in bloque), 2)
            filas += [sub, Fila()]
            subtotales.append(sub)
    nombre_tipo = (consulta_uno("SELECT nomtipocta FROM ownchqtipocta WHERE codtipocta = %s", (tipo,)) or {})
    rep = r.reporte("DISPONIBILIDAD", f"CUENTAS [{tipo}] {_t(nombre_tipo.get('nomtipocta'))} · {r.periodo()}"
                    + (f" · MONEDA [{p.codmoneda}]" if p.codmoneda else ""), cols, filas, "Disponibilidad")
    rep.encabezado_a, rep.encabezado_b = ("CUENTA", 16), ("CÓDIGO", 10)
    return rep


# ------------------------------------------------------------------ Cheques autorizados / entregados / pendientes

def _aplicaciones(keytras, liq_quedan=True):
    """
    Documentos aplicados con cada cheque (en ambos sentidos de transacciones_aplica, sin el propio
    cheque) y, para las liquidaciones de CXC, el detalle aplicado por keyaux.
    """
    salida = defaultdict(list)
    for bloque in _lotes(keytras):
        marcas = ",".join(["%s"] * len(bloque))
        cliente = ", IFNULL(dir.razonsocial, 'NO ENCONTRADO') AS nom_cliente" if liq_quedan else ", '' AS nom_cliente"
        join_liq = """LEFT JOIN liq_quedan liq ON liq.codempresa = tran.codempresa AND liq.keytra_quedan = tran.keyref
                           AND liq.keytra_quedan <> ''
                      LEFT JOIN grudirectorio dir ON dir.codgrupo = liq.codgrupo AND dir.coddirectorio = liq.coddirectoriocli""" \
            if liq_quedan else ""
        filas = consulta(
            f"""SELECT DISTINCT apl.keytra_apl AS cheque, apl.montodocml, tran.keytra, tran.fecmov, tran.codtipo,
                       tran.numerodoc, tran.nombre, tran.descontable{cliente}
                  FROM transacciones_aplica apl
                  JOIN transacciones tran ON tran.codempresa = apl.codempresa AND tran.keytra = apl.keytra {join_liq}
                 WHERE apl.keytra_apl IN ({marcas})
                UNION
                SELECT DISTINCT apl.keytra AS cheque, apl.montodocml, tran.keytra, tran.fecmov, tran.codtipo,
                       tran.numerodoc, tran.nombre, tran.descontable{cliente}
                  FROM transacciones_aplica apl
                  JOIN transacciones tran ON tran.codempresa = apl.codempresa AND tran.keytra = apl.keytra_apl {join_liq}
                 WHERE apl.keytra IN ({marcas})""", (*bloque, *bloque))
        for f in filas:
            if f["keytra"] != f["cheque"]:
                salida[f["cheque"]].append(f)
    # Detalle de las liquidaciones de CXC (VFP pasaba la llave del cheque, no la de la liquidación)
    liqs = [a["keytra"] for apls in salida.values() for a in apls if _t(a["keytra"]).upper()[6:12] == "LIQCXC"]
    detalle = defaultdict(list)
    for bloque in _lotes(liqs):
        marcas = ",".join(["%s"] * len(bloque))
        for f in consulta(
                f"""SELECT apl.keytra_apl AS liq, apl.montodocml, tran.keytra, tran.fecmov, tran.codtipo, tran.numerodoc,
                           tran.nombre, tran.descontable
                      FROM transacciones_aplica apl JOIN transacciones tran ON tran.codempresa = apl.codempresa
                           AND tran.keytra = apl.keyaux
                     WHERE apl.keytra_apl IN ({marcas})
                    UNION
                    SELECT apl.keytra AS liq, apl.montodocml, tran.keytra, tran.fecmov, tran.codtipo, tran.numerodoc,
                           tran.nombre, tran.descontable
                      FROM transacciones_aplica apl JOIN transacciones tran ON tran.codempresa = apl.codempresa
                           AND tran.keytra = apl.keyaux
                     WHERE apl.keytra IN ({marcas})""", (*bloque, *bloque)):
            if f["keytra"] != f["liq"]:
                detalle[f["liq"]].append(f)
    return salida, detalle


def _cheques(p, estado_campo, titulo, usuario_campo):
    """Base común de autorizados / entregados."""
    r = _Chq(p, cuenta_obligatoria=False)
    partes, params = [], []
    if estado_campo == "autorizado":
        partes.append("AND c.usachqautorizados = 1 AND t.numero > 0")
        if p.estado_chq == "pendientes":
            partes.append("AND IFNULL(t.fechaautorizado, '0000-00-00') = '0000-00-00' AND t.fecmov BETWEEN %s AND %s")
        else:
            partes.append("AND IFNULL(t.fechaautorizado, '0000-00-00') <> '0000-00-00' "
                          "AND t.fechaautorizado BETWEEN %s AND %s")
    else:
        partes.append("AND IF(c.usachqautorizados = 1, IFNULL(t.fechaautorizado, '0000-00-00') <> '0000-00-00', "
                      "IFNULL(t.fechaautorizado, '0000-00-00') = '0000-00-00') "
                      "AND IF(c.usa_activacion_chq = 1, IFNULL(t.fechaactivacionchq, '0000-00-00') <> '0000-00-00', "
                      "IFNULL(t.fechaactivacionchq, '0000-00-00') = '0000-00-00')")
        if p.estado_chq == "pendientes":
            partes.append("AND IFNULL(t.fechaentrega, '0000-00-00') = '0000-00-00' "
                          "AND IF(c.usachqautorizados = 1, t.fechaautorizado, t.fecmov) BETWEEN %s AND %s")
        else:
            partes.append("AND t.fechaentrega BETWEEN %s AND %s")
    params += [r.fecha_ini, r.fecha_fin]
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s"),
                       (p.codctachq, "t.codctachq = %s"), (p.usuario_chq, f"t.{usuario_campo} = %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    docs = consulta(
        f"""
        SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir, IFNULL(ua.nombre, '') AS usuario_autoriza,
               IFNULL(ue.nombre, '') AS usuario_entrega
          FROM transacciones t
          LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
          LEFT JOIN chqdefctas c ON c.codempresa = t.codempresa AND c.codctachq = t.codctachq
          LEFT JOIN empdoctos e ON e.codempresa = t.codempresa AND e.codtipodoctos = t.codtipodoctos AND e.codtipo = t.codtipo
          LEFT JOIN comtipodoctos ct ON ct.codsistema = e.codsistema AND ct.codtipodoctos = e.codtipodoctos
          LEFT JOIN vfusers ua ON ua.codempresa = t.codempresa AND ua.coduser = t.codusrautorizado
          LEFT JOIN vfusers ue ON ue.codempresa = t.codempresa AND ue.coduser = t.codusrentrega
         WHERE t.codempresa = %s AND t.codsistema = %s AND c.usachqentregados = 1 AND e.usaentrega_docs = 1
               AND t.anulado = 0 AND ct.codclavedoc = 'CH' {' '.join(partes)}
         ORDER BY t.fechaentrega, t.numero, t.nombre
        """, (p.codempresa, SISTEMA, *params))
    _sin_datos(docs)
    apls, detalle = _aplicaciones([d["keytra"] for d in docs]) if p.incluye_aplicacion else ({}, {})
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
            Columna("NÚMERO", ancho=18, texto=True), Columna("MON.", ancho=6, texto=True), Columna("VALOR"),
            Columna("FECHA", "APLICACIÓN", ancho=11, texto=True), Columna("TIPO", "APLICACIÓN", ancho=7, texto=True),
            Columna("NÚMERO", "APLICACIÓN", ancho=16, texto=True), Columna("APLICADO", "APLICACIÓN"),
            Columna("PROVEEDOR", "APLICACIÓN", ancho=30, texto=True),
            Columna("CLIENTE", "APLICACIÓN", ancho=30, texto=True),
            Columna("AUTORIZADO POR", ancho=20, texto=True), Columna("ENTREGADO POR", ancho=20, texto=True),
            Columna("CONCEPTO", ancho=50, texto=True)]
    n = len(cols)
    filas = []
    for d in docs:
        v = [None] * n
        v[0], v[1], v[2], v[3] = _t(d["nombre"]) or _t(d["nombre_dir"]), _t(d["codtipo"]), \
            f"{_t(d['serie'])} {_num(d['numero']).zfill(12)}".strip(), _t(d["codmoneda"])
        v[4] = _f(d["valorme"] if _t(d["codmoneda"]) != r.moneda_local else d["valordocml"])
        v[11], v[13] = _t(d["usuario_autoriza"]), _concepto(d)
        if estado_campo == "entregado":
            v[12] = _t(d["usuario_entrega"])
        filas.append(Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                          nombre=_t(d["coddirectorio"]), valores=v))
        for a in apls.get(d["keytra"], []):
            w = [None] * n
            w[5], w[6], w[7], w[8] = _txt_fecha(a["fecmov"]), _t(a["codtipo"]), _t(a["numerodoc"]), _f(a["montodocml"])
            w[9], w[10], w[13] = _t(a["nombre"]), _t(a["nom_cliente"]), _concepto(a)
            filas.append(Fila(tipo="cuenta", nivel=2, valores=w))
            for s in detalle.get(a["keytra"], []):
                x = [None] * n
                x[5], x[6], x[7], x[8] = _txt_fecha(s["fecmov"]), _t(s["codtipo"]), _t(s["numerodoc"]), \
                    _f(s["montodocml"])
                x[9], x[13] = _t(s["nombre"]), _concepto(s)
                filas.append(Fila(tipo="cuenta", nivel=3, valores=x))
    docs_filas = [f for f in filas if f.codigo]
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n)
    tot.valores[4] = round(sum(f.valores[4] for f in docs_filas), 2)
    filas.append(tot)
    leyenda = {"autorizado": ("AUTORIZADOS", "PENDIENTES DE AUTORIZAR"),
               "entregado": ("ENTREGADOS", "PENDIENTES DE ENTREGA")}[estado_campo]
    cuenta = r.nombre_cuenta() if r.cuenta else "TODAS LAS CUENTAS"
    rep = r.reporte(titulo, f"{r.periodo()} · {cuenta} · "
                    f"{leyenda[1] if p.estado_chq == 'pendientes' else leyenda[0]}", cols, filas, "Cheques")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    rep.grupos_columnas = [(5, 10, "DATOS DE LA APLICACIÓN")]
    ocultas = set()
    if not p.incluye_aplicacion:
        ocultas |= set(range(5, 11))
    if estado_campo == "autorizado":
        ocultas.add(12)
        if not p.mostrar_adicionales:
            ocultas.add(11)
    elif not p.mostrar_adicionales:
        ocultas |= {11, 12}
    rep.columnas_ocultas = ocultas
    return rep


def cheques_autorizados(p):
    """con_chq_autorizado_xls: cheques autorizados en el rango (o pendientes de autorizar)."""
    # VFP filtraba «Autorizado por» contra el usuario que entregó (codusrentrega)
    return _cheques(p, "autorizado", "MOVIMIENTO DE CHEQUES AUTORIZADOS", "codusrautorizado")


def cheques_entregados(p):
    """con_chq_entregados_xls: cheques entregados en el rango (o pendientes de entrega)."""
    return _cheques(p, "entregado", "MOVIMIENTO DE CHEQUES ENTREGADOS", "codusrentrega")


def pendientes_numero(p):
    """con_chq_pendientes_numero_xls: registros sin número (pendientes de impresión), no anulados."""
    r = _Chq(p, cuenta_obligatoria=False)
    partes, params = [], []
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s"),
                       (p.codctachq, "t.codctachq = %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    docs = consulta(
        f"""SELECT t.* FROM transacciones t
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.anulado = 0 AND t.numero = 0
                   AND t.fecmov BETWEEN %s AND %s {' '.join(partes)}
             ORDER BY t.fechaentrega, t.numero, t.nombre""",
        (p.codempresa, SISTEMA, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO DOC.", ancho=8, texto=True),
            Columna("NÚMERO", ancho=14, texto=True), Columna("MON", ancho=6, texto=True), Columna("VALOR"),
            Columna("BANCO", ancho=10, texto=True), Columna("DESCRIPCIÓN", ancho=50, texto=True)]
    filas = []
    for d in docs:
        v = [_t(d["nombre"]), _t(d["codtipo"]), f"{_t(d['serie'])} {_num(d['numero'])}".strip(), _t(d["codmoneda"]),
             _f(d["valorme"] if _t(d["codmoneda"]) != r.moneda_local else d["valordocml"]), _t(d["codctachq"]),
             _concepto(d)]
        filas.append(Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                          nombre=_t(d["coddirectorio"]), valores=v))
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[0], filas[-1]),
               valores=[None, None, None, None, round(sum(f.valores[4] for f in filas), 2), None, None])
    filas.append(tot)
    rep = r.reporte("REGISTROS NO IMPRESOS (PENDIENTES DE NÚMERO)",
                    f"{r.periodo()} · {r.nombre_cuenta() or 'TODAS LAS CUENTAS'}", cols, filas, "Pendientes")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    return rep


# ------------------------------------------------------------------ Cajas: detalle, liquidación y resumen

def cajas_detalle(p):
    """
    con_cl_detalle_cc (Crystal «Cajas de Liquidación»): documentos de la caja por directorio, mes y
    documento de referencia, con subtotal, IVA y total.
    """
    r = _Chq(p, ("CC", "CL"), cuenta_obligatoria=False)
    partes, params = [], []
    # En Crystal el filtro era «cuenta = '' or cuenta = x and (mes) and (docref)»: sin cuenta ignoraba mes
    # y documento de referencia. Aquí cada filtro se aplica por separado.
    for valor, sql in ((p.codctachq, "t.codctachq = %s"), (p.directorio_desde, "t.coddirectorio >= %s"),
                       (p.directorio_hasta, "t.coddirectorio <= %s"), (p.docref, "t.docref = %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    docs = consulta(
        f"""SELECT t.yearmes, t.docref, t.fecmov, t.fecha, t.numerodoc, t.nombre, t.nit, t.iva, t.total_doc,
                   t.descontable, t.coddirectorio, IFNULL(g.razonsocial, '') AS razonsocial, t.codctachq, t.valordocml,
                   t.keytra, t.anulado
              FROM transacciones t
              JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE t.codempresa = %s AND t.fecmov BETWEEN %s AND %s {' '.join(partes)}
               AND t.codctachq IN (SELECT codctachq FROM chqdefctas WHERE codempresa = %s AND codtipocta IN ('CC', 'CL'))
             ORDER BY t.coddirectorio, t.yearmes, t.docref, t.fecmov, t.numerodoc""",
        (p.codempresa, r.fecha_ini, r.fecha_fin, *params, p.codempresa))
    _sin_datos(docs)
    cols = [Columna("FECHA", ancho=11, texto=True), Columna("NÚMERO", ancho=14, texto=True),
            Columna("NIT", ancho=12, texto=True), Columna("NOMBRE", ancho=36, texto=True), Columna("SUB-TOTAL"),
            Columna("IVA"), Columna("TOTAL"), Columna("DESCRIPCIÓN", ancho=50, texto=True)]
    n = len(cols)
    filas, tot_dir = [], []
    por_dir = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    nombres = {}
    for d in docs:
        por_dir[_t(d["coddirectorio"])][_t(d["yearmes"])][_t(d["docref"])].append(d)
        nombres[_t(d["coddirectorio"])] = _t(d["razonsocial"])

    def total(nombre, rango_ini, rango_fin, hijos=None, nivel_neg=True):
        f = Fila(tipo="total", negrita=nivel_neg, nombre=nombre, valores=[None] * n)
        fuente = hijos if hijos is not None else []
        for k in (4, 5, 6):
            f.valores[k] = round(sum(x.valores[k] or 0 for x in fuente), 2)
        if hijos is None:
            f.rango = (rango_ini, rango_fin)
        return f

    for coddir, meses in por_dir.items():
        filas.append(Fila(tipo="grupo", cuenta=coddir, nombre=nombres[coddir]))
        tot_meses = []
        for mes, refs in meses.items():
            filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=f"{mes[:4]}-{mes[4:]}", valores=[None] * n))
            tot_refs = []
            for ref, lista in refs.items():
                bloque = []
                for d in lista:
                    iva = _f(d["iva"])
                    sub = _f(d["valordocml"]) if iva == 0 else _f(d["total_doc"]) - iva
                    tot = _f(d["valordocml"]) if iva == 0 else _f(d["total_doc"])
                    v = [_txt_fecha(d["fecha"]), _t(d["numerodoc"]), _t(d["nit"]), _t(d["nombre"]), round(sub, 2),
                         round(iva, 2), round(tot, 2), _concepto(d, 250)]
                    bloque.append(Fila(tipo="cuenta", nivel=2, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                                       nombre=ref, valores=v))
                filas += bloque
                t_ref = total(f"TOTAL DOC. REF. {ref or '(SIN)'}", bloque[0], bloque[-1])
                for k in (4, 5, 6):
                    t_ref.valores[k] = round(sum(b.valores[k] for b in bloque), 2)
                filas.append(t_ref)
                tot_refs.append(t_ref)
            t_mes = Fila(tipo="total", negrita=True, nombre=f"TOTAL {mes[:4]}-{mes[4:]}", valores=[None] * n,
                         refs=[(1, x) for x in tot_refs])
            for k in (4, 5, 6):
                t_mes.valores[k] = round(sum(x.valores[k] for x in tot_refs), 2)
            filas.append(t_mes)
            tot_meses.append(t_mes)
        t_dir = Fila(tipo="total", negrita=True, nombre=f"TOTAL {coddir}", valores=[None] * n,
                     refs=[(1, x) for x in tot_meses])
        for k in (4, 5, 6):
            t_dir.valores[k] = round(sum(x.valores[k] for x in tot_meses), 2)
        filas += [t_dir, Fila()]
        tot_dir.append(t_dir)
    gran = Fila(tipo="total", negrita=True, nombre="GRAN TOTAL", valores=[None] * n, refs=[(1, x) for x in tot_dir])
    for k in (4, 5, 6):
        gran.valores[k] = round(sum(x.valores[k] for x in tot_dir), 2)
    filas.append(gran)
    rep = r.reporte("CAJAS POR LIQUIDAR", f"{r.periodo()} · {r.nombre_cuenta() or 'CAJAS CC / CL'}", cols, filas,
                    "Cajas")
    rep.encabezado_a, rep.encabezado_b = ("FEC. MOV.", 11), ("DOC. REF.", 14)
    return rep


def liquidacion_caja_chica(p):
    """con_liquidacion_caja_chica: documentos de la caja por directorio, con el centro de costo de su partida."""
    r = _Chq(p, ("CC", "CL"))
    partes, params = [], []
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    if p.clasificaciones:
        partes.append(f"AND t.codclasifica IN ({','.join(['%s'] * len(p.clasificaciones))})")
        params += list(p.clasificaciones)
    docs = consulta(
        f"""SELECT DISTINCT t.keytra, t.fecmov, t.codtipo, t.serie, t.numero, t.coddirectorio, t.descontable, t.iva,
                   t.valorme, t.valordocml, t.total_doc, t.anulado, IFNULL(g.razonsocial, '') AS nomdir, cc.descripcion
              FROM transacciones t
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
              JOIN dettransacciones d ON d.keytra = t.keytra
              JOIN empcc cc ON cc.codempresa = d.codempresa AND cc.codcc = d.codcc
             WHERE t.codempresa = %s AND t.codctachq = %s AND t.fecmov BETWEEN %s AND %s {' '.join(partes)}
             ORDER BY t.coddirectorio, t.fecmov""",
        (p.codempresa, p.codctachq, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    cols = [Columna("TIPO", ancho=7, texto=True), Columna("SERIE", ancho=10, texto=True),
            Columna("NÚMERO", ancho=12, texto=True), Columna("NOMBRE PROVEEDOR", ancho=36, texto=True),
            Columna("DESCRIPCIÓN", ancho=50, texto=True), Columna("PARA COBRAR A", ancho=30, texto=True),
            Columna("IVA"), Columna("EXTRANJERA", "MONEDA"), Columna("LOCAL", "MONEDA"), Columna("TOTAL")]
    n = len(cols)
    filas, subtotales = [], []
    grupos = defaultdict(list)
    for d in docs:
        grupos[(_t(d["coddirectorio"]), _t(d["nomdir"]))].append(d)
    for (coddir, nombre), lista in grupos.items():
        filas.append(Fila(tipo="grupo", cuenta=coddir, nombre=nombre))
        bloque = []
        for d in lista:
            iva = _f(d["iva"])
            v = [_t(d["codtipo"]), _t(d["serie"]), _num(d["numero"]), nombre, _concepto(d, 250), _t(d["descripcion"]),
                 iva, _f(d["valorme"]), round(_f(d["valordocml"]) if iva == 0 else _f(d["total_doc"]) - iva, 2),
                 round(_f(d["valordocml"]) if iva == 0 else _f(d["total_doc"]), 2)]
            bloque.append(Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                               nombre=coddir, valores=v))
        sub = Fila(tipo="total", negrita=True, nombre=f"TOTAL {nombre}", rango=(bloque[0], bloque[-1]),
                   valores=[None] * n)
        for k in (6, 7, 8, 9):
            sub.valores[k] = round(sum(b.valores[k] for b in bloque), 2)
        filas += bloque + [sub, Fila()]
        subtotales.append(sub)
    gran = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n, refs=[(1, s) for s in subtotales])
    for k in (6, 7, 8, 9):
        gran.valores[k] = round(sum(s.valores[k] for s in subtotales), 2)
    filas.append(gran)
    rep = r.reporte("LIQUIDACIÓN DE CAJA CHICA", f"{r.periodo()} · {r.nombre_cuenta()}", cols, filas, "Liquidacion")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("CÓDIGO", 12)
    rep.grupos_columnas = [(7, 8, "MONEDA")]
    return rep


def resumen_cuentas_caja(p):
    """
    con_cc_resumen_ctas_doc_apl: por cada liquidación de la caja, las cuentas contables (y centro de
    integración) de los documentos que tiene aplicados, con cargo y abono.
    """
    r = _Chq(p, ("CC", "CL"))
    partes, params = [], []
    for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s"),
                       (p.numero_desde, "t.numero >= %s"), (p.numero_hasta, "t.numero <= %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    if p.clasificaciones:
        partes.append(f"AND t.codclasifica IN ({','.join(['%s'] * len(p.clasificaciones))})")
        params += list(p.clasificaciones)
    filas_bd = consulta(
        f"""SELECT t.keytra, t.fecmov, t.codtipo, t.numero, t.coddirectorio, t.nombre, d.codcuenta, d.codci,
                   IFNULL(n.nomcta, '') AS nomcta, SUM(d.cargo) AS cargo, SUM(d.abono) AS abono
              FROM transacciones t
              LEFT JOIN transacciones_aplica a ON a.keytra_apl = t.keytra
              LEFT JOIN dettransacciones d ON d.keytra = a.keytra
              LEFT JOIN empnomenclatura n ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo
                   AND n.codcuenta = d.codcuenta
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.codctachq = %s AND t.fecmov BETWEEN %s AND %s
                   {' '.join(partes)}
             GROUP BY t.keytra, d.codcuenta, d.codci, t.fecmov, t.codtipo, t.numero, t.coddirectorio, t.nombre, nomcta
             ORDER BY t.numero, t.fecmov, d.codcuenta""",
        (p.codempresa, SISTEMA, p.codctachq, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(filas_bd)
    cols = [Columna("TIPO", ancho=7, texto=True), Columna("NÚMERO", ancho=12, texto=True),
            Columna("CUENTA", ancho=16, texto=True), Columna("CI", ancho=8, texto=True),
            Columna("NOMBRE", ancho=40, texto=True), Columna("CARGO"), Columna("ABONO")]
    n = len(cols)
    filas, subtotales = [], []
    grupos = defaultdict(list)
    for f in filas_bd:
        grupos[f["keytra"]].append(f)
    for keytra, lista in grupos.items():
        c = lista[0]
        v = [None] * n
        v[0], v[1], v[2], v[4] = _t(c["codtipo"]), _num(c["numero"]), _t(c["coddirectorio"]), _t(c["nombre"])
        filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, codigo=keytra, cuenta=_txt_fecha(c["fecmov"]),
                          valores=v))
        bloque = []
        for f in lista:
            if not f["codcuenta"]:
                continue
            w = [None] * n
            w[2], w[3], w[4], w[5], w[6] = _t(f["codcuenta"]), _t(f["codci"]), _t(f["nomcta"]), _f(f["cargo"]), \
                _f(f["abono"])
            bloque.append(Fila(tipo="cuenta", nivel=2, valores=w))
        filas += bloque
        if bloque:
            sub = Fila(tipo="total", nombre="", rango=(bloque[0], bloque[-1]), valores=[None] * n)
            sub.valores[5] = round(sum(b.valores[5] for b in bloque), 2)
            sub.valores[6] = round(sum(b.valores[6] for b in bloque), 2)
            filas.append(sub)
            subtotales.append(sub)
    gran = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n, refs=[(1, s) for s in subtotales])
    gran.valores[5] = round(sum(s.valores[5] for s in subtotales), 2)
    gran.valores[6] = round(sum(s.valores[6] for s in subtotales), 2)
    filas.append(gran)
    rep = r.reporte("RESUMEN DE CUENTAS DE CAJA CHICA", f"{r.periodo()} · {r.nombre_cuenta()}", cols, filas, "Resumen")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("", 2)
    return rep


# ------------------------------------------------------------------ Conciliación bancaria (menú 20)

def _meses_antes(d, meses):
    m = d.month - 1 - meses
    return date(d.year + m // 12, m % 12 + 1, 1)


def _datos_conciliacion(r, p, condicion_inicio=True):
    """
    Lo que usan las dos conciliaciones (normal y AECID):
      saldo_banco   saldo inicial según el banco: saldo inicial del banco + movimientos conciliados
                    antes del mes (con «cuadrática»: saldo en libros al inicio del mes)
      conciliados   movimientos con fecha de conciliación en el mes
      saldo_libros  saldo en contabilidad al fin del mes
      circulacion   movimientos no conciliados al fin del mes
    `condicion_inicio`: lo anterior al inicio de operaciones de la cuenta solo cuenta si es DC / CC (VFP).
    """
    c, ini, fin = r.cuenta, r.fecha_ini, r.fecha_fin
    monto = "valorme" if r.me else "valordocml"
    inicio = c["feciniopera"] if _fecha(c["feciniopera"]) else None
    base = "t.codempresa = %s AND t.codsistema = %s AND t.codctachq = %s"
    bparams = [p.codempresa, SISTEMA, p.codctachq]

    previo_extra, previo_params = "", []
    lista_extra, lista_params = "", []
    if condicion_inicio and inicio:
        previo_extra = ("AND (t.fecmov >= %s OR (t.fechaconci >= %s AND t.fecmov < %s "
                        "AND t.codtipo = IF(t.deha = 'D', 'DC', 'CC')))")
        previo_params = [inicio, inicio, inicio]
        lista_extra = "AND (t.fecmov >= %s OR (t.fecmov >= %s AND t.codtipo IN ('CC', 'DC')))"
        lista_params = [inicio, _meses_antes(inicio, 6)]

    if p.conci_cuadratica:
        saldo_banco = _f(c["saldoiniconta"])
        sql_previo, pprev = "t.fecmov >= %s AND t.fecmov < %s", [inicio or date(1900, 1, 1), ini]
    else:
        saldo_banco = _f(c["saldoinibanco"])
        sql_previo, pprev = "IFNULL(t.fechaconci, '0000-00-00') > '0000-00-00' AND t.fechaconci < %s", [ini]
    f = consulta_uno(
        f"SELECT SUM(IF(t.deha = 'D', t.{monto}, -t.{monto})) AS neto FROM transacciones t "
        f"WHERE {base} AND {sql_previo} AND t.numero > 0 {previo_extra}",
        (*bparams, *pprev, *previo_params)) or {}
    saldo_banco = round(saldo_banco + _f(f.get("neto")), 2)

    conciliados = consulta(
        f"""SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir FROM transacciones t
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE {base} AND t.fechaconci BETWEEN %s AND %s AND t.numero > 0 {lista_extra}
             ORDER BY t.fecmov, t.deha, t.serie, t.numero""", (*bparams, ini, fin, *lista_params))
    f = consulta_uno(
        f"SELECT SUM(IF(t.deha = 'D', t.{monto}, -t.{monto})) AS neto FROM transacciones t "
        f"WHERE {base} AND t.fecmov BETWEEN %s AND %s", (*bparams, inicio or date(1900, 1, 1), fin)) or {}
    saldo_libros = round(_f(c["saldoiniconta"]) + _f(f.get("neto")), 2)
    circulacion = consulta(
        f"""SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir FROM transacciones t
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE {base} AND (IFNULL(t.fechaconci, '0000-00-00') = '0000-00-00' OR t.fechaconci > %s)
                   AND t.fecmov <= %s AND t.anulado = 0 {lista_extra}
             ORDER BY t.fecmov, t.deha, t.serie, t.numero""", (*bparams, fin, fin, *lista_params))
    if not c["usachqentregados"]:
        for d in circulacion:
            d["fechaentrega"] = d["fecmov"]
    return saldo_banco, conciliados, saldo_libros, circulacion


def _periodo_mes(r, p):
    r.fecha_ini = date(p.anio, p.mes or 1, 1)
    r.fecha_fin = date(p.anio, 12, 31) if not p.mes else ctx.fin_de_mes(p.anio, p.mes)


def conciliacion(p):
    """
    con_dm_conci: conciliación bancaria del mes. Hoja «Resumen» (conciliación cuadrática: banco
    contra contabilidad); hoja «Conciliacion»: saldo inicial según el banco, movimientos conciliados
    en el mes (marca los de otros meses), movimientos en circulación (separando los cheques no
    entregados) y saldo final en contabilidad; hojas de registros por aclarar y no entregados si los hay.
    """
    from .modelo import Libro
    r = _Chq(p, ("DM", "AH"))
    _periodo_mes(r, p)
    saldo_banco, conciliados, saldo_libros, circulacion = _datos_conciliacion(r, p)
    me = r.me
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
            Columna("NO. DOC.", ancho=18, texto=True), Columna("M", ancho=5, texto=True), Columna("CARGO"),
            Columna("ABONO"), Columna("NO ENTREGADOS"), Columna("SALDO"), Columna("CNT", ancho=5, texto=True),
            Columna("ACT", ancho=5, texto=True), Columna("DOC. REF.", ancho=14, texto=True),
            Columna("SUCURSAL", ancho=8, texto=True), Columna("SIS.", ancho=5, texto=True),
            Columna("LLAVE INTERNA", ancho=26, texto=True), Columna("OTRO MES", ancho=7, texto=True),
            Columna("CONCEPTO", ancho=50, texto=True), Columna("MONTO M.L.")]
    n = len(cols)

    def renglon(d, circula):
        v = [None] * n
        debe = _t(d["deha"]) == "D"
        monto = _f(d["valorme"] if me else d["valordocml"])
        v[0], v[1], v[2], v[3] = _t(d["nombre_dir"]), _t(d["codtipo"]), \
            f"{_t(d['serie'])} {_num(d['numero'])}".strip(), _t(d["codmoneda"])
        no_entregado = circula and not debe and not _fecha(d.get("fechaentrega")) \
            and _t(d["codtipodoctos"]).upper() == "CH"
        if debe:
            v[4] = monto
        elif no_entregado:
            v[6] = monto
        else:
            v[5] = monto
        v[8], v[9] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
        v[10], v[11], v[12], v[13] = _t(d["docref"]), _t(d.get("sucursal")), _t(d["codsistema"]), _t(d["keytra"])
        v[14] = "" if r.fecha_ini <= d["fecmov"] <= r.fecha_fin else "X"
        v[15] = _concepto(d, 300)
        if me:
            v[16] = _f(d["valordocml"])
        return Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                    nombre=_t(d["coddirectorio"]), valores=v)

    def linea(nombre, valores, total=False):
        v = [None] * n
        v[0] = nombre
        for k, x in valores.items():
            v[k] = round(x, 2)
        return Fila(tipo="total" if total else "cuenta", nivel=1, negrita=total, valores=v)

    def suma(lista, k):
        return round(sum(f.valores[k] or 0 for f in lista), 2)

    filas = [Fila(tipo="grupo", nombre="MOVIMIENTOS CONCILIADOS")]
    ini = linea("SALDO INICIAL EN BANCOS", {7: saldo_banco})
    ini.negrita = True
    filas.append(ini)
    previa, saldo, bloque = ini, saldo_banco, []
    for d in conciliados:
        f = renglon(d, False)
        saldo = round(saldo + (f.valores[4] or 0) - (f.valores[5] or 0), 2)
        f.valores[7] = saldo
        f.formulas = {7: ("corrido", previa, 7, 4, 5)}
        f.oculta = p.ocultar_conciliados
        bloque.append(f)
        previa = f
    filas += bloque
    otros = [f for f in bloque if f.valores[14] == "X"]
    del_mes = [f for f in bloque if f.valores[14] != "X"]
    cargos_banco, abonos_banco = suma(bloque, 4), suma(bloque, 5)
    saldo_final_banco = round(saldo_banco + cargos_banco - abonos_banco, 2)
    filas += [linea("CONCILIADOS QUE NO SON DEL MES", {4: suma(otros, 4), 5: suma(otros, 5)}),
              linea("CONCILIADOS DEL MES", {4: suma(del_mes, 4), 5: suma(del_mes, 5)}),
              linea("SALDO FINAL EN BANCO", {4: cargos_banco, 5: abonos_banco, 7: saldo_final_banco}, True),
              Fila(), Fila(tipo="grupo", nombre="MOVIMIENTOS EN CIRCULACIÓN")]
    sb = linea("SALDO FINAL EN BANCO", {7: saldo_final_banco})
    sb.negrita = True
    filas.append(sb)
    circ, saldo = [], saldo_final_banco
    for d in circulacion:
        f = renglon(d, True)
        saldo = round(saldo + (f.valores[4] or 0) - (f.valores[5] or 0) - (f.valores[6] or 0), 2)
        f.valores[7] = saldo
        circ.append(f)
    filas += circ
    otros = [f for f in circ if f.valores[14] == "X"]
    del_mes = [f for f in circ if f.valores[14] != "X"]
    c_circ, a_circ, ne_circ = suma(circ, 4), suma(circ, 5), suma(circ, 6)
    saldo_calculado = round(saldo_final_banco + c_circ - a_circ - ne_circ, 2)
    diferencia = round(saldo_libros - saldo_calculado, 2)
    filas += [linea("NO CONCILIADOS QUE NO SON DEL MES", {4: suma(otros, 4), 5: suma(otros, 5), 6: suma(otros, 6)}),
              linea("NO CONCILIADOS DEL MES", {4: suma(del_mes, 4), 5: suma(del_mes, 5), 6: suma(del_mes, 6)}),
              linea("TOTALES EN CIRCULACIÓN", {4: c_circ, 5: a_circ, 6: ne_circ, 7: saldo_calculado}, True),
              linea("DIFERENCIAS POR ACLARAR CON EL BANCO", {7: diferencia}),
              linea("SALDO FINAL EN CONTABILIDAD", {7: saldo_libros}, True)]
    codcuenta, _ = r.cuenta_contable()
    sub = f"{r.periodo()} · {r.nombre_cuenta()}" + (f" · CUENTA No. {codcuenta}" if codcuenta else "")
    hoja = r.reporte("D.M. - CONCILIACIÓN BANCARIA", sub, cols, filas, "Conciliacion")
    hoja.encabezado_a, hoja.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    if not me:
        hoja.columnas_ocultas = {16}

    rcols = [Columna("SALDO INICIAL"), Columna("CARGOS"), Columna("ABONOS"), Columna("SALDO")]

    def fr(nombre, vals, total=False):
        return Fila(tipo="total" if total else "cuenta", nivel=1, negrita=total, nombre=nombre, valores=vals)

    resumen = [
        Fila(tipo="grupo", nombre="BANCO"),
        fr("Saldo inicial", [saldo_banco, None, None, saldo_banco]),
        fr("Cargos registrados en el banco", [None, cargos_banco, None, round(saldo_banco + cargos_banco, 2)]),
        fr("Abonos registrados en el banco", [None, None, abonos_banco, saldo_final_banco]),
        fr("SALDO FINAL BANCO", [saldo_banco, cargos_banco, abonos_banco, saldo_final_banco], True),
        Fila(), Fila(tipo="grupo", nombre="CONTABILIDAD"),
        fr("Saldo final en banco", [saldo_final_banco, None, None, saldo_final_banco]),
        fr("Cargos en circulación", [None, c_circ, None, round(saldo_final_banco + c_circ, 2)]),
        fr("Abonos en circulación", [None, None, a_circ, round(saldo_final_banco + c_circ - a_circ, 2)]),
        fr("Registros pendientes de entregar", [None, None, ne_circ, saldo_calculado]),
        fr("SALDO SEGÚN CONCILIACIÓN", [saldo_final_banco, c_circ, round(a_circ + ne_circ, 2), saldo_calculado], True),
        fr("SALDO FINAL CONTABILIDAD", [None, None, None, saldo_libros], True),
        fr("DIFERENCIA", [None, None, None, diferencia], True),
    ]
    res = r.reporte("RESUMEN DE CONCILIACIÓN", sub, rcols, resumen, "Resumen")
    res.encabezado_a, res.encabezado_b = ("", 3), ("CONCEPTO", 44)
    hojas = [res, hoja]

    # Registros pendientes por aclarar con el banco (chq_pendconci; solo cuentas con conciliación cuadrática)
    if r.cuenta.get("usa_conci_cuadratica"):
        yearmes = f"{p.anio}{(p.mes or 1):02d}"
        pend = consulta(
            """SELECT cab.codconci, cab.descripcion AS des_conci, cab.saldo_inicial, chq.fecmov, det.*
                 FROM chq_pendconci_detalle det
                 JOIN chq_pendconci cab ON cab.codempresa = det.codempresa AND cab.codconci = det.codconci
                 LEFT JOIN transacciones chq ON chq.codempresa = det.codempresa AND chq.keytra = det.llaveconci
                WHERE cab.codempresa = %s AND cab.codctachq = %s AND cab.yearmes = %s AND cab.ultimo = '1'
                      AND IF(det.es_manual = 0, det.llaveconci = '', cab.yearmes <> '')
                ORDER BY det.fecha""", (p.codempresa, p.codctachq, yearmes))
        if pend:
            pc = [Columna("DOCUMENTO", ancho=16, texto=True), Columna("DESCRIPCIÓN", ancho=40, texto=True),
                  Columna("DEBE"), Columna("HABER"), Columna("ESTADO", ancho=28, texto=True),
                  Columna("MANUAL", ancho=7, texto=True), Columna("¿EN EL MES?", ancho=8, texto=True)]
            pf = []
            for d in pend:
                mes_mov = d["fecmov"].strftime("%Y%m") if _fecha(d.get("fecmov")) else yearmes
                if d["es_manual"] == 1 and _t(d["llaveconci"]):
                    estado = "MANUAL_CONCILIADO" if mes_mov == yearmes else "MANUAL_CONCILIADO_FUERA_MES"
                elif d["es_manual"] == 1:
                    estado = "MANUAL_NO_CONCILIADO"
                else:
                    estado = "ELECTRONICO"
                pf.append(Fila(tipo="cuenta", nivel=1, cuenta=_txt_fecha(d["fecha"]), valores=[
                    _t(d["numero"]), _t(d["descripcion"]), _f(d["cargo"]), _f(d["abono"]), estado,
                    "SI" if d["es_manual"] == 1 else "NO", "SI" if mes_mov == yearmes else "NO"]))
            pf.append(Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(pf[0], pf[-1]),
                           valores=[None, None, round(sum(x.valores[2] for x in pf), 2),
                                    round(sum(x.valores[3] for x in pf), 2), None, None, None]))
            h = r.reporte("REGISTROS PENDIENTES POR ACLARAR CON EL BANCO",
                          f"{_t(pend[0]['codconci'])} {_t(pend[0]['des_conci'])} · SALDO INICIAL "
                          f"{_f(pend[0]['saldo_inicial']):,.2f}", pc, pf, "Pendientes aclarar")
            h.encabezado_a, h.encabezado_b = ("FECHA", 11), ("", 2)
            hojas.append(h)

    # Cheques no entregados de la cuenta (en VFP la consulta comparaba codsistema con la cuenta: siempre vacía)
    no_ent = [d for d in circulacion if _t(d["deha"]) == "H" and _t(d["codtipodoctos"]).upper() == "CH"
              and not _fecha(d.get("fechaentrega"))]
    if no_ent:
        nc = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
              Columna("NO. DOC.", ancho=18, texto=True), Columna("MON.", ancho=6, texto=True), Columna("VALOR"),
              Columna("CONCEPTO", ancho=50, texto=True)]
        nf = [Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                   nombre=_t(d["coddirectorio"]),
                   valores=[_t(d["nombre_dir"]), _t(d["codtipo"]), f"{_t(d['serie'])} {_num(d['numero'])}".strip(),
                            _t(d["codmoneda"]), _f(d["valorme"] if me else d["valordocml"]), _concepto(d)])
              for d in no_ent]
        nf.append(Fila(tipo="total", negrita=True, nombre="TOTAL", rango=(nf[0], nf[-1]),
                       valores=[None] * 4 + [round(sum(x.valores[4] for x in nf), 2), None]))
        h = r.reporte("REGISTROS NO ENTREGADOS", sub, nc, nf, "No entregados")
        h.encabezado_a, h.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
        hojas.append(h)
    return Libro(titulo=hoja.titulo, hojas=hojas, nombre_archivo=hoja.nombre_archivo)


def conciliacion_aecid(p):
    """
    con_dm_conci_aecid: formato de conciliación de la AECID: saldo según el banco, movimientos
    conciliados y en circulación (fecha según libros y banco, importe con signo) y saldo según libros.
    """
    r = _Chq(p, ("DM", "AH"))
    _periodo_mes(r, p)
    saldo_banco, conciliados, saldo_libros, circulacion = _datos_conciliacion(r, p, condicion_inicio=False)
    cols = [Columna("FECHA S/BANCO", ancho=12, texto=True), Columna("CONCEPTO", ancho=60, texto=True),
            Columna("IMPORTE")]

    def renglon(d):
        importe = _f(d["valorme"] if r.me else d["valordocml"]) * (1 if _t(d["deha"]) == "D" else -1)
        doc = f"[{_t(d['codtipo'])} {_t(d['serie'])}{_num(d['numero'])}] - {_t(d['nombre'])}"
        concepto = f"*** A N U L A D O *** {doc}" if d["anulado"] == 1 else doc
        return Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                    valores=[_txt_fecha(d.get("fechaconci")), concepto, round(importe, 2)])

    def bloque_total(nombre, bloque):
        return Fila(tipo="total", negrita=True, nombre=nombre, rango=(bloque[0], bloque[-1]),
                    valores=[None, None, round(sum(f.valores[2] for f in bloque), 2)])

    filas = [Fila(tipo="total", negrita=True, nombre=f"SALDO SEGÚN EL BANCO AL {_txt_fecha(r.fecha_fin)}",
                  valores=[None, None, saldo_banco]), Fila(), Fila(tipo="grupo", nombre="MOVIMIENTOS CONCILIADOS")]
    b1 = [renglon(d) for d in conciliados]
    filas += b1 + ([bloque_total("TOTAL CONCILIADOS", b1)] if b1 else [])
    filas += [Fila(), Fila(tipo="grupo", nombre="MOVIMIENTOS EN CIRCULACIÓN")]
    b2 = [renglon(d) for d in circulacion]
    filas += b2 + ([bloque_total("TOTAL EN CIRCULACIÓN", b2)] if b2 else [])
    filas += [Fila(), Fila(tipo="total", negrita=True, nombre=f"SALDO SEGÚN EL LIBRO AL {_txt_fecha(r.fecha_fin)}",
                           valores=[None, None, saldo_libros])]
    rep = r.reporte("CONCILIACIÓN BANCARIA",
                    f"{r.nombre_cuenta()} · No. DE CTA. {_t(r.cuenta['noctachq'])} · {r.periodo()}",
                    cols, filas, "Conciliacion")
    rep.encabezado_a, rep.encabezado_b = ("FECHA S/LIBROS", 13), ("", 2)
    return rep


# ------------------------------------------------------------------ Posición bancaria y flujo de fondos

def tasa_cambio(codempresa, fecha):
    """
    Tasa de venta de comtdc en la fecha más cercana (moneda local contra USD). VFP buscaba la
    combinación «$.» / moneda local, que no existe en comtdc, y siempre caía en el valor fijo.
    """
    e = consulta_uno("SELECT codpais, codmoneda FROM empresas WHERE codempresa = %s", (codempresa,)) or {}
    f = consulta_uno(
        """SELECT tasacventa FROM comtdc WHERE codpais = %s AND codmoneda = %s AND codmonedacb <> codmoneda
            ORDER BY ABS(DATEDIFF(fecha, %s)), fecha DESC LIMIT 1""",
        (_t(e.get("codpais")), _t(e.get("codmoneda")), fecha)) or {}
    return _f(f.get("tasacventa")) or 1.0


def _ajustes(r, cuentas, fecha, meses_caducado):
    """
    Por cuenta: pendientes de número, no autorizados, no entregados y caducados al corte (los
    procedimientos documentos_* de VFP, una consulta por concepto para todas las cuentas).
    """
    if not cuentas:
        return {}
    cods = [_t(c["codctachq"]) for c in cuentas]
    marcas = ",".join(["%s"] * len(cods))
    me = {_t(c["codctachq"]): _t(c["codmoneda"]) != r.moneda_local for c in cuentas}
    base = (f"t.codempresa = %s AND t.codsistema = %s AND t.codctachq IN ({marcas}) AND t.anulado = 0 "
            f"AND t.fecmov <= %s")
    pbase = [r.p.codempresa, SISTEMA, *cods]
    sal = defaultdict(lambda: defaultdict(float))

    def acumula(nombre, filas):
        for f in filas:
            cod = f["codctachq"]
            sal[cod][nombre] += _f(f["me"] if me.get(cod) else f["ml"])

    acumula("pend_numero", consulta(
        f"SELECT t.codctachq, SUM(t.valordocml) AS ml, SUM(t.valorme) AS me FROM transacciones t "
        f"WHERE {base} AND t.numero = 0 GROUP BY t.codctachq", (*pbase, fecha)))
    entrega = f"""FROM transacciones t
          JOIN chqdefctas c ON c.codempresa = t.codempresa AND c.codctachq = t.codctachq
          JOIN empdoctos e ON e.codempresa = t.codempresa AND e.codtipodoctos = t.codtipodoctos AND e.codtipo = t.codtipo
         WHERE {base} AND c.usachqentregados = 1 AND e.usaentrega_docs = 1 AND t.numero > 0
               AND IFNULL(t.fechaentrega, '0000-00-00') = '0000-00-00'"""
    acumula("no_autorizados", consulta(
        f"SELECT t.codctachq, SUM(t.valordocml) AS ml, SUM(t.valorme) AS me {entrega} "
        f"AND IFNULL(t.fechaautorizado, '0000-00-00') = '0000-00-00' GROUP BY t.codctachq", (*pbase, fecha)))
    acumula("no_entregados", consulta(
        f"SELECT t.codctachq, SUM(t.valordocml) AS ml, SUM(t.valorme) AS me {entrega} "
        f"AND IFNULL(t.fechaautorizado, '0000-00-00') <> '0000-00-00' GROUP BY t.codctachq", (*pbase, fecha)))
    corte = _meses_antes(fecha, meses_caducado) if meses_caducado else fecha
    caduc = (f"{base} AND t.numero > 0 AND IFNULL(t.fechaconci, '0000-00-00') = '0000-00-00' "
             f"AND t.codctachq <> ''")
    acumula("caducados", consulta(
        f"SELECT t.codctachq, SUM(t.valordocml) AS ml, SUM(t.valorme) AS me FROM transacciones t "
        f"WHERE {caduc} GROUP BY t.codctachq", (*pbase, corte)))
    # menos lo aplicado entre documentos del mismo sistema (en ambos sentidos)
    for lado_t, lado_o in (("keytra", "keytra_apl"), ("keytra_apl", "keytra")):
        filas = consulta(
            f"""SELECT t.codctachq, SUM(a.montodocml) AS ml, SUM(a.montome) AS me FROM transacciones t
                  JOIN transacciones_aplica a ON a.codempresa = t.codempresa AND a.{lado_t} = t.keytra
                  JOIN transacciones o ON o.codempresa = a.codempresa AND o.keytra = a.{lado_o}
                 WHERE {caduc} AND o.codsistema = %s GROUP BY t.codctachq""", (*pbase, corte, SISTEMA))
        for f in filas:
            cod = f["codctachq"]
            sal[cod]["caducados"] -= _f(f["me"] if me.get(cod) else f["ml"])
    return sal


def _cuentas_flujo(r, tipo, moneda, fecha):
    return [c for c in cuentas_permitidas(r.p.codempresa, r.p.coduser, (tipo,))
            if c["usa_flujo_fondos"] == 1 and _fecha(c["feciniopera"]) and c["feciniopera"] <= fecha
            and (not moneda or _t(c["codmoneda"]) == moneda)]


def posicion_bancaria(p):
    """
    con_dm_posicion: por cuenta (con flujo de fondos), saldo anterior, cargos y abonos del rango,
    documentos pendientes de número, no autorizados, no entregados, por aclarar con el banco y
    caducados, saldo en M.L. y M.E.; con «resumen de conciliación», el saldo según el banco al
    cierre del mes y lo que está en circulación.
    """
    from .modelo import Libro
    r = _Chq(p, cuenta_obligatoria=False)
    tipo = p.codtipocta or "DM"
    cuentas = _cuentas_flujo(r, tipo, p.codmoneda, r.fecha_ini)
    _sin_datos(cuentas)
    tasa = float(p.tasa_cambio or 0) or tasa_cambio(p.codempresa, r.fecha_fin)
    cods = [_t(c["codctachq"]) for c in cuentas]
    marcas = ",".join(["%s"] * len(cods))
    previos = r.saldos_previos(cuentas, r.fecha_ini, solo_numerados=False)
    movs = {f["codctachq"]: f for f in consulta(
        f"""SELECT codctachq, SUM(IF(deha = 'D', valordocml, 0)) AS cargo_ml, SUM(IF(deha = 'H', valordocml, 0)) AS abono_ml,
                   SUM(IF(deha = 'D', valorme, 0)) AS cargo_me, SUM(IF(deha = 'H', valorme, 0)) AS abono_me
              FROM transacciones WHERE codempresa = %s AND codsistema = %s AND codctachq IN ({marcas})
                   AND fecmov BETWEEN %s AND %s GROUP BY codctachq""",
        (p.codempresa, SISTEMA, *cods, r.fecha_ini, r.fecha_fin))}
    aj = _ajustes(r, cuentas, r.fecha_fin, p.meses_caducado)
    aclarar = defaultdict(lambda: [0.0, 0.0])
    if p.incluye_aclarar:
        # VFP unía chq_pendconci sin condición (sumaba los pendientes de todas las cuentas)
        for f in consulta(
                f"""SELECT cab.codctachq, SUM(det.cargo) AS cargo, SUM(det.abono) AS abono
                      FROM chq_pendconci_detalle det
                      JOIN chq_pendconci cab ON cab.codempresa = det.codempresa AND cab.codconci = det.codconci
                     WHERE cab.codempresa = %s AND cab.codctachq IN ({marcas}) AND cab.ultimo = '1'
                     GROUP BY cab.codctachq""", (p.codempresa, *cods)):
            aclarar[f["codctachq"]] = [_f(f["cargo"]), _f(f["abono"])]
    conci = {}
    if p.resumen_conci:
        ini_mes = r.fecha_fin.replace(day=1)
        fin_mes = ctx.fin_de_mes(r.fecha_fin.year, r.fecha_fin.month)
        for f in consulta(
                f"""SELECT codctachq,
                       SUM(IF(fechaconci > '0000-00-00' AND fechaconci < %s, IF(deha = 'D', valordocml, -valordocml), 0)) AS ant_ml,
                       SUM(IF(fechaconci > '0000-00-00' AND fechaconci < %s, IF(deha = 'D', valorme, -valorme), 0)) AS ant_me,
                       SUM(IF(fechaconci BETWEEN %s AND %s AND deha = 'D', valordocml, 0)) AS c_ml,
                       SUM(IF(fechaconci BETWEEN %s AND %s AND deha = 'D', valorme, 0)) AS c_me,
                       SUM(IF(fechaconci BETWEEN %s AND %s AND deha = 'H', valordocml, 0)) AS a_ml,
                       SUM(IF(fechaconci BETWEEN %s AND %s AND deha = 'H', valorme, 0)) AS a_me,
                       SUM(IF((IFNULL(fechaconci, '0000-00-00') = '0000-00-00' OR fechaconci > %s) AND fecmov <= %s
                              AND anulado = 0, IF(deha = 'D', valordocml, -valordocml), 0)) AS circ_ml,
                       SUM(IF((IFNULL(fechaconci, '0000-00-00') = '0000-00-00' OR fechaconci > %s) AND fecmov <= %s
                              AND anulado = 0, IF(deha = 'D', valorme, -valorme), 0)) AS circ_me
                  FROM transacciones WHERE codempresa = %s AND codsistema = %s AND codctachq IN ({marcas})
                 GROUP BY codctachq""",
                (ini_mes, ini_mes, ini_mes, fin_mes, ini_mes, fin_mes, ini_mes, fin_mes, ini_mes, fin_mes,
                 fin_mes, fin_mes, fin_mes, fin_mes, p.codempresa, SISTEMA, *cods)):
            conci[f["codctachq"]] = f
    cols = [Columna("BANCO", ancho=36, texto=True), Columna("SALDO ANTERIOR"), Columna("CARGOS"), Columna("ABONOS"),
            Columna("PEND. DE NÚMERO"), Columna("NO AUTORIZADOS"), Columna("NO ENTREGADOS"),
            Columna("CARGO", "POR ACLARAR"), Columna("ABONO", "POR ACLARAR"), Columna("CADUCADOS"),
            Columna("SALDO M.L."), Columna("SALDO M.E."),
            Columna("SALDO INICIAL", "CONCILIACIÓN"), Columna("CARGOS", "CONCILIACIÓN"),
            Columna("ABONOS", "CONCILIACIÓN"), Columna("SALDO FINAL BANCO", "CONCILIACIÓN"),
            Columna("CON CIRCULACIÓN", "CONCILIACIÓN"), Columna("DIF. CON BANCO", "CONCILIACIÓN"),
            Columna("SALDO FINAL CONTA", "CONCILIACIÓN"), Columna("MONEDA", ancho=7, texto=True)]
    n = len(cols)
    filas = []
    for moneda in list(dict.fromkeys(_t(c["codmoneda"]) for c in cuentas)):
        bloque = []
        es_me = moneda != r.moneda_local
        for c in (x for x in cuentas if _t(x["codmoneda"]) == moneda):
            cod = _t(c["codctachq"])
            m, a = movs.get(cod, {}), aj.get(cod, {})
            v = [None] * n
            v[0], v[1] = _t(c["nomctachq"]), previos[cod]["propia"]
            v[2] = round(_f(m.get("cargo_me" if es_me else "cargo_ml")), 2)
            v[3] = round(_f(m.get("abono_me" if es_me else "abono_ml")), 2)
            v[4], v[5] = round(a.get("pend_numero", 0), 2), round(a.get("no_autorizados", 0), 2)
            v[6] = round(a.get("no_entregados", 0), 2)
            v[7], v[8] = round(aclarar[cod][0], 2), round(aclarar[cod][1], 2)
            v[9] = round(a.get("caducados", 0), 2)
            saldo = round(v[1] + v[2] - v[3] + v[4] + v[5] + v[6] + v[7] - v[8] + v[9], 2)
            v[10], v[11] = (round(saldo * tasa, 2), saldo) if es_me else (saldo, round(saldo / tasa, 2) if tasa else None)
            f = Fila(tipo="cuenta", nivel=1, cuenta=_t(c["noctachq"]), nombre=cod, valores=v)
            k_saldo = 11 if es_me else 10
            f.formulas = {k_saldo: ("lineal", [(1, 1), (1, 2), (-1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (-1, 8), (1, 9)])}
            f.formulas[10 if es_me else 11] = ("factor", f, k_saldo, tasa if es_me else round(1 / tasa, 10))
            if cod in conci:
                k = conci[cod]
                suf = "me" if es_me else "ml"
                ini_b = round(_f(c["saldoinibanco"]) + _f(k[f"ant_{suf}"]), 2)
                fin_b = round(ini_b + _f(k[f"c_{suf}"]) - _f(k[f"a_{suf}"]), 2)
                v[12], v[13], v[14], v[15] = ini_b, round(_f(k[f"c_{suf}"]), 2), round(_f(k[f"a_{suf}"]), 2), fin_b
                v[16] = round(fin_b + _f(k[f"circ_{suf}"]), 2)
                v[17], v[18] = 0.0, v[16]
            v[19] = moneda
            bloque.append(f)
        filas += bloque
        if not p.codmoneda:
            sub = Fila(tipo="total", negrita=True, nombre=f"SUB-TOTAL MONEDA [{moneda}]", rango=(bloque[0], bloque[-1]),
                       valores=[None] * n)
            for k in range(1, 19):
                sub.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
            filas += [sub, Fila()]
    rep = r.reporte("POSICIÓN BANCARIA", f"CUENTAS [{tipo}] · {r.periodo()} · TASA DE CAMBIO {tasa:.5f}"
                    + (f" · MONEDA [{p.codmoneda}]" if p.codmoneda else ""), cols, filas, "Posicion")
    rep.encabezado_a, rep.encabezado_b = ("CUENTA", 16), ("CÓDIGO", 10)
    rep.grupos_columnas = [(7, 8, "POR ACLARAR CON EL BANCO"), (12, 18, "CONCILIACIÓN BANCARIA")]
    ocultas = set()
    if not p.resumen_conci:
        ocultas |= set(range(12, 19))
    if not p.incluye_pend_numero:
        ocultas.add(4)
    if not p.incluye_aclarar:
        ocultas |= {7, 8}
    rep.columnas_ocultas = ocultas
    hojas = [rep] + _detalle_pendientes(r, cuentas, r.fecha_fin, p.meses_caducado)
    return Libro(titulo=rep.titulo, hojas=hojas, nombre_archivo=rep.nombre_archivo) if len(hojas) > 1 else rep


def _detalle_pendientes(r, cuentas, fecha, meses_caducado):
    """Hojas con el detalle de no autorizados, no entregados y caducados (si se pidieron y hay datos)."""
    p = r.p
    hojas = []
    if not cuentas:
        return hojas
    cods = [_t(c["codctachq"]) for c in cuentas]
    marcas = ",".join(["%s"] * len(cods))
    consultas = []
    entrega = """JOIN chqdefctas c ON c.codempresa = t.codempresa AND c.codctachq = t.codctachq
                 JOIN empdoctos e ON e.codempresa = t.codempresa AND e.codtipodoctos = t.codtipodoctos AND e.codtipo = t.codtipo"""
    cond_ent = ("c.usachqentregados = 1 AND e.usaentrega_docs = 1 AND t.numero > 0 "
                "AND IFNULL(t.fechaentrega, '0000-00-00') = '0000-00-00'")
    if p.detalle_no_autorizados:
        consultas.append(("NO AUTORIZADOS", entrega,
                          cond_ent + " AND IFNULL(t.fechaautorizado, '0000-00-00') = '0000-00-00'", fecha))
    if p.detalle_no_entregados:
        consultas.append(("NO ENTREGADOS", entrega,
                          cond_ent + " AND IFNULL(t.fechaautorizado, '0000-00-00') <> '0000-00-00'", fecha))
    if p.detalle_caducados:
        consultas.append(("CADUCADOS", "", "t.numero > 0 AND IFNULL(t.fechaconci, '0000-00-00') = '0000-00-00'",
                          _meses_antes(fecha, meses_caducado) if meses_caducado else fecha))
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
            Columna("NO. DOC.", ancho=18, texto=True), Columna("MON.", ancho=6, texto=True), Columna("VALOR"),
            Columna("CUENTA", ancho=10, texto=True), Columna("CONCEPTO", ancho=50, texto=True)]
    for titulo, join, cond, corte in consultas:
        docs = consulta(
            f"""SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir FROM transacciones t {join}
                  LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
                 WHERE t.codempresa = %s AND t.codsistema = %s AND t.codctachq IN ({marcas}) AND t.anulado = 0
                       AND t.fecmov <= %s AND {cond}
                 ORDER BY t.codctachq, t.codtipo, t.serie, t.numero""", (p.codempresa, SISTEMA, *cods, corte))
        if not docs:
            continue
        filas = [Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                      nombre=_t(d["coddirectorio"]),
                      valores=[_t(d["nombre_dir"]), _t(d["codtipo"]), f"{_t(d['serie'])} {_num(d['numero'])}".strip(),
                               _t(d["codmoneda"]),
                               _f(d["valorme"] if _t(d["codmoneda"]) != r.moneda_local else d["valordocml"]),
                               _t(d["codctachq"]), _concepto(d)]) for d in docs]
        filas.append(Fila(tipo="total", negrita=True, nombre="TOTAL", rango=(filas[0], filas[-1]),
                          valores=[None] * 4 + [round(sum(f.valores[4] for f in filas), 2), None, None]))
        h = r.reporte(f"REGISTROS {titulo}", f"AL {_txt_fecha(corte)}", cols, filas, titulo.title())
        h.encabezado_a, h.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
        hojas.append(h)
    return hojas


def _resumen_bancos(r, p, cuentas, fecha, tasa, titulo):
    """
    Hoja de disponibilidad por cuenta al cierre de `fecha`: saldo (todos los documentos, con o sin
    número) más no autorizados, no entregados y caducados, en M.L. y M.E. Devuelve (hoja, total M.L.).
    """
    previos = r.saldos_previos(cuentas, fecha + timedelta(days=1), solo_numerados=False)
    aj = _ajustes(r, cuentas, fecha, p.meses_caducado)
    cols_b = [Columna("BANCO", ancho=36, texto=True), Columna(f"SALDO AL {_txt_fecha(fecha)}"),
              Columna("DOCS. NO AUTORIZADOS"), Columna("DOCS. NO ENTREGADOS"), Columna("DOCUMENTOS CADUCADOS"),
              Columna("SALDO ACTUAL M.L."), Columna("SALDO ACTUAL M.E."), Columna("MONEDA", ancho=7, texto=True)]
    n = len(cols_b)
    fb, total_ml, total_me = [], 0.0, 0.0
    usa = {"aut": False, "ent": False, "conci": False}
    for moneda in list(dict.fromkeys(_t(c["codmoneda"]) for c in cuentas)):
        bloque = []
        es_me = moneda != r.moneda_local
        for c in (x for x in cuentas if _t(x["codmoneda"]) == moneda):
            cod = _t(c["codctachq"])
            a = aj.get(cod, {})
            v = [None] * n
            v[0], v[1] = _t(c["nomctachq"]), previos[cod]["propia"]
            v[2] = round(a.get("no_autorizados", 0), 2) if c["usachqautorizados"] else 0.0
            v[3] = round(a.get("no_entregados", 0), 2) if c["usachqentregados"] else 0.0
            v[4] = round(a.get("caducados", 0), 2) if c["usaconci"] else 0.0
            usa["aut"] |= bool(c["usachqautorizados"])
            usa["ent"] |= bool(c["usachqentregados"])
            usa["conci"] |= bool(c["usaconci"])
            saldo = round(v[1] + v[2] + v[3] + v[4], 2)
            v[5], v[6] = (round(saldo * tasa, 2), saldo) if es_me else (saldo, round(saldo / tasa, 2) if tasa else None)
            v[7] = moneda
            f = Fila(tipo="cuenta", nivel=1, cuenta=_t(c["noctachq"]), nombre=cod, valores=v)
            k = 6 if es_me else 5
            f.formulas = {k: ("lineal", [(1, 1), (1, 2), (1, 3), (1, 4)]),
                          (5 if es_me else 6): ("factor", f, k, tasa if es_me else round(1 / tasa, 10))}
            bloque.append(f)
        fb += bloque
        if not p.codmoneda:
            sub = Fila(tipo="total", negrita=True, nombre=f"SUB-TOTAL MONEDA [{moneda}]", rango=(bloque[0], bloque[-1]),
                       valores=[None] * n)
            for k in range(1, 7):
                sub.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
            fb += [sub, Fila()]
    cuentas_filas = [f for f in fb if f.tipo == "cuenta"]
    total_ml = round(sum(f.valores[5] or 0 for f in cuentas_filas), 2)
    total_me = round(sum(f.valores[6] or 0 for f in cuentas_filas), 2)
    fb.append(Fila(tipo="total", negrita=True, nombre="TOTAL EN BANCOS", valores=[None] * 5 + [total_ml, total_me, None]))
    bancos = r.reporte(titulo, f"AL {_txt_fecha(fecha)} · TASA DE CAMBIO {tasa:.5f}",
                       cols_b, fb, "Bancos")
    bancos.encabezado_a, bancos.encabezado_b = ("CUENTA", 16), ("CÓDIGO", 10)
    bancos.columnas_ocultas = {k for k, u in ((2, usa["aut"]), (3, usa["ent"]), (4, usa["conci"])) if not u}
    return bancos, total_ml


def flujo_de_fondos(p):
    """
    con_flujo_de_fondos_xls: disponibilidad en bancos a la fecha (con no autorizados, no entregados
    y caducados), más ingresos por cobrar (CXC) y provisiones de ingreso, menos egresos por pagar
    (CXP) y provisiones de egreso, por clasificación y cuenta.
    """
    from .modelo import Libro
    r = _Chq(p, cuenta_obligatoria=False)
    fecha = r.fecha_fin
    tipo = p.codtipocta or "DM"
    cuentas = _cuentas_flujo(r, tipo, p.codmoneda, fecha)
    tasa = float(p.tasa_cambio or 0) or tasa_cambio(p.codempresa, fecha)
    # VFP tomaba el saldo con movimientos ANTERIORES a la fecha («SALDO AL» sin el último día)
    bancos, total_ml = _resumen_bancos(r, p, cuentas, fecha, tasa, "FLUJO DE FONDOS - RESUMEN BANCOS")

    # Ingresos (CXC) y egresos (CXP) pendientes por clasificación, a la fecha
    ctas = consulta(
        """SELECT t.usactacorriente AS tipo, IF(t.codclasifica = '', 'SIN CLASIFICACION', t.codclasifica) AS clasif,
                  SUM(IF(t.filectacorriente IN ('CXCMOVDE', 'CXPMOVHA'), 1, -1)
                      * (t.valordocml - IF(t.valorme > 0, ROUND(t.noaplica * t.tasac, 2), t.noaplica))) AS saldo
             FROM transacciones t
            WHERE t.codempresa = %s AND t.anulado = 0 AND t.numero > 0 AND t.fecmov <= %s
                  AND ((t.usactacorriente = 'C' AND t.filectacorriente <> '')
                       OR (t.usactacorriente = 'P' AND LEFT(t.filectacorriente, 3) = 'CXP'))
            GROUP BY t.usactacorriente, clasif ORDER BY clasif""", (p.codempresa, fecha))
    try:
        codperiodo = ctx.periodo(p.codempresa, fecha.year, fecha.month).codperiodo
    except ErrorReporte:
        raise ErrorReporte("No hay periodo fiscal para la fecha indicada.")
    prov = consulta(
        """SELECT cp.tipomovimiento, d.codcuenta, n.nomcta,
                  IF(n.naturalezacta = 'D', SUM(d.cargo) - SUM(d.abono), SUM(d.abono) - SUM(d.cargo)) AS saldo
             FROM dettransacciones d
             JOIN transacciones t ON t.codempresa = d.codempresa AND t.keytra = d.keytra
             JOIN chq_cuentas_provisionff cp ON cp.codempresa = d.codempresa AND cp.codperiodo = d.codperiodo
                  AND cp.codcuenta = d.codcuenta
             JOIN empnomenclatura n ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
            WHERE d.codempresa = %s AND d.codperiodo = %s AND t.fecmov <= %s AND t.anulado = 0 AND t.numero > 0
                  AND t.actualizadocnt = 1
            GROUP BY cp.tipomovimiento, d.codcuenta, n.nomcta, n.naturalezacta
            ORDER BY d.codcuenta""", (p.codempresa, codperiodo, fecha))
    cols = [Columna("CONCEPTO", ancho=44, texto=True), Columna("MONTO M.L.")]
    filas = []

    def seccion(titulo, renglones):
        filas.append(Fila(tipo="grupo", nombre=titulo))
        bloque = [Fila(tipo="cuenta", nivel=1, cuenta=c, valores=[nom, round(v, 2)]) for c, nom, v in renglones]
        filas.extend(bloque)
        tot = Fila(tipo="total", negrita=True, nombre=f"TOTAL {titulo}", valores=[None, round(sum(b.valores[1] for b in bloque), 2)])
        if bloque:
            tot.rango = (bloque[0], bloque[-1])
        filas.append(tot)
        return tot

    t_bancos = Fila(tipo="total", negrita=True, nombre="BANCOS", valores=[None, total_ml])
    filas.append(t_bancos)
    t_ing = seccion("INGRESOS", [("", f["clasif"], _f(f["saldo"])) for f in ctas if f["tipo"] == "C"])
    t_ingp = seccion("INGRESOS PROVISIONES", [(_t(f["codcuenta"]), _t(f["nomcta"]), _f(f["saldo"]))
                                             for f in prov if int(f["tipomovimiento"] or 0) == 1])
    sub_i = Fila(tipo="total", negrita=True, nombre="SUB-TOTAL INGRESOS", refs=[(1, t_bancos), (1, t_ing), (1, t_ingp)],
                 valores=[None, round(total_ml + t_ing.valores[1] + t_ingp.valores[1], 2)])
    filas += [sub_i, Fila()]
    t_egr = seccion("EGRESOS", [("", f["clasif"], _f(f["saldo"])) for f in ctas if f["tipo"] == "P"])
    t_egrp = seccion("EGRESOS PROVISIONES", [(_t(f["codcuenta"]), _t(f["nomcta"]), _f(f["saldo"]))
                                            for f in prov if int(f["tipomovimiento"] or 0) == 2])
    sub_e = Fila(tipo="total", negrita=True, nombre="SUB-TOTAL EGRESOS", refs=[(1, t_egr), (1, t_egrp)],
                 valores=[None, round(t_egr.valores[1] + t_egrp.valores[1], 2)])
    gran = Fila(tipo="total", negrita=True, nombre="GRAN TOTAL", refs=[(1, sub_i), (-1, sub_e)],
                valores=[None, round(sub_i.valores[1] - sub_e.valores[1], 2)])
    filas += [sub_e, Fila(), gran]
    flujo = r.reporte("FLUJO DE FONDOS", f"AL {_txt_fecha(fecha)}", cols, filas, "Flujo")
    flujo.encabezado_a, flujo.encabezado_b = ("CUENTA", 16), ("", 2)
    hojas = [flujo] + ([bancos] if p.mostrar_resumen_bancos else []) + \
        _detalle_pendientes(r, cuentas, fecha, p.meses_caducado)
    return Libro(titulo=flujo.titulo, hojas=hojas, nombre_archivo=flujo.nombre_archivo) if len(hojas) > 1 else flujo


# ------------------------------------------------------------------ Recibos (menú 60)

def _filtros_doc(p, partes, params, alias="t", clasif=True, numero=True, serie=True, tipo=True):
    a = alias
    for valor, sql in ((p.directorio_desde, f"{a}.coddirectorio >= %s"), (p.directorio_hasta, f"{a}.coddirectorio <= %s"),
                       (p.codtipo if tipo else "", f"{a}.codtipo = %s"), (p.codmoneda, f"{a}.codmoneda = %s"),
                       (p.numero_desde if numero else None, f"{a}.numero >= %s"),
                       (p.numero_hasta if numero else None, f"{a}.numero <= %s"),
                       (p.serie if serie else "", f"{a}.serie = %s")):
        if valor not in (None, ""):
            partes.append("AND " + sql)
            params.append(valor)
    if clasif and p.clasificaciones:
        partes.append(f"AND {a}.codclasifica IN ({','.join(['%s'] * len(p.clasificaciones))})")
        params += list(p.clasificaciones)


def _vence(d, pref=""):
    v = _fecha(d.get(f"fechavenc{pref}"))
    if v:
        return v
    f = _fecha(d.get(f"fecmov{pref}"))
    if not f:
        return None
    dias = int(_f(d.get(f"diascr{pref}"))) or int(_f(d.get(f"dias_cr_cxc{pref}")))
    return f + timedelta(days=dias)


def _dias_credito(fecmov, venc):
    """Días de crédito del documento (VFP los contaba contra la fecha de hoy)."""
    f = _fecha(fecmov)
    return (venc - f).days if venc and f else 0


def _monto_doc(r, d):
    return _f(d["valordocml"]) if not _t(d["codmoneda"]) or _t(d["codmoneda"]) == r.moneda_local else _f(d["valorme"])


def _marca_correlativo(filas, docs):
    """Salto de numeración (en rojo) respecto al documento anterior de la misma cuenta, tipo y serie."""
    previo = {}
    for f, d in zip(filas, docs):
        numero = int(_f(d["numero"]))
        if not numero:
            continue
        clave = (_t(d.get("codctachq")), _t(d.get("codtipo")), _t(d["serie"]))
        if clave in previo and numero != previo[clave] + 1:
            f.alerta = True
        previo[clave] = numero


def correlativo_recibos(p):
    """
    con_correlativo_recibos: cada recibo con los documentos que tiene aplicados (en ambos sentidos
    de transacciones_aplica), el saldo al descubierto, diferencial cambiario y días de crédito.
    """
    r = _Chq(p, ("CR",), cuenta_obligatoria=False)
    partes, params = [], []
    _filtros_doc(p, partes, params)
    if p.codctachq:
        partes.append("AND t.codctachq = %s")
        params.append(p.codctachq)
    docs = consulta(
        f"""SELECT t.*, IFNULL(g.razonsocial, '') AS razonsocial, g.dias_cr_cxc FROM transacciones t
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.fecmov BETWEEN %s AND %s {' '.join(partes)}
             ORDER BY t.numero, t.keytra""", (p.codempresa, SISTEMA, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    aplicados = defaultdict(list)
    for bloque in _lotes(d["keytra"] for d in docs):
        marcas = ",".join(["%s"] * len(bloque))
        for a in consulta(
                f"""SELECT a.keytra_apl AS recibo, a.montodocml, a.montome, a.difcambiario_ml, a.codmoneda_apl,
                           t1.fecmov AS fecmov_1, t1.coddirectorio AS coddirectorio_1, t1.nombre AS nombre_1,
                           t1.numero AS numero_1, t1.codtipo AS codtipo_1, t1.serie AS serie_1, t1.fechavenc AS fechavenc_1,
                           t1.diascr AS diascr_1, t1.descontable AS descontable_1, g1.dias_cr_cxc AS dias_cr_cxc_1
                      FROM transacciones_aplica a
                      LEFT JOIN transacciones t1 ON t1.keytra = a.keytra
                      LEFT JOIN grudirectorio g1 ON g1.codgrupo = t1.codgrupo AND g1.coddirectorio = t1.coddirectorio
                     WHERE a.codempresa = %s AND a.keytra_apl IN ({marcas})
                    UNION ALL
                    SELECT a.keytra AS recibo, a.montodocml, a.montome, a.difcambiario_ml, a.codmoneda,
                           t1.fecmov, t1.coddirectorio, t1.nombre, t1.numero, t1.codtipo, t1.serie, t1.fechavenc,
                           t1.diascr, t1.descontable, g1.dias_cr_cxc
                      FROM transacciones_aplica a
                      JOIN transacciones t0 ON t0.keytra = a.keytra AND t0.cuenta_aux <> ''
                      LEFT JOIN transacciones t1 ON t1.keytra = a.keytra_apl
                      LEFT JOIN grudirectorio g1 ON g1.codgrupo = t1.codgrupo AND g1.coddirectorio = t1.coddirectorio
                     WHERE a.codempresa = %s AND a.keytra IN ({marcas})""",
                (p.codempresa, *bloque, p.codempresa, *bloque)):
            if a["numero_1"]:
                aplicados[a["recibo"]].append(a)
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO/DOC", ancho=7, texto=True),
            Columna("SERIE", ancho=10, texto=True), Columna("NÚMERO", ancho=12, texto=True),
            Columna("MONEDA", ancho=7, texto=True), Columna("VALOR"), Columna("VALOR APLICADO"),
            Columna("SALDO AL DESCUBIERTO"), Columna("DIFERENCIAL CAMBIARIO"), Columna("CONCEPTO", ancho=50, texto=True),
            Columna("FECHA VENCIMIENTO", ancho=11, texto=True), Columna("DÍAS CRÉDITO", ancho=8, entero=True),
            Columna("FECHA DE INGRESO", ancho=18, texto=True)]
    n = len(cols)
    filas, cabeceras, totales = [], [], []
    for d in docs:
        local = not _t(d["codmoneda"]) or _t(d["codmoneda"]) == r.moneda_local
        v = [None] * n
        v[0], v[1], v[2], v[3] = _t(d["nombre"]), _t(d["codtipo"]), _t(d["serie"]), _num(d["numero"])
        v[4], v[5] = _t(d["codmoneda"]) or r.moneda_local, _monto_doc(r, d)
        v[9] = _concepto(d)
        venc = _vence(d)
        v[10], v[11] = _txt_fecha(venc), _dias_credito(d["fecmov"], venc)
        v[12] = str(d.get("fecusr") or "")
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                   nombre=_t(d["coddirectorio"]), valores=v)
        filas.append(cab)
        cabeceras.append(cab)
        bloque = []
        for a in aplicados.get(d["keytra"], []):
            w = [None] * n
            w[0], w[1], w[2], w[3] = _t(a["nombre_1"]), _t(a["codtipo_1"]), _t(a["serie_1"]), _num(a["numero_1"])
            w[4] = _t(a["codmoneda_apl"]) or r.moneda_local
            w[6] = _f(a["montodocml"] if local else a["montome"])
            w[8], w[9] = _f(a["difcambiario_ml"]), " ".join(_t(a["descontable_1"])[:200].split())
            venc = _vence(a, "_1")
            w[10], w[11] = _txt_fecha(venc), _dias_credito(a["fecmov_1"], venc)
            bloque.append(Fila(tipo="cuenta", nivel=2, cuenta=_txt_fecha(a["fecmov_1"]),
                               nombre=_t(a["coddirectorio_1"]), valores=w))
        if not bloque and _t(d.get("cuenta_aux")):
            w = [None] * n
            w[0] = "***Pendiente de Aplicar***"
            bloque.append(Fila(tipo="cuenta", nivel=2, valores=w))
        filas += bloque
        if _t(d.get("cuenta_aux")):
            sub = Fila(tipo="total", nombre="SUBTOTAL", valores=[None] * n)
            sub.valores[5] = v[5]
            sub.valores[6] = round(sum(b.valores[6] or 0 for b in bloque), 2)
            sub.valores[7] = round(sub.valores[5] - sub.valores[6], 2)
            sub.formulas = {5: ("refs", [(1, cab, 5)]), 7: ("resta", 5, 6)}
            if bloque:
                sub.formulas[6] = ("subtotal", bloque[0], bloque[-1])
            filas.append(sub)
            totales.append(sub)
        filas.append(Fila())
    _marca_correlativo(cabeceras, docs)
    gran = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n)
    gran.valores[5] = round(sum(c.valores[5] or 0 for c in cabeceras), 2)
    gran.valores[6] = round(sum(t.valores[6] or 0 for t in totales), 2)
    gran.valores[7] = round(sum(t.valores[7] or 0 for t in totales), 2)
    filas.append(gran)
    rep = r.reporte("CORRELATIVO DE RECIBOS APLICADOS", r.periodo(), cols, filas, "Recibos")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    if not p.mostrar_adicionales:
        rep.columnas_ocultas = {12}
    return rep


REGISTROS = {
    "todos": ("Todos", ""),
    "actualizados": ("Actualizados", "AND t.actualizadocnt = 1"),
    "desactualizados": ("Desactualizados", "AND t.actualizadocnt = 0 AND t.usacnt = 1"),
    "anulados": ("Anulados", "AND t.anulado = 1"),
    "contables": ("Contables", "AND t.usacnt = 1"),
    "no_contables": ("No contables", "AND t.usacnt = 0"),
}


def correlativo_general(p):
    """
    con_empdoctos_correl: documentos del sistema por tipo (o por serie) con cargo / abono en M.L. y
    M.E., saltos de numeración en rojo y subtotal por grupo.
    """
    r = _Chq(p, cuenta_obligatoria=False)
    partes, params = [], []
    for valor, sql in ((p.codctachq, "t.codctachq = %s"), (p.tipo_desde, "t.codtipo >= %s"),
                       (p.tipo_hasta, "t.codtipo <= %s"), (p.serie_desde, "t.serie >= %s"),
                       (p.serie_hasta, "t.serie <= %s"), (p.directorio_desde, "t.coddirectorio >= %s"),
                       (p.directorio_hasta, "t.coddirectorio <= %s"), (p.docref, "t.docref = %s")):
        if valor:
            partes.append("AND " + sql)
            params.append(valor)
    if p.clasificaciones:
        partes.append(f"AND t.codclasifica IN ({','.join(['%s'] * len(p.clasificaciones))})")
        params += list(p.clasificaciones)
    if p.codmoneda and p.codmoneda != r.moneda_local:
        partes.append("AND t.codmoneda = %s")
        params.append(p.codmoneda)
    if p.solo_sin_numero:
        partes.append("AND t.numero = 0")
    else:
        for valor, sql in ((p.numero_desde, "t.numero >= %s"), (p.numero_hasta, "t.numero <= %s")):
            if valor not in (None, ""):
                partes.append("AND " + sql)
                params.append(valor)
    if p.valor_doc:
        partes.append(f"AND t.{'valorme' if p.codmoneda and p.codmoneda != r.moneda_local else 'valordocml'} = %s")
        params.append(p.valor_doc)
    partes.append(REGISTROS.get(p.estado_reg, REGISTROS["todos"])[1])
    orden = "t.codtipo, t.serie, t.numero, t.fecmov" if p.orden_fac != 2 else "t.serie, t.numero, t.codtipo, t.fecmov"
    docs = consulta(
        f"""SELECT t.*, IFNULL(g.razonsocial, '') AS nombre_dir FROM transacciones t
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.fecmov BETWEEN %s AND %s {' '.join(partes)}
             ORDER BY t.codsistema, {orden}""", (p.codempresa, SISTEMA, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO", ancho=7, texto=True),
            Columna("SERIE", ancho=10, texto=True), Columna("NÚMERO", ancho=12, texto=True),
            Columna("MON", ancho=5, texto=True), Columna("CARGO", "M. LOCAL"), Columna("ABONO", "M. LOCAL"),
            Columna("CARGO", "M. EXTRANJERA"), Columna("ABONO", "M. EXTRANJERA"), Columna("TASA C.", ancho=9),
            Columna("CNT", ancho=5, texto=True), Columna("ACT", ancho=5, texto=True),
            Columna("DOC. REF.", ancho=14, texto=True), Columna("SUCURSAL", ancho=8, texto=True), Columna("IVA"),
            Columna("SIS.", ancho=5, texto=True), Columna("LLAVE INTERNA", ancho=26, texto=True),
            Columna("CONCEPTO", ancho=50, texto=True), Columna("CREADO POR", ancho=30, texto=True)]
    n = len(cols)
    filas = []
    grupos = defaultdict(list)
    for d in docs:
        grupos[_t(d["codtipo"]) if p.orden_fac != 2 else _t(d["serie"])].append(d)
    for clave, lista in grupos.items():
        filas.append(Fila(tipo="grupo", cuenta=clave))
        bloque = []
        for d in lista:
            v = [None] * n
            debe = _t(d["deha"]) == "D"
            ml, me = _f(d["valordocml"]), _f(d["valorme"])
            v[0] = "*** A N U L A D O ***" if d["anulado"] == 1 else _t(d["nombre_dir"])
            v[1], v[2], v[3], v[4] = _t(d["codtipo"]), _t(d["serie"]), _num(d["numero"]), _t(d["codmoneda"])
            v[5], v[6] = (ml or None, None) if debe else (None, ml or None)
            v[7], v[8] = (me or None, None) if debe else (None, me or None)
            v[9] = _f(d["tasac"])
            v[10], v[11] = ("X" if d["usacnt"] == 1 else ""), ("X" if d["actualizadocnt"] == 1 else "")
            v[12], v[13], v[14] = _t(d["docref"]), _t(d.get("sucursal")), _f(d["iva"])
            v[15], v[16], v[17], v[18] = _t(d["codsistema"]), _t(d["keytra"]), _concepto(d, 300), _t(d.get("creusr"))
            bloque.append(Fila(tipo="cuenta", nivel=1, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                               nombre=_t(d["coddirectorio"]), valores=v))
        _marca_correlativo(bloque, lista)
        sub = Fila(tipo="total", negrita=True, nombre=f"TOTAL {clave}", rango=(bloque[0], bloque[-1]),
                   valores=[None] * n)
        for k in (5, 6, 7, 8):
            sub.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
        filas += bloque + [sub, Fila()]
    rep = r.reporte("CORRELATIVO DE DOCUMENTOS", f"[{SISTEMA}] · {r.periodo()} · "
                    f"{REGISTROS.get(p.estado_reg, REGISTROS['todos'])[0].upper()}", cols, filas, "Correlativo")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    rep.grupos_columnas = [(5, 6, "M. LOCAL"), (7, 8, "M. EXTRANJERA")]
    ocultas = {15, 13}
    if not p.mostrar_adicionales:
        ocultas |= {10, 11, 12, 14}
    if not p.mostrar_creacion:
        ocultas.add(18)
    rep.columnas_ocultas = ocultas
    return rep


def movimiento_recibos(p):
    """
    con_recibos_aplicados (Movimiento de recibos): cada recibo de la caja con los documentos que
    tiene aplicados, valor aplicado y saldo al descubierto. La cadena de liquidaciones de agencia
    (LIQ -> facturas del proveedor -> pagos) no se migró: no hay liquidaciones en la base.
    """
    r = _Chq(p, ("CR",))
    partes, params = [], []
    _filtros_doc(p, partes, params, tipo=False, numero=False, serie=False)
    docs = consulta(
        f"""SELECT t.* FROM transacciones t
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.codctachq = %s AND t.fecmov BETWEEN %s AND %s
                   AND t.numero <> 0 AND t.anulado = 0 {' '.join(partes)}
             ORDER BY t.numerodoc, t.keytra""",
        (p.codempresa, SISTEMA, p.codctachq, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    apl = defaultdict(list)
    for bloque in _lotes(d["keytra"] for d in docs):
        for a in consulta(
                f"""SELECT DISTINCT a.keytra_apl AS recibo, a.keytra, a.montodocml, a.montome, t1.fecmov, t1.codtipo,
                           t1.serie, t1.numero, t1.codmoneda, t1.nombre, t1.codsistema
                      FROM transacciones_aplica a LEFT JOIN transacciones t1 ON t1.keytra = a.keytra
                     WHERE a.codempresa = %s AND a.keytra_apl IN ({','.join(['%s'] * len(bloque))})""",
                (p.codempresa, *bloque)):
            apl[a["recibo"]].append(a)
    cols = [Columna("DOCUMENTO", ancho=8, texto=True), Columna("SERIE", ancho=10, texto=True),
            Columna("NÚMERO", ancho=14, texto=True), Columna("DESCRIPCIÓN", ancho=40, texto=True),
            Columna("MONEDA", ancho=7, texto=True), Columna("VALOR DEL RECIBO"), Columna("VALOR APLICADO"),
            Columna("FACTURAS"), Columna("SALDO AL DESCUBIERTO"), Columna("INGRESO AGENCIA")]
    n = len(cols)
    filas, cabeceras = [], []
    for d in docs:
        local = not _t(d["codmoneda"]) or _t(d["codmoneda"]) == r.moneda_local
        v = [None] * n
        v[0], v[1], v[2], v[3] = _t(d["codtipo"]), _t(d["serie"]), _num(d["numero"]).zfill(10), _t(d["nombre"])
        v[4], v[5] = _t(d["codmoneda"]) or r.moneda_local, _monto_doc(r, d)
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]), valores=v)
        bloque = []
        for a in apl.get(d["keytra"], []):
            w = [None] * n
            monto = _f(a["montodocml"] if local else a["montome"])
            w[0], w[1], w[2], w[4] = _t(a["codtipo"]), _t(a["serie"]), _num(a["numero"]).zfill(12), \
                _t(a["codmoneda"]) or r.moneda_local
            w[3] = _t(a["nombre"])
            w[6], w[7], w[9] = monto, monto, monto
            bloque.append(Fila(tipo="cuenta", nivel=2, cuenta=_txt_fecha(a["fecmov"]), valores=w))
        for k in (6, 7, 9):
            v[k] = round(sum(b.valores[k] for b in bloque), 2)
        v[8] = round(v[5] - v[6], 2)
        if bloque:
            cab.formulas = {8: ("resta", 5, 6)}
        filas += [cab] + bloque
        cabeceras.append(cab)
    gran = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n)
    for k in (5, 6, 7, 8, 9):
        gran.valores[k] = round(sum(c.valores[k] or 0 for c in cabeceras), 2)
    filas.append(gran)
    rep = r.reporte("MOVIMIENTO DE RECIBOS", f"DOCUMENTOS APLICADOS · {r.periodo()} · {r.nombre_cuenta()}",
                    cols, filas, "Recibos")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("", 2)
    return rep


def depositos_aplicados(p):
    """
    con_depositos_aplicados_recibos: depósitos (clave DP) cuya cuenta auxiliar es de caja / banco,
    con los recibos que tienen aplicados (transacciones_doc) o «NO APLICADO».
    """
    r = _Chq(p, ("DM", "AH"), cuenta_obligatoria=False)
    partes, params = [], []
    _filtros_doc(p, partes, params, serie=False)
    if p.codctachq:
        partes.append("AND t.codctachq = %s")
        params.append(p.codctachq)
    if p.solo_pendientes:
        partes.append("AND NOT EXISTS (SELECT 1 FROM transacciones_doc x WHERE x.codempresa = t.codempresa "
                      "AND x.keytra_apl = t.keytra)")
    docs = consulta(
        f"""SELECT DISTINCT t.*, g.dias_cr_cxc FROM transacciones t
              JOIN comtipodoctos com ON com.codsistema = t.codsistema AND com.codtipodoctos = t.codtipodoctos
              JOIN chqdetctanomencla cc ON cc.codempresa = t.codempresa AND cc.codcuenta = t.cuenta_aux
              LEFT JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE t.anulado = 0 AND t.codempresa = %s AND t.codsistema = %s AND com.codclavedoc = 'DP'
                   AND t.fecmov BETWEEN %s AND %s {' '.join(partes)}
             ORDER BY t.numero, t.keytra""", (p.codempresa, SISTEMA, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    recibos = defaultdict(list)
    for bloque in _lotes(d["keytra"] for d in docs):
        for a in consulta(
                f"""SELECT d.keytra_apl AS deposito, d.montodocml, d.montome, d.difcambiario_ml, d.codmoneda_apl,
                           t1.fecmov, t1.coddirectorio, t1.nombre, t1.numero, t1.codtipo, t1.serie, t1.fechavenc,
                           t1.diascr, t1.descontable, g1.dias_cr_cxc
                      FROM transacciones_doc d
                      LEFT JOIN transacciones t1 ON t1.keytra = d.keytra
                      LEFT JOIN grudirectorio g1 ON g1.codgrupo = t1.codgrupo AND g1.coddirectorio = t1.coddirectorio
                     WHERE d.codempresa = %s AND d.keytra_apl IN ({','.join(['%s'] * len(bloque))})""",
                (p.codempresa, *bloque)):
            recibos[a["deposito"]].append(a)
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("TIPO/DOC", ancho=7, texto=True),
            Columna("SERIE", ancho=10, texto=True), Columna("NÚMERO", ancho=12, texto=True),
            Columna("MONEDA", ancho=7, texto=True), Columna("VALOR"), Columna("VALOR APLICADO"),
            Columna("SALDO AL DESCUBIERTO"), Columna("DIFERENCIAL CAMBIARIO"), Columna("CONCEPTO", ancho=50, texto=True),
            Columna("FECHA VENCIMIENTO", ancho=11, texto=True), Columna("DÍAS CRÉDITO", ancho=8, entero=True)]
    n = len(cols)
    filas, cabeceras = [], []
    for d in docs:
        local = not _t(d["codmoneda"]) or _t(d["codmoneda"]) == r.moneda_local
        v = [None] * n
        v[0], v[1], v[2], v[3] = _t(d["nombre"]), _t(d["codtipo"]), _t(d["serie"]), _num(d["numero"])
        v[4], v[5], v[9] = _t(d["codmoneda"]) or r.moneda_local, _monto_doc(r, d), _concepto(d)
        venc = _vence(d)
        v[10], v[11] = _txt_fecha(venc), _dias_credito(d["fecmov"], venc)
        cab = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=d["keytra"], cuenta=_txt_fecha(d["fecmov"]),
                   nombre=_t(d["coddirectorio"]), valores=v)
        bloque = []
        for a in recibos.get(d["keytra"], []) or [None]:
            w = [None] * n
            if a is None or not a["numero"]:
                w[0] = "**** NO APLICADO ****"
            else:
                w[0], w[1], w[2], w[3] = _t(a["nombre"]), _t(a["codtipo"]), _t(a["serie"]), _num(a["numero"])
                w[4] = _t(a["codmoneda_apl"]) or r.moneda_local
                w[6], w[8] = _f(a["montodocml"] if local else a["montome"]), _f(a["difcambiario_ml"])
                w[9] = " ".join(_t(a["descontable"])[:200].split())
                venc = _vence(a)
                w[10], w[11] = _txt_fecha(venc), _dias_credito(a["fecmov"], venc)
            bloque.append(Fila(tipo="cuenta", nivel=2, cuenta=_txt_fecha(a["fecmov"]) if a else "",
                               nombre=_t(a["coddirectorio"]) if a else "", valores=w))
        v[6] = round(sum(b.valores[6] or 0 for b in bloque), 2)
        v[7] = round(v[5] - v[6], 2)
        filas += [cab] + bloque
        cabeceras.append(cab)
    gran = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n)
    for k in (5, 6, 7):
        gran.valores[k] = round(sum(c.valores[k] or 0 for c in cabeceras), 2)
    filas.append(gran)
    rep = r.reporte("DEPÓSITOS APLICADOS A RECIBOS", r.periodo() + (" · PENDIENTES DE APLICAR" if p.solo_pendientes else ""),
                    cols, filas, "Depositos")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("DIRECTORIO", 14)
    return rep


def recibos_depositos(p):
    """
    con_correlativo_recibos_depositos: recibos de la caja con el depósito en que se aplicaron (en
    ambos sentidos de transacciones_doc) y, con «detalle», las formas de pago de cada recibo.
    """
    r = _Chq(p, ("CR",))
    partes, params = [], []
    _filtros_doc(p, partes, params, clasif=False)
    docs = consulta(
        f"""SELECT t.*, IFNULL(g.razonsocial, '') AS razonsocial FROM transacciones t
              JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
             WHERE t.codempresa = %s AND t.codsistema = %s AND t.codctachq = %s AND t.fecmov BETWEEN %s AND %s
                   {' '.join(partes)}
             ORDER BY t.serie, t.numero, t.fecmov, t.coddirectorio""",
        (p.codempresa, SISTEMA, p.codctachq, r.fecha_ini, r.fecha_fin, *params))
    _sin_datos(docs)
    deps, lineas = defaultdict(list), defaultdict(list)
    for bloque in _lotes(d["keytra"] for d in docs):
        marcas = ",".join(["%s"] * len(bloque))
        for a in consulta(
                f"""SELECT d.keytra AS recibo, t.fecmov, t.numero, t.codctachq, t.usacnt, t.actualizadocnt,
                           d.codmoneda_apl, d.montodocml, d.montome
                      FROM transacciones_doc d LEFT JOIN transacciones t ON t.keytra = d.keytra_apl
                     WHERE d.keytra IN ({marcas})
                    UNION ALL
                    SELECT d.keytra_apl, t.fecmov, t.numero, t.codctachq, t.usacnt, t.actualizadocnt,
                           d.codmoneda_apl, d.montodocml, d.montome
                      FROM transacciones_doc d JOIN transacciones t ON t.keytra = d.keytra
                     WHERE d.keytra_apl IN ({marcas})""", (*bloque, *bloque)):
            deps[a["recibo"]].append(a)
        if p.detallado:
            for li in consulta(
                    f"""SELECT keytra, codmoneda, forma_de_pago_rc, banco, no_documento_rc, total, totalme, creusr
                          FROM transacciones_linea WHERE keytra IN ({marcas}) ORDER BY keytra, linea""", bloque):
                lineas[li["keytra"]].append(li)
    cols = [Columna("NOMBRE", ancho=36, texto=True), Columna("DOC. REFERENCIA", ancho=14, texto=True),
            Columna("SERIE", ancho=12, texto=True), Columna("NÚMERO", ancho=12, texto=True),
            Columna("MON", ancho=5, texto=True), Columna("VALOR M.L."), Columna("VALOR M.E."),
            Columna("CNT", ancho=4, texto=True), Columna("ACT", ancho=4, texto=True),
            Columna("FECHA", "DEPÓSITO", ancho=11, texto=True), Columna("NÚMERO", "DEPÓSITO", ancho=12, texto=True),
            Columna("MON", "DEPÓSITO", ancho=5, texto=True), Columna("VALOR M.L.", "DEPÓSITO"),
            Columna("VALOR M.E.", "DEPÓSITO"), Columna("CNT", "DEPÓSITO", ancho=4, texto=True),
            Columna("ACT", "DEPÓSITO", ancho=4, texto=True), Columna("DEPOSITADO EN", ancho=12, texto=True),
            Columna("TIPO DOC PAGO", "DETALLE", ancho=10, texto=True), Columna("BANCO", "DETALLE", ancho=14, texto=True),
            Columna("NÚMERO", "DETALLE", ancho=14, texto=True), Columna("MON", "DETALLE", ancho=5, texto=True),
            Columna("MONTO M.L.", "DETALLE"), Columna("MONTO M.E.", "DETALLE"), Columna("RETENCIONES", "DETALLE"),
            Columna("OPERADO POR", ancho=30, texto=True)]
    n = len(cols)
    filas = []
    for d in docs:
        local = not _t(d["codmoneda"]) or _t(d["codmoneda"]) == r.moneda_local
        dep = deps.get(d["keytra"], [])
        lin = lineas.get(d["keytra"], [])
        alto = max(1, len(dep), len(lin))
        renglones = [[None] * n for _ in range(alto)]
        v = renglones[0]
        v[0], v[1], v[2], v[3] = _t(d["nombre"]), _t(d["docref"]), f"{_t(d['codtipo'])} {_t(d['serie'])}".strip(), \
            _num(d["numero"])
        v[4] = _t(d["codmoneda"]) or r.moneda_local
        if local:
            v[5] = _f(d["valordocml"])
        else:
            v[6] = _f(d["valorme"])
        v[7], v[8] = ("SI" if d["usacnt"] == 1 else "NO"), ("SI" if d["actualizadocnt"] == 1 else "NO")
        for i, a in enumerate(dep):
            w = renglones[i]
            w[9], w[10], w[11] = _txt_fecha(a["fecmov"]), _num(a["numero"]), _t(a["codmoneda_apl"]) or r.moneda_local
            if not _t(a["codmoneda_apl"]) or _t(a["codmoneda_apl"]) == r.moneda_local:
                w[12] = _f(a["montodocml"])
            else:
                w[13] = _f(a["montome"])
            w[14], w[15] = ("SI" if a["usacnt"] == 1 else "NO"), ("SI" if a["actualizadocnt"] == 1 else "NO")
            w[16] = _t(a["codctachq"])
        for i, li in enumerate(lin):
            w = renglones[i]
            w[17], w[18], w[19], w[20] = _t(li["forma_de_pago_rc"]), _t(li["banco"]), _t(li["no_documento_rc"]), \
                _t(li["codmoneda"])
            if _t(li["forma_de_pago_rc"]) in ("RET", "RI", "RIV"):
                w[23] = _f(li["total"])
            elif not _t(li["codmoneda"]) or _t(li["codmoneda"]) == r.moneda_local:
                w[21] = _f(li["total"])
            else:
                w[22] = _f(li["totalme"])
            w[24] = _t(li["creusr"])
        for i, w in enumerate(renglones):
            filas.append(Fila(tipo="cuenta", nivel=1 if i == 0 else 2, codigo=d["keytra"] if i == 0 else "",
                              cuenta=_txt_fecha(d["fecmov"]) if i == 0 else "", valores=w))
    cab = [f for f in filas if f.codigo]
    _marca_correlativo(cab, docs)
    gran = Fila(tipo="total", negrita=True, nombre="TOTALES", valores=[None] * n)
    for k in (5, 6, 12, 13, 21, 22, 23):
        gran.valores[k] = round(sum(f.valores[k] or 0 for f in filas), 2)
    filas.append(gran)
    rep = r.reporte("CORRELATIVO DE RECIBOS APLICADOS A DEPÓSITOS", f"{r.periodo()} · {r.nombre_cuenta()}",
                    cols, filas, "Recibos")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("", 2)
    rep.grupos_columnas = [(9, 16, "DEPÓSITO"), (17, 23, "DETALLE DEL PAGO")]
    if not p.detallado:
        rep.columnas_ocultas = set(range(17, 25))
    return rep


# ------------------------------------------------------------------ Origen y aplicación de fondos

MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")


def origen_aplicacion(p):
    """
    con_origen_aplicacion_fondos: saldo inicial de cada mes del año, entradas (cargos) y salidas
    (abonos) por clasificación y saldo final, de las cuentas del tipo con usa_flujo_fondos. Montos
    en M.E. convertidos con la tasa indicada. Como el estado de cuenta, solo documentos con número
    salvo que se pida incluir los pendientes de número. Una consulta de saldo previo y una de movimientos del
    año (VFP: 24 consultas de saldo + 2 de movimientos).
    """
    r = _Chq(p, cuenta_obligatoria=False)
    anio = p.anio
    ini, fin = date(anio, 1, 1), date(anio, 12, 31)
    tasa = float(p.tasa_cambio or 0) or tasa_cambio(p.codempresa, fin) or 1.0
    monto = f"IF(c.codmoneda <> %s, t.valorme * {tasa:.8f}, t.valordocml)"
    filtro, params = "c.codempresa = %s AND c.codtipocta = %s AND c.usa_flujo_fondos = 1 AND c.feciniopera <= %s", \
        [p.codempresa, p.codtipocta, fin]
    if p.codmoneda:
        filtro += " AND c.codmoneda = %s"
        params.append(p.codmoneda)
    numerados = "" if p.incluye_pend_numero else " AND t.numero > 0"
    previo = consulta_uno(
        f"""SELECT IFNULL(SUM(IF(c.codmoneda <> %s, c.saldoiniconta * {tasa:.8f}, c.saldoiniconta)), 0) AS inicial,
                   (SELECT IFNULL(SUM(IF(t.deha = 'D', 1, -1) * {monto}), 0)
                      FROM transacciones t JOIN chqdefctas c ON c.codempresa = t.codempresa AND c.codctachq = t.codctachq
                     WHERE {filtro} AND t.codsistema = %s AND t.fecmov >= c.feciniopera AND t.fecmov < %s{numerados}) AS movs
              FROM chqdefctas c WHERE {filtro}""",
        (r.moneda_local, r.moneda_local, *params, SISTEMA, ini, *params))
    movs = consulta(
        f"""SELECT t.deha, IFNULL(t.codclasifica, '') AS codclasifica, MONTH(t.fecmov) AS mes,
                   SUM({monto}) AS monto
              FROM transacciones t JOIN chqdefctas c ON c.codempresa = t.codempresa AND c.codctachq = t.codctachq
             WHERE {filtro} AND t.codsistema = %s AND t.fecmov >= c.feciniopera AND t.fecmov BETWEEN %s AND %s{numerados}
             GROUP BY t.deha, t.codclasifica, MONTH(t.fecmov)""",
        (r.moneda_local, *params, SISTEMA, ini, fin))
    if not movs and not _f(previo["inicial"]) and not _f(previo["movs"]):
        _sin_datos([])
    nombres = {_t(c["codclasifica"]): _t(c["descripcion"]) for c in consulta(
        """SELECT codclasifica, descripcion FROM gruclasificaciones
            WHERE codgrupo = (SELECT codgrupo FROM empresas WHERE codempresa = %s)""", (p.codempresa,))}
    lados = {"D": defaultdict(lambda: [0.0] * 12), "H": defaultdict(lambda: [0.0] * 12)}
    for m in movs:
        lados[_t(m["deha"])][_t(m["codclasifica"])][m["mes"] - 1] += _f(m["monto"])
    neto = [sum(v[i] for v in lados["D"].values()) - sum(v[i] for v in lados["H"].values()) for i in range(12)]
    saldo = _f(previo["inicial"]) + _f(previo["movs"])
    iniciales = []
    for i in range(12):
        iniciales.append(round(saldo, 2))
        saldo += neto[i]

    cols = [Columna(m) for m in MESES] + [Columna("TOTAL M.L."), Columna("TOTAL M.E."),
                                          Columna("%", porcentaje=True, ancho=9)]
    n = len(cols)
    fila_ini = Fila(tipo="cuenta", nivel=1, negrita=True, nombre="SALDO INICIAL", valores=iniciales + [None] * 3)
    fila_ini.valores[12] = iniciales[0]
    fila_ini.valores[13] = round(iniciales[0] / tasa, 2)
    fila_ini.formulas = {12: ("refs", [(1, fila_ini, 0)])}
    filas = [fila_ini, Fila()]
    secciones = []
    for lado, titulo in (("D", "ENTRADAS"), ("H", "SALIDAS")):
        datos = lados[lado]
        total_lado = sum(sum(v) for v in datos.values())
        bloque = []
        for clasif in sorted(datos):
            v = [round(x, 2) for x in datos[clasif]]
            tot = round(sum(v), 2)
            v += [tot, round(tot / tasa, 2), tot / total_lado if total_lado else 0]
            bloque.append(Fila(tipo="cuenta", nivel=2, cuenta=clasif or "(SIN CLASIF.)",
                               nombre=nombres.get(clasif, "Sin clasificación" if not clasif else ""), valores=v,
                               formulas={12: ("suma", 0, 11)}))
        tot = Fila(tipo="total", nivel=1, negrita=True, nombre=titulo, valores=[None] * n)
        for k in range(14):
            tot.valores[k] = round(sum(b.valores[k] for b in bloque), 2)
        tot.valores[14] = 1.0 if bloque else 0
        if bloque:
            tot.rango = (bloque[0], bloque[-1])
        filas += [tot] + bloque + [Fila()]
        secciones.append(tot)
    entradas, salidas = secciones
    final = Fila(tipo="total", nivel=1, negrita=True, nombre="SALDO FINAL", valores=[None] * n)
    final.formulas = {}
    for k in range(14):
        final.valores[k] = round(fila_ini.valores[k] + entradas.valores[k] - salidas.valores[k], 2)
        final.formulas[k] = ("refs", [(1, fila_ini, k), (1, entradas, k), (-1, salidas, k)])
    filas.append(final)
    rep = r.reporte("ORIGEN Y APLICACIÓN DE FONDOS",
                    f"CUENTAS [{p.codtipocta}] · AÑO {anio} · TASA DE CAMBIO {tasa:,.5f}"
                    + (f" · MONEDA [{p.codmoneda}]" if p.codmoneda else ""), cols, filas, "Origen y aplicacion")
    rep.encabezado_a, rep.encabezado_b = ("CLASIF.", 12), ("CONCEPTO", 34)
    return rep


# ------------------------------------------------------------------ Flujo de fondos proyectado

DIAS_SEMANA = ("LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO")
MAX_PERIODOS = 60


def _periodos(ini, fin, por, primer_dia):
    """[(título, desde, hasta)] entre ini y fin: semanas que empiezan en `primer_dia` (0 = lunes), meses o años."""
    salida, d = [], ini
    while d <= fin:
        if por == "semana":
            hasta = d + timedelta(days=(primer_dia - 1 - d.weekday()) % 7)
        elif por == "mes":
            siguiente = date(d.year + d.month // 12, d.month % 12 + 1, 1)
            hasta = siguiente - timedelta(days=1)
        else:
            hasta = date(d.year, 12, 31)
        hasta = min(hasta, fin)
        if por == "semana":
            titulo = _txt_fecha(d) if d == hasta else f"{_txt_fecha(d)} AL {_txt_fecha(hasta)}"
        elif por == "mes":
            titulo = f"{MESES[d.month - 1]} {d.year}"
        else:
            titulo = str(d.year)
        salida.append((titulo, d, hasta))
        d = hasta + timedelta(days=1)
        if len(salida) > MAX_PERIODOS:
            raise ErrorReporte(f"El rango genera más de {MAX_PERIODOS} columnas: amplíe el periodo de proyección "
                               "(semana → mes → año) o acorte las fechas.")
    return salida


def flujo_proyectado(p):
    """
    con_flujo_de_fondos_proyectado_xls: saldo de bancos al inicio, más cuentas por cobrar y menos
    cuentas por pagar pendientes repartidas por su fecha a trabajar (pago prometido / entrega +
    crédito / vencimiento) en columnas por semana, mes o año, con provisiones contables y manuales.
    Cada periodo arranca con el saldo final del anterior. Además, antigüedad al final del rango de
    lo proyectado y lo pendiente con fecha anterior al rango.
    """
    from . import ctacte
    from .modelo import Libro
    r = _Chq(p, cuenta_obligatoria=False)
    ini, fin = r.fecha_ini, r.fecha_fin
    if not ini or ini > fin:
        raise ErrorReporte("Indique un rango de fechas válido.")
    periodos = _periodos(ini, fin, p.proyectar_por, p.primer_dia)
    np_ = len(periodos)
    tipo = p.codtipocta or "DM"
    tasa = float(p.tasa_cambio or 0) or tasa_cambio(p.codempresa, ini) or 1.0
    cuentas = _cuentas_flujo(r, tipo, p.codmoneda, ini - timedelta(days=1))
    bancos, total_bancos = _resumen_bancos(r, p, cuentas, ini - timedelta(days=1), tasa,
                                           "FLUJO PROYECTADO - DISPONIBILIDAD INICIAL")

    # columnas: total | periodos | antigüedad (6) | total antigüedad | anterior al rango | total
    a0 = np_ + 1
    k_ant, k_prev, k_tot = a0 + 6, a0 + 7, a0 + 8
    cols = [Columna("TOTAL PROYECTADO")] + [Columna(t) for t, _, _ in periodos] + \
        [Columna(t, "ANTIGÜEDAD") for t in ("CORRIENTE", "01-30", "31-60", "61-90", "91-120", "MÁS DE 120")] + \
        [Columna("TOTAL ANTIGÜEDAD"), Columna("PENDIENTE ANTES DEL RANGO"), Columna("TOTAL PENDIENTE")]
    n = len(cols)

    def columna(fecha):
        for i, (_, d, h) in enumerate(periodos):
            if d <= fecha <= h:
                return 1 + i
        return None

    def antig(venc):
        dias = (fin - venc).days if venc else 0
        for i, tope in enumerate((0, 30, 60, 90, 120)):
            if dias <= tope:
                return a0 + i
        return a0 + 5

    def renglon(cuenta, nombre, movs):
        """movs: [(fecha_trabajar, vencimiento, monto)] -> Fila con periodos, antigüedad y anterior al rango."""
        v = [0.0] * n
        for trabajar, venc, monto in movs:
            if trabajar and trabajar < ini:
                v[k_prev] += monto
                continue
            k = columna(trabajar) if trabajar else None
            if k is None:
                continue
            v[k] += monto
            v[antig(venc)] += monto
        v = [round(x, 2) for x in v]
        v[0] = round(sum(v[1:a0]), 2)
        v[k_ant] = round(sum(v[a0:k_ant]), 2)
        v[k_tot] = round(v[k_ant] + v[k_prev], 2)
        return Fila(tipo="cuenta", nivel=2, cuenta=cuenta, nombre=nombre, valores=[x or None for x in v],
                    formulas={0: ("suma", 1, np_), k_ant: ("suma", a0, k_ant - 1),
                              k_tot: ("lineal", [(1, k_ant), (1, k_prev)])})

    def agrupa(docs):
        grupos = defaultdict(list)
        for d in docs:
            signo = 1 if d["lado"] == "documentos" else -1
            if p.mostrar_por == "clasificacion":
                clave = ("", d["codclasifica"] or "SIN CLASIFICACIÓN")
            elif p.mostrar_por == "aca":
                clave = ("", _t(d["codaca"]) or "VACIO")
            else:
                clave = (d["coddirectorio"], d["razonsocial"])
            grupos[clave].append((d["fecha_trabajar"], d["fechavenc"], signo * d["diferencia"]))
        orden = sorted(grupos, key=lambda c: (c[1], c[0]) if p.mostrar_por == "directorio" else c)
        return [renglon(c, nom, grupos[(c, nom)]) for c, nom in orden]

    filtro = ctacte.FiltroCtaCte(fecha_fin=fin, dir_desde=p.directorio_desde, dir_hasta=p.directorio_hasta)
    secciones = {}
    for sistema, titulo in (("CXC", "INGRESOS"), ("CXP", "EGRESOS")):
        docs = [d for d in ctacte.documentos(p.codempresa, sistema, filtro, solo_pendientes=True) if d["diferencia"]]
        secciones[titulo] = agrupa(docs)

    # Provisiones contables (cuentas de chq_cuentas_provisionff) y manuales, por fecha de movimiento
    prov = consulta(
        """SELECT cp.tipomovimiento, d.codcuenta, n.nomcta, t.fecmov,
                  IF(n.naturalezacta = 'D', SUM(d.cargo) - SUM(d.abono), SUM(d.abono) - SUM(d.cargo)) AS saldo
             FROM dettransacciones d
             JOIN transacciones t ON t.codempresa = d.codempresa AND t.keytra = d.keytra
             JOIN chq_cuentas_provisionff cp ON cp.codempresa = d.codempresa AND cp.codperiodo = d.codperiodo
                  AND cp.codcuenta = d.codcuenta
             JOIN empnomenclatura n ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
            WHERE d.codempresa = %s AND t.fecmov BETWEEN %s AND %s AND t.anulado = 0 AND t.numero > 0
                  AND t.actualizadocnt = 1
            GROUP BY cp.tipomovimiento, d.codcuenta, n.nomcta, n.naturalezacta, t.fecmov
            ORDER BY d.codcuenta""", (p.codempresa, ini, fin))
    manual = consulta(
        """SELECT tipomovimiento, codprovision, LEFT(descripcion, 250) AS descripcion, fecha, valor
             FROM chq_provisiones_manuales WHERE codempresa = %s AND fecha BETWEEN %s AND %s
            ORDER BY codprovision""", (p.codempresa, ini, fin))
    for tipo_mov, sufijo in ((1, "INGRESOS"), (2, "EGRESOS")):
        g = defaultdict(list)
        for f in prov:
            if int(f["tipomovimiento"] or 0) == tipo_mov:
                g[(_t(f["codcuenta"]), _t(f["nomcta"]))].append((_fecha(f["fecmov"]), _fecha(f["fecmov"]), _f(f["saldo"])))
        secciones[f"{sufijo} PROVISIONES"] = [renglon(c, nom, m) for (c, nom), m in g.items()]
        g = defaultdict(list)
        for f in manual:
            if int(f["tipomovimiento"] or 0) == tipo_mov:
                g[(_t(f["codprovision"]), _t(f["descripcion"]))].append((_fecha(f["fecha"]), _fecha(f["fecha"]), _f(f["valor"])))
        secciones[f"{sufijo} PROVISIONES MANUALES"] = [renglon(c, nom, m) for (c, nom), m in g.items()]

    if not any(secciones.values()) and not cuentas:
        _sin_datos([])
    saldo_ini = Fila(tipo="total", nivel=1, negrita=True, nombre="SALDO INICIAL (BANCOS)", valores=[None] * n)
    filas = [saldo_ini, Fila()]
    totales = []
    for titulo in ("INGRESOS", "INGRESOS PROVISIONES", "INGRESOS PROVISIONES MANUALES",
                   "EGRESOS", "EGRESOS PROVISIONES", "EGRESOS PROVISIONES MANUALES"):
        bloque = secciones[titulo]
        if not bloque and "PROVISIONES" in titulo:
            continue                       # sin provisiones configuradas: la sección no aporta
        tot = Fila(tipo="total", nivel=1, negrita=True, nombre=titulo, valores=[None] * n)
        for k in range(n):
            tot.valores[k] = round(sum(b.valores[k] or 0 for b in bloque), 2)
        if bloque:
            tot.rango = (bloque[0], bloque[-1])
        filas += [tot] + bloque + [Fila()]
        totales.append((1 if titulo.startswith("INGRESOS") else -1, tot))
    final = Fila(tipo="total", nivel=1, negrita=True, nombre="SALDO FINAL", valores=[None] * n)
    # saldo inicial de cada periodo = saldo final del anterior; el primero, la disponibilidad en bancos
    saldo = total_bancos
    saldo_ini.valores[0] = round(total_bancos, 2)
    saldo_ini.formulas = {}
    for k in range(1, a0):
        saldo_ini.valores[k] = round(saldo, 2)
        saldo_ini.formulas[k] = ("refs", [(1, saldo_ini, 0)]) if k == 1 else ("refs", [(1, final, k - 1)])
        saldo += sum(s * t.valores[k] for s, t in totales)
    final.formulas = {}
    for k in range(n):
        base = saldo_ini.valores[k] if k < a0 else 0.0
        final.valores[k] = round(base + sum(s * t.valores[k] for s, t in totales), 2)
        final.formulas[k] = ("refs", ([(1, saldo_ini, k)] if k < a0 else []) + [(s, t, k) for s, t in totales])
    filas.append(final)
    por = {"semana": f"SEMANAS (INICIAN EL {DIAS_SEMANA[p.primer_dia]})", "mes": "MESES", "anio": "AÑOS"}[p.proyectar_por]
    rep = r.reporte("FLUJO DE FONDOS PROYECTADO",
                    f"DEL {_txt_fecha(ini)} AL {_txt_fecha(fin)} · POR {por} · CUENTAS [{tipo}] · TASA {tasa:,.5f}",
                    cols, filas, "Flujo proyectado")
    rep.encabezado_a, rep.encabezado_b = ("CÓDIGO", 14), ("DESCRIPCIÓN", 40)
    rep.grupos_columnas = [(1, np_, "PROYECCIÓN"), (a0, k_ant, "ANTIGÜEDAD AL " + _txt_fecha(fin))]
    return Libro(titulo=rep.titulo, hojas=[rep, bancos], nombre_archivo=rep.nombre_archivo)
