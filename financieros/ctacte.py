"""
Saldos de cuenta corriente (CXC / CXP): equivalente a sistema\\prgs\\_saldos_cuenta_corriente.prg.

Cada documento de cuenta corriente (transacciones con usactacorriente C / P) con su saldo a una
fecha: valor del documento menos lo aplicado hasta esa fecha (transacciones_aplica) menos el
diferencial cambiario contabilizado. Dos lados:

  documentos  CXC: CXCMOVDE (facturas, notas de débito)   CXP: CXPMOVHA (facturas de proveedor)
  pagos       CXC: CXCMOVHA (recibos, notas de crédito)   CXP: CXPMOVDE (pagos, anticipos)

Respecto a VFP:
* Las aplicaciones se suman en una subconsulta por documento (en VFP: LEFT JOIN a la vista
  v_transacciones_aplica, que une transacciones dos veces para TODA la base, y GROUP BY keytra
  con columnas no agrupadas, que MySQL 5.7 con ONLY_FULL_GROUP_BY rechaza).
* Una consulta por lado; los filtros van como parámetros (VFP concatenaba texto en el WHERE).
"""
import time
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from core.db import consulta, consulta_uno, fecha_valida

from .contexto import ErrorReporte

LADOS = {
    #        documentos      pagos         columna de diferencial cambiario
    "CXC": ("CXCMOVDE", "CXCMOVHA", "CXCMOVHA"),
    "CXP": ("CXPMOVHA", "CXPMOVDE", "CXPMOVDE"),
}


@dataclass
class FiltroCtaCte:
    fecha_fin: date
    cuenta_aux: str = ""
    dir_desde: str = ""
    dir_hasta: str = ""
    no_contrato: int = 0
    codmoneda: str = ""
    codaca: str = ""
    clasificaciones: list = field(default_factory=list)
    fecmov_desde: date | None = None      # Documentos pendientes: rango de fecha de movimiento
    venc_desde: date | None = None        #   ... o de vencimiento (y entonces sin tope de fecmov)
    venc_hasta: date | None = None
    codtipo: str = ""                     # Detalle por cuenta: tipo de documento

    def sql(self):
        if self.venc_desde or self.venc_hasta:
            partes, params = [], []
            for valor, op in ((self.venc_desde, ">="), (self.venc_hasta, "<=")):
                if valor:
                    partes.append(f"AND t.fechavenc {op} %s")
                    params.append(valor)
        else:
            partes, params = ["AND t.fecmov <= %s"], [self.fecha_fin]
        if self.fecmov_desde:
            partes.append("AND t.fecmov >= %s")
            params.append(self.fecmov_desde)
        if self.codtipo:
            partes.append("AND t.codtipo = %s")
            params.append(self.codtipo)
        for valor, sql in ((self.cuenta_aux, "t.cuenta_aux = %s"), (self.dir_desde, "t.coddirectorio >= %s"),
                           (self.dir_hasta, "t.coddirectorio <= %s"), (self.codmoneda, "t.codmoneda = %s"),
                           (self.codaca, "g.codaca = %s")):
            if valor:
                partes.append("AND " + sql)
                params.append(valor)
        if self.no_contrato:
            partes.append("AND t.no_contrato = %s")
            params.append(str(self.no_contrato))
        if self.clasificaciones:
            partes.append(f"AND t.codclasifica IN ({','.join(['%s'] * len(self.clasificaciones))})")
            params += list(self.clasificaciones)
        return " ".join(partes), params


SQL_DOCUMENTOS = """
SELECT t.idtransac, t.keytra, t.keyref, t.keyaux, t.codsistema, t.filectacorriente, t.deha,
       t.fecmov, t.fecha, t.fechavenc, t.diascr, t.codtipo, t.serie, t.numero, t.numerodoc,
       t.coddirectorio, IF(IFNULL(dc.cuenta_ref, '') = '', t.coddirectorio, dc.cuenta_ref) AS cuenta_dir,
       t.nombre, t.codclasifica, t.docref, t.ocompra, t.codmoneda, t.descontable, t.actualizadocnt, t.usacnt,
       t.cuenta_aux, t.cuenta_ref, t.anulado, t.fechaprometidapago, t.fechaentrega, t.tasac, t.esajuste,
       t.codproceso, t.codvendedor, t.no_contrato,
       t.valordocml AS valordocml_bruto, t.noaplica_ml, t.valorme AS valorme_bruto, t.noaplica, t.valor AS valor_bruto,
       t.dcambiario_conta, e.codmoneda AS moneda_empresa,
       IFNULL(g.razonsocial, '') AS razonsocial, IFNULL(g.nit, '') AS nit, IFNULL(g.codaca, '') AS codaca,
       IFNULL(g.dias_cr_cxc, 0) AS dias_cr_cxc, IFNULL(g.dias_cr_cxp, 0) AS dias_cr_cxp,
       IFNULL(g.limite_cr_cxc, 0) AS limite_cr_cxc, IFNULL(g.direccion, '') AS direccion,
       IFNULL(g.telefonooficina, '') AS telefono, t.sucursal,
       IFNULL(su.nombre, '') AS sucursal_nombre, IFNULL(su.razonsocial, '') AS sucursal_razonsocial,
       IFNULL(a.aplicadoml, 0) AS aplicadoml, IFNULL(a.aplicadoml_abs, 0) AS aplicadoml_abs,
       IFNULL(a.aplicadome, 0) AS aplicadome, a.tran_tasac, a.tran_tasac_apl
  FROM transacciones t
  JOIN empresas e ON e.codgrupo = t.codgrupo AND e.codempresa = t.codempresa
  JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
  LEFT JOIN iva_sucursales su ON su.codempresa = t.codempresa AND su.sucursal = t.sucursal
  LEFT JOIN grudirectorioclasifica dc
    ON dc.codgrupo = t.codgrupo AND dc.coddirectorio = t.coddirectorio AND dc.codclasifica = t.codclasifica
  LEFT JOIN (
        SELECT ta.{propia} AS keytra_doc, o.codclasifica AS clasifica_otra,
               SUM(ta.montodocml) AS aplicadoml, SUM(ABS(ta.montodocml)) AS aplicadoml_abs,
               SUM(ta.montome) AS aplicadome, MAX(ta.tasac) AS tran_tasac, MAX(ta.tasac_apl) AS tran_tasac_apl
          FROM transacciones_aplica ta
          JOIN transacciones o ON o.codempresa = ta.codempresa AND o.keytra = ta.{otra}
         WHERE ta.codempresa = %s AND ta.feckeytra <= %s AND ta.feckeytra_apl <= %s
         GROUP BY ta.{propia}, o.codclasifica
       ) a ON a.keytra_doc = t.keytra AND a.clasifica_otra = t.codclasifica
 WHERE t.codempresa = %s
   AND t.usactacorriente = %s
   AND t.filectacorriente = %s
   AND t.numero <> 0
   AND t.anulado = 0
   AND NOT FIND_IN_SET(TRIM(t.codsistema), 'PFA')
   {filtros}
"""


_parametros_cache = {}


def parametro_general(codempresa, nombre, defecto="0"):
    """
    Tabla `parametros` (los gPG_* de VFP): el de la empresa manda sobre el general. Se guarda 5
    minutos: contra el servidor remoto cada consulta es una ida y vuelta.
    """
    clave = (codempresa, nombre)
    guardado = _parametros_cache.get(clave)
    if guardado and time.monotonic() - guardado[0] < 300:
        return guardado[1]
    filas = consulta("SELECT codempresa, valor FROM parametros WHERE parametro = %s AND codempresa IN ('', %s)",
                     (nombre, codempresa))
    propio = [f["valor"] for f in filas if f["codempresa"] == codempresa]
    general = [f["valor"] for f in filas if not f["codempresa"]]
    valor = (propio or general or [defecto])[0]
    _parametros_cache[clave] = (time.monotonic(), valor)
    return valor


def _fecha(v):
    return fecha_valida(v) if not isinstance(v, date) or v.year > 1900 else None


# Saldo pendiente calculado en la base (misma fórmula que _completa) para traer solo los documentos
# con saldo cuando el reporte no necesita los saldados: en CXC de 003 son ~200 de 25 mil.
SQL_PENDIENTE = """
   AND (ROUND((t.valordocml - t.noaplica_ml)
              - (ROUND(IF(t.esajuste = 1 OR TRIM(t.docref) = 'DIFERENCIAL_CAMBIARIO',
                          IFNULL(a.aplicadoml_abs, 0), IFNULL(a.aplicadoml, 0)), 2)
                 + IF(t.filectacorriente = %s, t.dcambiario_conta, 0)), 2) <> 0
        OR (t.valorme <> 0 AND ROUND(t.valorme - t.noaplica - IFNULL(a.aplicadome, 0), 2) <> 0))"""


def documentos(codempresa, sistema, filtro, lados=("documentos", "pagos"), solo_pendientes=False):
    """
    Lista de documentos (dicts) con saldo al `filtro.fecha_fin`.
    `lados`: documentos, pagos o ambos (VFP: nTipoSaldos 1 = ambos, nPagos_cobros = 1 solo documentos).
    `solo_pendientes`: filtra en la base los documentos sin saldo (diferencia y diferencia M.E. en 0);
    el resultado es el mismo que filtrarlos después, pero viaja una fracción de las filas.
    """
    if sistema not in LADOS:
        raise ErrorReporte("Sistema de cuenta corriente no válido.")
    doc, pago, lado_dc = LADOS[sistema]
    recalcular = parametro_general(codempresa, "recalcular_valordocml_en_estado_de_cuenta_cc") == "1"
    filtros, params = filtro.sql()
    if solo_pendientes:
        filtros += SQL_PENDIENTE
        params = [*params, lado_dc]
    # Los dos lados en una sola consulta (UNION ALL): una ida y vuelta menos contra el servidor
    partes, valores = [], []
    for lado in lados:
        archivo = doc if lado == "documentos" else pago
        propia, otra = ("keytra", "keytra_apl") if lado == "documentos" else ("keytra_apl", "keytra")
        partes.append(f"SELECT '{lado}' AS lado, x.* FROM ({SQL_DOCUMENTOS.format(propia=propia, otra=otra, filtros=filtros)}) x")
        valores += [codempresa, filtro.fecha_fin, filtro.fecha_fin, codempresa, sistema[-1], archivo, *params]
    salida = [_completa(f, sistema, f["lado"], lado_dc, recalcular)
              for f in consulta("\nUNION ALL\n".join(partes), valores)]
    salida.sort(key=lambda d: (d["coddirectorio"], d["razonsocial"], d["fecmov"] or date.min, d["numerodoc"] or ""))
    return salida


SQL_SALDOS_ANTERIORES = """
SELECT t.coddirectorio, MAX(IFNULL(g.razonsocial, '')) AS razonsocial, SUM({termino}) AS saldo
  FROM transacciones t
  JOIN empresas e ON e.codgrupo = t.codgrupo AND e.codempresa = t.codempresa
  JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
 WHERE t.codempresa = %s
   AND t.usactacorriente = %s
   AND t.filectacorriente IN (%s, %s)
   AND t.numero <> 0
   AND t.anulado = 0
   AND NOT FIND_IN_SET(TRIM(t.codsistema), 'PFA')
   {filtros}
 GROUP BY t.coddirectorio
"""


def saldos_anteriores(codempresa, sistema, filtro, antes_de, modo, en_me=False):
    """
    Saldo de cada directorio con los documentos anteriores a `antes_de`, sumado en la base: una
    fila por directorio en lugar de traer todo el historial (25 mil documentos en CXC de 003).
    Mismas columnas y signos que _completa, sin aplicaciones (estos saldos no las usan).

      modo «estado»: Estado de cuenta, cargo / abono según el archivo (invertido si es ajuste)
                     con el signo del sistema, más el diferencial cambiario (salvo en M.E.).
      modo «lado»:   Moras y Detalle por cuenta, valordocml - diferencial, positivo en el lado de
                     los documentos (facturas) y negativo en el de los pagos.
    """
    doc, pago, lado_dc = LADOS[sistema]
    recalcular = parametro_general(codempresa, "recalcular_valordocml_en_estado_de_cuenta_cc") == "1"
    vml = ("IF(%s AND t.valorme > 0 AND t.codmoneda <> e.codmoneda, "
           "ROUND((t.valorme - t.noaplica) * t.tasac + 0.000000000001, 2), ROUND(t.valordocml - t.noaplica_ml, 2))")
    vme = "(t.valorme - IF(t.valorme > 0, t.noaplica, 0))"
    dc = "IF(t.filectacorriente = %s, t.dcambiario_conta, 0)"
    if modo == "estado":
        signo = 1 if sistema == "CXC" else -1
        cargo = "((RIGHT(t.filectacorriente, 2) = 'DE') <> (t.esajuste = 1))"
        valor = vme if en_me else vml
        termino = f"{signo} * IF({cargo}, {valor}, -{valor})" + ("" if en_me else f" + {dc}")
        params_t = [] if en_me else [recalcular, recalcular, lado_dc]     # vml aparece dos veces
    else:
        termino = f"IF(t.filectacorriente = %s, 1, -1) * ({vml} - {dc})"
        params_t = [doc, recalcular, lado_dc]
    previo = replace(filtro, fecha_fin=antes_de - timedelta(days=1), fecmov_desde=None)
    filtros, params = previo.sql()
    salida = {}
    for f in consulta(SQL_SALDOS_ANTERIORES.format(termino=termino, filtros=filtros),
                      (*params_t, codempresa, sistema[-1], doc, pago, *params)):
        salida[f["coddirectorio"]] = {"razonsocial": (f["razonsocial"] or "").strip(),
                                      "saldo": round(float(f["saldo"] or 0), 2)}
    return salida


def documentos_del_rango(codempresa, sistema, filtro, desde, lados=("documentos", "pagos")):
    """Documentos con fecha de movimiento entre `desde` y filtro.fecha_fin (con sus aplicaciones)."""
    return documentos(codempresa, sistema, replace(filtro, fecmov_desde=desde), lados)


def _completa(f, sistema, lado, lado_dc, recalcular):
    """Columnas calculadas de _saldos_cuenta_corriente (diferencia, fechas de vencimiento y de trabajo)."""
    d = dict(f)
    d["lado"] = lado
    d["fecmov"] = _fecha(d["fecmov"])
    vml, nml = float(d["valordocml_bruto"] or 0), float(d["noaplica_ml"] or 0)
    vme, nme = float(d["valorme_bruto"] or 0), float(d["noaplica"] or 0)
    tasac = float(d["tasac"] or 0)
    if recalcular and vme > 0 and d["codmoneda"] != d["moneda_empresa"]:
        d["valordocml"] = round((vme - nme) * tasac + 1e-12, 2)
    else:
        d["valordocml"] = round(vml - nml, 2)
    d["valorme"] = vme - (nme if vme > 0 else 0.0)
    vb = float(d["valor_bruto"] or 0)
    d["valor"] = vb - ((nml if lado == "documentos" else nme) if vb > 0 else 0.0)
    d["dcambiario"] = float(d["dcambiario_conta"] or 0) if d["filectacorriente"] == lado_dc else 0.0
    ajuste = d["esajuste"] == 1 or (d["docref"] or "").strip() == "DIFERENCIAL_CAMBIARIO"
    aplicado = round(float(d["aplicadoml_abs"] if ajuste else d["aplicadoml"]), 2)
    d["aplicadoml"] = round(float(d["aplicadoml"]), 2)
    d["aplicadome"] = round(float(d["aplicadome"]), 2)
    d["diferencia"] = round((vml - nml) - (aplicado + d["dcambiario"]), 2)
    d["diferenciame"] = round(vme - nme - d["aplicadome"], 2) if vme != 0 else 0.0
    d["tasac"] = tasac
    d["tran_tasac"] = float(d["tran_tasac"]) if d["tran_tasac"] is not None else tasac
    d["tran_tasac_apl"] = float(d["tran_tasac_apl"]) if d["tran_tasac_apl"] is not None else tasac
    # Vencimiento y «fecha a trabajar» (pago / cobro prometido), como los REPLACE ALL de VFP
    dias_dir = float(d["dias_cr_cxc"] if sistema == "CXC" else d["dias_cr_cxp"] or 0)
    diascr = int(d["diascr"] or 0)
    venc, entrega, prometida = _fecha(d["fechavenc"]), _fecha(d["fechaentrega"]), _fecha(d["fechaprometidapago"])
    if venc is None and d["fecmov"]:
        venc = d["fecmov"] + timedelta(days=int(diascr or dias_dir))
    trabajar = prometida or (entrega + timedelta(days=diascr) if entrega else None) or venc
    if trabajar is None and d["fecmov"]:
        trabajar = d["fecmov"] + timedelta(days=diascr)
    d["fechavenc"], d["fechaentrega"], d["fechaprometidapago"] = venc, entrega, prometida
    d["fecha_trabajar"] = trabajar or d["fecmov"]
    for k in ("serie", "codtipo", "nombre", "descontable", "docref", "codclasifica", "cuenta_aux", "numerodoc"):
        d[k] = (d[k] or "").strip() if isinstance(d[k], str) else d[k]
    d["numero"] = int(d["numero"] or 0)
    return d


def solo_saldos(docs, codempresa, usa_fecha_trabajar=False, fecha_pago=None):
    """Documentos con saldo pendiente (nMostrarSoloSaldos = 1 en VFP)."""
    negativos = parametro_general(codempresa, "mostrar_valores_negativos_integracion_cuentas") == "1"
    if usa_fecha_trabajar and fecha_pago:
        docs = [d for d in docs if d["fecha_trabajar"] and d["fecha_trabajar"] <= fecha_pago]
    salida = []
    for d in docs:
        local = d["codmoneda"] == d["moneda_empresa"]
        if negativos:
            ok = d["diferencia"] != 0 or (d["diferenciame"] != 0 and not local)
        else:
            ok = (d["diferencia"] != 0 and local) or (d["diferenciame"] != 0 and not local)
        if ok and not (d["diferencia"] == 0 and d["diferenciame"] != 0):
            salida.append(d)
    return salida


def moneda_empresa(codempresa):
    f = consulta_uno("""SELECT e.codmoneda, IFNULL(m.nommoneda, '') AS nommoneda FROM empresas e
                         LEFT JOIN commonedas m ON m.codmoneda = e.codmoneda WHERE e.codempresa = %s""", (codempresa,))
    return (f["codmoneda"], f["nommoneda"]) if f else ("", "")


def nombre_moneda(codmoneda):
    f = consulta_uno("SELECT nommoneda FROM commonedas WHERE codmoneda = %s", (codmoneda,))
    return (f or {}).get("nommoneda", "") or codmoneda
