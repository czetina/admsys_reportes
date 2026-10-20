"""
Auxiliares de IVA (menú 50, 52 y 55 del sistema IVA de VFP).

  con_iva_compras_xls             Libro de compras + resumen + detalle de proveedores
  con_iva_ventas_xls              Libro de ventas + resumen (detalle o resumido por día)
  con_iva_compras_referencia_xls  Compras agrupadas por el documento que las aplica (cheque, pago)
  sat_iva_compras_2013 / sat_iva_ventas_2013   Archivos para Asiste Libros (versión 2)
  sat_iva_compras / sat_iva_ventas              Archivos para Asiste Libros (versión 1)

En VFP cada reporte arma el libro en una tabla temporal del servidor (CREATE TABLE + INSERT +
UPDATE) y, además, modifica datos reales en cada corrida:
  · UPDATE transacciones SET total_servicios = total_servicios + timbre_prensa (todas las empresas)
  · UPDATE libro_comprasventas SET base_no_gravada = 0 (todas las empresas, SAT versión 1)
  · actualiza_regs_retenciones_exenciones_cab.prg (UPDATE por documento, si la empresa lo usa)
Aquí todo se calcula en memoria con dos consultas de lectura (libro manual + transacciones) y el
ajuste del timbre de prensa se aplica en la propia consulta, sin escribir.
"""
import re
from datetime import datetime
from decimal import Decimal

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import ErrorReporte, fin_de_mes
from .modelo import ArchivoTexto, Columna, Fila, Libro, Reporte

MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")

MONTOS = ("total_bienes", "total_servicios", "base_gravada_bienes", "base_gravada_servicios", "base_exenta_bienes",
          "base_exenta_servicios", "base_no_gravada", "iva", "total_doc", "valor_constancia_exencion")

# total_servicios como queda después del UPDATE que VFP hace sobre transacciones antes de leerlas
_TS = ("IF(t.timbre_prensa > 0 AND t.valordocml = t.total_servicios + t.timbre_prensa, "
       "t.total_servicios + t.timbre_prensa, t.total_servicios)")
_DEN = "((100 + IF(t.iva = 0, 0, t.porcentaje)) / 100)"
_BEB_VENTAS = ("t.base_exenta_bienes + IF(t.base_no_gravada = t.total_doc AND (t.base_exenta_bienes "
               "+ t.base_exenta_servicios + t.valor_combustible + IF(t.tipo_iva = 'EXPORTACION', 0, t.timbre_prensa) "
               "+ t.valor_otros_no_gravados) = 0, t.base_no_gravada, 0)")
_TS_VENTAS = "t.total_servicios - IF(t.total_servicios > 0, t.timbre_prensa, 0)"

# Montos de cada versión del INSERT de VFP (lo que no se indica se toma tal cual de transacciones)
VARIANTES = {
    "compras": {
        "total_servicios": f"{_TS} - t.timbre_prensa",
        "base_gravada_bienes": f"ROUND((t.total_bienes - t.valor_combustible) / {_DEN}, 2)",
        "base_gravada_servicios": f"ROUND(({_TS} - t.timbre_prensa) / {_DEN}, 2)",
        "base_no_gravada": "t.valor_combustible + t.timbre_prensa + t.valor_otros_no_gravados",
        "total_doc": "t.valordocml",
    },
    "ventas": {
        "total_bienes": "t.total_bienes - t.valor_combustible",
        "total_servicios": _TS_VENTAS,
        "base_gravada_bienes": ("ROUND((t.total_bienes - IF(t.total_bienes > 0 AND t.total_servicios = 0, "
                                "t.timbre_prensa, 0) - IF(t.total_bienes > 0, t.valor_otros_no_gravados, 0) "
                                f"- t.valor_combustible) / {_DEN}, 2)"),
        "base_gravada_servicios": ("ROUND((t.total_servicios - IF(t.total_servicios > 0 AND t.tipo_iva <> 'LOCAL', "
                                   "t.valor_otros_no_gravados, 0) - IF(t.total_servicios > 0, t.timbre_prensa, 0)) "
                                   f"/ {_DEN}, 2)"),
        "base_exenta_bienes": _BEB_VENTAS,
        "base_no_gravada": "t.valor_combustible + t.timbre_prensa + t.valor_otros_no_gravados",
        "total_doc": "t.valordocml - t.gtos_cuenta_ajena",
    },
    "sat2013": {
        "total_bienes": "t.total_bienes - t.valor_combustible",
        "total_servicios": f"{_TS} - t.timbre_prensa",
        "base_gravada_bienes": f"ROUND((t.total_bienes - t.valor_combustible) / {_DEN}, 2)",
        "base_gravada_servicios": f"ROUND(({_TS} - t.timbre_prensa) / {_DEN}, 2)",
        "base_no_gravada": "t.valor_combustible + t.timbre_prensa + t.valor_otros_no_gravados",
        "total_doc": "t.valordocml",
    },
    "sat1_compras": {
        "total_bienes": "t.total_bienes - t.valor_combustible",
        "total_servicios": f"{_TS} - t.timbre_prensa",
        "base_gravada_bienes": "ROUND((t.total_bienes - t.valor_combustible) / ((100 + t.porcentaje) / 100), 2)",
        "base_gravada_servicios": f"ROUND(({_TS} - t.timbre_prensa) / ((100 + t.porcentaje) / 100), 2)",
        "base_no_gravada": "t.valor_combustible + t.timbre_prensa + t.valor_otros_no_gravados",
        "total_doc": "t.valordocml",
    },
    "sat1_ventas": {
        "total_bienes": "t.total_bienes - t.valor_combustible",
        "total_servicios": _TS_VENTAS,
        "base_gravada_bienes": f"ROUND((t.total_bienes - t.valor_combustible) / {_DEN}, 2)",
        "base_gravada_servicios": f"ROUND(({_TS_VENTAS}) / {_DEN}, 2)",
        "base_exenta_bienes": _BEB_VENTAS,
        "base_exenta_servicios": "t.base_exenta_servicios + t.timbre_prensa",
        "base_no_gravada": "t.valor_combustible + t.valor_otros_no_gravados",
        "total_doc": "t.valordocml",
    },
    "referencia": {},
}

_COLUMNAS_T = ("total_bienes", "total_servicios", "base_gravada_bienes", "base_gravada_servicios",
               "base_exenta_bienes", "base_exenta_servicios", "base_no_gravada", "total_doc")


def _texto_fecha(d):
    return d.strftime("%d/%m/%Y") if d else ""


def _f(v):
    return float(v or 0)


def _txt(v):
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


class _Iva:
    """Contexto de los libros de IVA: empresa, país, nombres del impuesto y del NIT, mes."""

    def __init__(self, p, clasificacion):
        self.p = p
        self.clasificacion = clasificacion
        self.emp = ctx.empresa(p.codempresa)
        e = consulta_uno(
            """
            SELECT e.codpais, e.direccionemp, IFNULL(g.nomiva, 'IVA') AS nomiva, IFNULL(g.nomnit, 'NIT') AS nomnit,
                   IFNULL(g.nom_reporte_compras, '') AS nom_compras, IFNULL(g.nom_reporte_ventas, '') AS nom_ventas
              FROM empresas e
              LEFT JOIN empparametrosgenerales g ON g.codempresa = e.codempresa
             WHERE e.codempresa = %s
            """, (p.codempresa,)) or {}
        self.codpais = _txt(e.get("codpais"))
        self.direccion = _txt(e.get("direccionemp"))
        self.nomiva = _txt(e.get("nomiva")) or "IVA"
        self.nomnit = _txt(e.get("nomnit")) or "NIT"
        self.nom_libro = _txt(e.get("nom_compras" if clasificacion == "C" else "nom_ventas"))
        self.fecini = fin_de_mes(p.anio, p.mes).replace(day=1)
        self.fecfin = fin_de_mes(p.anio, p.mes)
        self.sucursal = None
        if p.sucursal is not None:
            s = consulta_uno("SELECT nombre, direccion FROM iva_sucursales WHERE codempresa = %s AND sucursal = %s",
                             (p.codempresa, p.sucursal))
            self.sucursal = (p.sucursal, _txt((s or {}).get("nombre")) or ("SIN SUCURSAL" if p.sucursal == 0 else ""))

    # ------------------------------------------------------------------ datos

    def libro(self, variante, sucursal_por_serie=False, excluye_pfa=False, orden_sql=None):
        """
        Filas del libro: primero las de libro_comprasventas (captura manual / importadas) y luego las
        de transacciones del mes, cada una como dict con los nombres de libro_comprasventas.
        """
        p, suc = self.p, self.p.sucursal
        filtro_l, params_l = "", []
        if suc is not None and (suc or sucursal_por_serie):
            filtro_l, params_l = " AND sucursal = %s", [suc]
        manuales = consulta(
            f"""
            SELECT * FROM libro_comprasventas
             WHERE codempresa = %s AND clasificacion = %s AND anio = %s AND mes = %s{filtro_l}
             ORDER BY id
            """, (p.codempresa, self.clasificacion, p.anio, p.mes, *params_l))
        filas = []
        for m in manuales:
            f = {k: m.get(k) for k in m}
            f["origen"] = "L"
            f["actualizadocnt"], f["usacnt"] = 0, 0
            f["numero_doc"] = _txt(m.get("numero_doc"))
            if variante == "compras" and _f(m.get("valor_combustible")) > 0:
                f["base_no_gravada"] = m["valor_combustible"]
            if variante == "sat2013" and self.clasificacion == "C" and _f(m.get("valor_combustible")) > 0:
                f["total_bienes"] = _f(m["total_bienes"]) - _f(m["valor_combustible"])
            filas.append(f)

        exprs = VARIANTES[variante]
        montos = ", ".join(f"{exprs.get(c, 't.' + c)} AS {c}" for c in _COLUMNAS_T)
        joins, filtros, params = "", [], [p.codempresa, self.fecini, self.fecfin, self.clasificacion]
        if suc is not None:
            if sucursal_por_serie:
                joins = """LEFT JOIN empdoctos_series s ON s.codempresa = t.codempresa AND s.codsistema = t.codsistema
                                AND s.codtipodoctos = t.codtipodoctos AND s.codtipo = t.codtipo AND s.serie = t.serie"""
                filtros.append("AND s.sucursal = %s")
                params.append(suc)
            elif suc:
                filtros.append("AND t.sucursal = %s")
                params.append(suc)
        if excluye_pfa:
            filtros.append("AND TRIM(t.codsistema) <> 'PFA'")
        trans = consulta(
            f"""
            SELECT t.fecha AS fechadoc, t.fecmov, t.nit, t.nombre, t.tipo_doc_iva AS tipo_doc, t.serie AS serie_doc,
                   t.numero, t.caja, {montos}, t.porcentaje, t.iva, t.tipo_iva AS tipo, t.tipodocimp, t.numdocimp,
                   t.retiva_valorml, t.retiva_numero, t.no_constancia_exencion, t.valor_constancia_exencion,
                   t.no_constancia_insumos, t.valor_constancia_insumos, t.actualizadocnt, t.usacnt, t.keytra,
                   t.keyref, t.keyaux, t.anulado, t.descontable, t.docref, t.es_facesp,
                   t.ret_facesp_comp_valorml, t.ret_facesp_serv_valorml, t.valor_combustible
              FROM transacciones t {joins}
             WHERE t.codempresa = %s AND t.fecmov BETWEEN %s AND %s AND t.clasificacion_iva = %s
                   AND t.tipo_doc_iva <> '' {' '.join(filtros)}
             ORDER BY {orden_sql or 't.fecmov, t.caja, t.tipo_doc_iva, t.serie, t.numero'}
            """, params)
        for t in trans:
            f = dict(t)
            f["origen"] = "T"
            f["clasificacion"] = self.clasificacion
            f["numero_doc"] = str(int(t["numero"] or 0))
            f["concepto"] = ""
            f["orden_cedula"] = f["registro_cedula"] = ""
            if variante == "ventas":            # el INSERT de ventas no copia estas columnas
                f["keyref"] = f["keyaux"] = f["descontable"] = f["docref"] = ""
            filas.append(f)
        for f in filas:
            for c in MONTOS + ("porcentaje", "retiva_valorml", "valor_constancia_insumos", "retiva_numero",
                               "no_constancia_exencion", "no_constancia_insumos", "ret_facesp_comp_valorml",
                               "ret_facesp_serv_valorml"):
                f[c] = _f(f.get(c))
            for c in ("nit", "nombre", "tipo_doc", "serie_doc", "tipo", "tipodocimp", "numdocimp", "keytra", "keyref",
                      "keyaux", "descontable", "docref", "concepto", "orden_cedula", "registro_cedula"):
                f[c] = _txt(f.get(c))
            f["anulado"] = int(f.get("anulado") or 0)
        return filas

    def aplica_signos(self, filas):
        """Documentos con signo '-' en iva_tipo_doc (notas de crédito): montos en negativo."""
        negativos = {_txt(r["tipo_doc"]).upper() for r in consulta(
            "SELECT tipo_doc FROM iva_tipo_doc WHERE codpais = %s AND clasificacion = %s AND signo = '-'",
            (self.codpais, self.clasificacion))}
        for f in filas:
            if f["tipo_doc"].upper() in negativos:
                for c in MONTOS:
                    f[c] = -f[c]

    def tipos_documento(self):
        return [(_txt(r["tipo_doc"]), _txt(r["nombre"])) for r in consulta(
            "SELECT tipo_doc, nombre FROM iva_tipo_doc WHERE codpais = %s AND clasificacion = %s ORDER BY tipo_doc",
            (self.codpais, self.clasificacion))]

    def tipos_docimp(self):
        return list(dict.fromkeys(_txt(r["tipodocimp"]) for r in consulta(
            "SELECT tipodocimp FROM iva_tipodocimp WHERE codpais = %s AND clasificacion = %s ORDER BY tipodocimp",
            (self.codpais, self.clasificacion))))

    def porcentaje_default(self):
        r = consulta_uno("SELECT porcentaje FROM iva_tipo WHERE codpais = %s AND clasificacion = %s AND esdefault = 1",
                         (self.codpais, self.clasificacion))
        return _f((r or {}).get("porcentaje")) / 100

    def referencias(self, filas):
        """
        Registro contable que referencia cada documento (dettransacciones por keyref, o por keyaux si
        no tiene keyref): una consulta por cada 500 llaves; VFP hacía una consulta por documento.
        """
        salida = {}
        for campo in ("keyref", "keyaux"):
            llaves = sorted({f[campo] for f in filas if f[campo] and (campo == "keyref" or not f["keyref"])})
            for i in range(0, len(llaves), 500):
                bloque = llaves[i:i + 500]
                for r in consulta(
                        f"""
                        SELECT d.{campo} AS llave, d.keytra, t.usacnt, t.codtipo, t.serie, t.numero, t.actualizadocnt
                          FROM dettransacciones d
                          LEFT JOIN transacciones t ON t.keytra = d.keytra
                         WHERE d.{campo} IN ({','.join(['%s'] * len(bloque))})
                         ORDER BY d.iddettransacciones
                        """, bloque):
                    salida.setdefault((campo, r["llave"]), r)
        return salida

    # ------------------------------------------------------------------ salida

    def subtitulo(self):
        partes = [f"{MESES[self.p.mes - 1]} DEL {self.p.anio}"]
        if self.sucursal:
            partes.append(f"SUCURSAL: {self.sucursal[0]} {self.sucursal[1]}".strip())
        if self.direccion:
            partes.append(f"DIRECCIÓN: {self.direccion}")
        return " · ".join(partes)

    def titulo(self, defecto):
        nombre = (self.p.nombre or "").strip()
        if (not nombre or nombre.upper() == defecto) and self.nom_libro:
            return self.nom_libro
        return nombre or defecto

    def reporte(self, titulo, subtitulo, columnas, filas, hoja):
        archivo = re.sub(r"[^\w\- ]", "", f"{titulo} {self.emp.codempresa} {self.p.anio}-{self.p.mes:02d}")
        return Reporte(titulo=titulo, subtitulo=subtitulo, empresa=self.emp, columnas=columnas, filas=filas,
                       nombre_archivo=archivo.strip() + ".xlsx", hoja=hoja,
                       fecha_hora=datetime.now() if self.p.fecha_hora else None)


class _Columnas:
    """Columnas con nombre: c["iva"] da el índice; evita contar posiciones a mano."""

    def __init__(self):
        self.lista, self.pos = [], {}

    def add(self, clave, columna):
        self.pos[clave] = len(self.lista)
        self.lista.append(columna)

    def __getitem__(self, clave):
        return self.pos[clave]

    def rango(self, desde, hasta):
        return list(range(self.pos[desde], self.pos[hasta] + 1))


def _sin_datos(filas, que):
    if not filas:
        raise ErrorReporte(f"No hay documentos de {que} en el periodo indicado.")


def _resumen_documentos(r, filas, n, i_cant, i_nombre):
    """«RESUMEN DE DOCUMENTOS»: cantidad por tipo de documento del catálogo iva_tipo_doc."""
    cuenta = {}
    for f in filas:
        cuenta[f["tipo_doc"].upper()] = cuenta.get(f["tipo_doc"].upper(), 0) + 1
    tipos = r.tipos_documento()
    salida = [Fila(), Fila(tipo="grupo", nombre="RESUMEN DE DOCUMENTOS")]
    total = sum(cuenta.get(t.upper(), 0) for t, _ in tipos)
    v = [None] * n
    v[i_cant], v[i_nombre] = str(total), "TOTAL DE DOCUMENTOS"
    salida.append(Fila(tipo="cuenta", nivel=1, valores=v, negrita=True))
    for tipo, nombre in tipos:
        v = [None] * n
        v[i_cant], v[i_nombre] = str(cuenta.get(tipo.upper(), 0)), f"{nombre}  [{tipo}]"
        salida.append(Fila(tipo="cuenta", nivel=1, valores=v))
    return salida


def _celda_ref(fila_total, col):
    return ("refs", [(1, fila_total, col)])


# ------------------------------------------------------------------ Libro de compras

def libro_compras(p):
    """
    con_iva_compras_xls: una fila por documento de compra del mes, columnas por tipo de compra
    (bienes, combustibles, servicios, importaciones, pequeño contribuyente), totales, resumen de
    documentos, hoja de resumen (verificación del IVA) y detalle de proveedores.
    """
    r = _Iva(p, "C")
    filas_bd = r.libro("compras")
    _sin_datos(filas_bd, "compras")
    r.aplica_signos(filas_bd)
    filas_bd.sort(key=lambda f: (f["fechadoc"] or datetime.min.date(), f["tipo_doc"].lower(), f["serie_doc"].lower(),
                                 f["numero_doc"].lower()))
    docimp = r.tipos_docimp()
    iva, nit = r.nomiva.upper(), r.nomnit

    c = _Columnas()
    c.add("serie", Columna("SERIE", ancho=8, texto=True))
    c.add("numero", Columna("NÚMERO", ancho=18, texto=True))
    c.add("nit", Columna(f"{nit} O DPI", ancho=14, texto=True))
    c.add("nombre", Columna("NOMBRE", ancho=40, texto=True))
    c.add("serv_exe", Columna("SERVICIOS EXENTOS", ancho=14))
    c.add("bien_exe", Columna("BIENES EXENTOS", ancho=14))
    c.add("otros_exe", Columna("OTROS EXENTOS", ancho=14))
    c.add("nc", Columna("NOTA CRÉDITO", ancho=14))
    c.add("nc_neto", Columna("NOTA CRÉDITO NETAS", ancho=14))
    c.add("bienes", Columna("COMPRAS BIENES", ancho=14))
    c.add("bienes_neto", Columna("COMPRAS BIENES NETAS", ancho=14))
    c.add("comb", Columna("COMPRAS COMBUSTIBLES", ancho=14))
    c.add("comb_neto", Columna("COMPRAS COMBUSTIBLES NETAS", ancho=14))
    c.add("serv", Columna("COMPRAS SERVICIOS", ancho=14))
    c.add("serv_neto", Columna("COMPRAS SERVICIOS NETOS", ancho=14))
    c.add("imp", Columna("COMPRAS IMPORTACIONES", ancho=14))
    for d in docimp:
        c.add("imp:" + d.upper(), Columna(d.upper(), ancho=14))
    c.add("peq", Columna("PEQUEÑO CONTRIB.", ancho=14))
    c.add("iva", Columna(iva, ancho=14))
    c.add("total", Columna("TOTAL", ancho=14))
    c.add("pct", Columna(f"% {iva}", ancho=8))
    c.add("tipo_iva", Columna(f"TIPO {iva}", ancho=13, texto=True))
    c.add("iva_man", Columna(f"{iva} MANUAL", ancho=14))
    c.add("total_man", Columna("TOTAL MANUAL", ancho=14))
    c.add("dif_iva", Columna(f"DIF. {iva}", ancho=12))
    c.add("dif_total", Columna("DIF. TOTAL", ancho=12))
    c.add("act", Columna("ACT. CNT", ancho=6, texto=True))
    c.add("llave", Columna("LLAVE", ancho=26, texto=True))
    c.add("llave_aux", Columna("LLAVE AUX.", ancho=26, texto=True))
    c.add("llave_ref", Columna("LLAVE REF.", ancho=26, texto=True))
    c.add("registro_ref", Columna("REGISTRO REF.", ancho=18, texto=True))
    c.add("isr_fe", Columna("ISR FAC. ESP.", ancho=12))
    c.add("ins_num", Columna("NÚMERO", ancho=12, texto=True))
    c.add("ins_val", Columna("VALOR", ancho=12))
    c.add("ret_num", Columna("NÚMERO", ancho=16, texto=True))
    c.add("ret_val", Columna("VALOR", ancho=12))
    c.add("desc", Columna("DESCRIPCIÓN", ancho=50, texto=True))
    c.add("docref", Columna("DOC. REF.", ancho=16, texto=True))
    n = len(c.lista)
    cols_imp = [c["imp:" + d.upper()] for d in docimp]

    refs = r.referencias(filas_bd) if p.inc_det_cuadre else {}
    filas = []
    for f in filas_bd:
        anulado = f["anulado"] == 1
        tipo = f["tipo"].upper()
        v = [None] * n

        def pon(clave, valor):
            v[c[clave]] = 0.0 if anulado else round(valor, 2)

        v[c["serie"]], v[c["numero"]] = f["serie_doc"], f["numero_doc"].rjust(16, "0")
        v[c["nit"]] = " " if anulado else f["nit"]
        v[c["nombre"]] = "*** A N U L A D A ***" if anulado else f["nombre"]
        if f["base_exenta_servicios"]:
            pon("serv_exe", f["base_exenta_servicios"])
        if f["base_exenta_bienes"]:
            pon("bien_exe", f["base_exenta_bienes"])
        if tipo in ("IMPORTACION", "COMBUSTIBLES", "LOCAL", "P.CONTRIB") and f["base_no_gravada"]:
            pon("otros_exe", f["base_no_gravada"])
        if f["total_doc"] < 0:
            pon("nc", f["total_bienes"] + f["total_servicios"])
            pon("nc_neto", f["base_gravada_bienes"] + f["base_gravada_servicios"])
        if tipo in ("IMPORTACION", "FAUCA"):
            pon("imp", f["total_bienes"] + f["total_servicios"])
            for d in docimp:
                if f["tipodocimp"].upper().startswith(d.upper()):
                    pon("imp:" + d.upper(), f["base_gravada_bienes"] + f["base_gravada_servicios"])
        else:
            if tipo == "COMBUSTIBLES":
                pon("comb", f["total_bienes"] - f["base_no_gravada"])
                pon("comb_neto", f["base_gravada_bienes"])
            elif tipo == "EX.DCR.29-89":
                pon("bien_exe", f["total_bienes"])
            elif tipo != "P.CONTRIB":
                pon("bienes", f["total_bienes"])
                pon("bienes_neto", f["base_gravada_bienes"])
            if tipo != "P.CONTRIB":
                pon("serv_neto", f["base_gravada_servicios"])
                pon("serv", f["total_servicios"])
            else:
                pon("peq", f["total_servicios"] + f["total_bienes"])
        pon("iva", f["iva"])
        pon("total", f["total_doc"])

        formulas = {}
        if p.inc_det_cuadre:
            v[c["tipo_iva"]] = f["tipo"]
            if not anulado:
                v[c["pct"]] = f["porcentaje"]
                base = [c["bienes_neto"], c["comb_neto"], c["serv_neto"]] + cols_imp
                formulas[c["iva_man"]] = ("producto", [(1, k) for k in base], c["pct"])
                v[c["iva_man"]] = round(sum(v[k] or 0 for k in base) * f["porcentaje"] / 100, 2)
                if f["tipo_doc"].upper() == "NC":
                    suma = [c["bien_exe"], c["otros_exe"], c["bienes_neto"], c["comb_neto"], c["serv_neto"],
                            c["imp"]] + cols_imp + [c["peq"], c["iva"]]
                else:
                    suma = [c["serv_exe"], c["bien_exe"], c["otros_exe"], c["nc"], c["bienes_neto"], c["comb_neto"],
                            c["serv_neto"]] + cols_imp + [c["peq"], c["iva_man"]]
                formulas[c["total_man"]] = ("lineal", [(1, k) for k in suma])
                v[c["total_man"]] = round(sum(v[k] or 0 for k in suma), 2)
                formulas[c["dif_iva"]] = ("resta", c["iva"], c["iva_man"])
                v[c["dif_iva"]] = round((v[c["iva"]] or 0) - v[c["iva_man"]], 2)
                formulas[c["dif_total"]] = ("resta", c["total"], c["total_man"])
                v[c["dif_total"]] = round((v[c["total"]] or 0) - v[c["total_man"]], 2)
                v[c["act"]] = "SI" if f["actualizadocnt"] else "NO"
            v[c["llave"]], v[c["llave_aux"]] = f["keytra"], f["keyref"]
            ref = refs.get(("keyref", f["keyref"])) if f["keyref"] else \
                refs.get(("keyaux", f["keyaux"])) if f["keyaux"] else None
            if ref:
                if ref["usacnt"] == 1:
                    v[c["act"]] = "SI" if ref["actualizadocnt"] else "NO"
                v[c["llave_ref"]] = _txt(ref["keytra"])
                if ref["codtipo"] is not None:
                    v[c["registro_ref"]] = f"{_txt(ref['codtipo'])} {_txt(ref['serie'])} {int(ref['numero'] or 0)}"
        isr = f["ret_facesp_comp_valorml"] + f["ret_facesp_serv_valorml"]
        if isr and not anulado:
            v[c["isr_fe"]] = round(isr, 2)
        if f["valor_constancia_insumos"] > 0:
            v[c["ins_num"]] = str(int(f["no_constancia_insumos"]))
            v[c["ins_val"]] = f["valor_constancia_insumos"]
        if f["retiva_valorml"] > 0:
            v[c["ret_num"]] = str(int(f["retiva_numero"]))
            v[c["ret_val"]] = f["retiva_valorml"]
        v[c["desc"]] = (f["concepto"] or f["descontable"])[:300]
        v[c["docref"]] = f["docref"]
        filas.append(Fila(tipo="cuenta", nivel=1, codigo=f["keytra"], cuenta=_texto_fecha(f["fechadoc"]),
                          nombre=f["tipo_doc"], valores=v, formulas=formulas))

    cols_total = c.rango("serv_exe", "total") + c.rango("iva_man", "dif_total") + [c["isr_fe"], c["ins_val"],
                                                                                   c["ret_val"]]
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[0], filas[-1]), valores=[None] * n)
    for k in cols_total:
        tot.valores[k] = round(sum(f.valores[k] or 0 for f in filas if isinstance(f.valores[k], float)), 2)
    filas += [tot] + _resumen_documentos(r, filas_bd, n, c["nit"], c["nombre"])

    titulo = r.titulo("LIBRO DE COMPRAS")
    rep = r.reporte(titulo, r.subtitulo(), c.lista, filas, "Compras")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("TIPO DOC.", 8)
    rep.grupos_columnas = [(c["serie"], c["numero"], "FACTURA"), (c["ins_num"], c["ins_val"], "CONST. ADQ. INSUMOS"),
                           (c["ret_num"], c["ret_val"], f"RETENCIÓN {iva}")]
    ocultas = {c["nc"], c["bienes"], c["comb"], c["serv"], c["imp"]}
    if not p.incluye_des_contable:
        ocultas |= set(c.rango("pct", "llave_ref"))
    if not p.incluye_info_adicional:
        ocultas |= set(c.rango("registro_ref", "docref"))
    if not p.inc_det_cuadre:
        ocultas |= set(c.rango("pct", "dif_total"))
    if p.mostrar_ret_fe:
        ocultas.discard(c["isr_fe"])
    else:
        ocultas.add(c["isr_fe"])
    rep.columnas_ocultas = ocultas

    resumen = _resumen_compras(r, rep, c, tot)
    proveedores = _resumen_proveedores(r, filas_bd)
    return Libro(titulo=titulo, hojas=[rep, resumen, proveedores], nombre_archivo=rep.nombre_archivo)


def _resumen_compras(r, rep, c, tot):
    """Hoja «Resumen»: compras brutas / netas por tipo, verificación del IVA, exentos y notas de crédito."""
    iva = r.nomiva.upper()
    pct = r.porcentaje_default()
    filas = [Fila(tipo="grupo", nombre="RESUMEN DE COMPRAS Y SERVICIOS")]

    def linea(nombre, bruto, neto):
        f = Fila(tipo="cuenta", nivel=1, nombre=nombre, valores=[tot.valores[c[bruto]], tot.valores[c[neto]]])
        f.formulas = {0: _celda_ref(tot, c[bruto]), 1: _celda_ref(tot, c[neto])}
        return f

    bloque = [linea("COMPRAS BIENES", "bienes", "bienes_neto"), linea("COMBUSTIBLES", "comb", "comb_neto"),
              linea("SERVICIOS", "serv", "serv_neto")]
    imp = Fila(tipo="cuenta", nivel=1, nombre="IMPORTACIÓN", valores=[tot.valores[c["imp"]], None])
    imp.valores[1] = round(imp.valores[0] / (1 + pct), 2)
    imp.formulas = {0: _celda_ref(tot, c["imp"]), 1: ("factor", imp, 0, round(1 / (1 + pct), 10))}
    bloque.append(imp)
    totales = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(bloque[0], bloque[-1]),
                   valores=[round(sum(f.valores[k] or 0 for f in bloque), 2) for k in (0, 1)])
    filas += bloque + [totales, Fila(), Fila(tipo="grupo", nombre=f"VERIFICACIÓN DE {iva}")]
    fila_pct = Fila(tipo="cuenta", nivel=1, nombre=f"% {iva}", valores=[pct, None])
    valor = Fila(tipo="cuenta", nivel=1, nombre=f"VALOR {iva}", valores=[round(totales.valores[1] * pct, 2), None])
    valor.formulas = {0: ("factor", totales, 1, pct)}
    dif = Fila(tipo="cuenta", nivel=1, nombre="DIFERENCIA CONTABILIDAD", negrita=True,
               valores=[round(valor.valores[0] - tot.valores[c["iva"]], 2), None])
    dif.formulas = {0: ("refs", [(1, valor, 0), (-1, tot, c["iva"])])}
    filas += [fila_pct, valor, dif, Fila(), Fila(tipo="grupo", nombre="EXENTOS Y NOTAS DE CRÉDITO")]
    exe = Fila(tipo="cuenta", nivel=1, nombre="EXENTOS EX DCR. 29-89",
               valores=[round(tot.valores[c["serv_exe"]] + tot.valores[c["bien_exe"]], 2), None])
    exe.formulas = {0: ("refs", [(1, tot, c["serv_exe"]), (1, tot, c["bien_exe"])])}
    exentos = [exe, linea("PEQUEÑO CONTRIBUYENTE", "peq", "peq"), linea("OTROS EXENTOS", "otros_exe", "otros_exe")]
    for f in exentos[1:]:
        f.valores[1] = None
        del f.formulas[1]
    tot_exe = Fila(tipo="total", negrita=True, nombre="TOTAL EXENTOS", rango=(exentos[0], exentos[-1]),
                   valores=[round(sum(f.valores[0] or 0 for f in exentos), 2), None])
    nc = Fila(tipo="cuenta", nivel=1, nombre="NOTAS DE CRÉDITO (NETAS)", valores=[None, tot.valores[c["nc_neto"]]])
    nc.formulas = {1: _celda_ref(tot, c["nc_neto"])}
    filas += exentos + [tot_exe, nc]
    res = r.reporte(rep.titulo, rep.subtitulo, [Columna("TOTAL BRUTO", ancho=18), Columna("TOTAL NETO", ancho=18)],
                    filas, "Resumen")
    res.encabezado_a, res.encabezado_b = ("", 3), ("CONCEPTO", 36)
    res.porcentaje_filas = {id(fila_pct)}
    return res


def _resumen_proveedores(r, filas_bd):
    """Hoja «Resumen Proveedores»: facturas y monto base por NIT (sin pequeños contribuyentes FPC)."""
    grupos = {}
    for f in filas_bd:
        if f["tipo_doc"].upper() == "FPC":
            continue
        g = grupos.setdefault((f["nit"], f["nombre"]), [0, 0.0])
        g[0] += 1
        g[1] += f["total_doc"] - f["iva"] - f["base_no_gravada"]
    orden = sorted(grupos.items(), key=lambda kv: -kv[1][1])
    filas = [Fila(tipo="cuenta", nivel=1, cuenta=str(i), nombre=nit, valores=[nombre, cant, round(monto, 2)])
             for i, ((nit, nombre), (cant, monto)) in enumerate(orden, 1)]
    if filas:
        filas.append(Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[0], filas[-1]),
                          valores=["", sum(f.valores[1] for f in filas), round(sum(f.valores[2] for f in filas), 2)]))
    res = r.reporte("DETALLE DE PROVEEDORES LOCALES (CRÉDITO FISCAL)", r.subtitulo(),
                    [Columna("PROVEEDOR", ancho=60, texto=True), Columna("CANTIDAD DE FACTURAS", ancho=14, entero=True),
                     Columna("MONTO BASE", ancho=18)], filas, "Resumen Proveedores")
    res.encabezado_a, res.encabezado_b = ("NO.", 6), (r.nomnit, 16)
    return res


# ------------------------------------------------------------------ Libro de ventas

def libro_ventas(p):
    """
    con_iva_ventas_xls: documentos de venta del mes (o resumidos por día, tipo y serie), con
    exportaciones por tipo de documento, verificación del IVA y hoja de resumen.
    """
    r = _Iva(p, "V")
    filas_bd = r.libro("ventas", sucursal_por_serie=True, excluye_pfa=True,
                       orden_sql="t.tipo_doc_iva, t.serie, t.numero")
    _sin_datos(filas_bd, "ventas")
    r.aplica_signos(filas_bd)
    det_cuadre = p.inc_det_cuadre and not p.resumen
    if p.orden == 2:
        filas_bd.sort(key=lambda f: (f["serie_doc"].lower(), f["numero_doc"].lower()))
    else:
        filas_bd.sort(key=lambda f: f["fechadoc"] or datetime.min.date())
    docimp = r.tipos_docimp()
    iva = r.nomiva.upper()

    if p.resumen:                     # ventas del día: suma por tipo, serie y fecha
        grupos = {}
        for f in filas_bd:
            g = grupos.get((f["tipo_doc"], f["serie_doc"], f["fechadoc"]))
            if g is None:
                g = grupos[(f["tipo_doc"], f["serie_doc"], f["fechadoc"])] = {
                    **{k: 0.0 for k in MONTOS}, "tipo_doc": f["tipo_doc"], "serie_doc": f["serie_doc"],
                    "fechadoc": f["fechadoc"], "numero_ini": f["numero_doc"], "numero_doc": f["numero_doc"],
                    "nit": "C/F", "nombre": "VENTAS DEL DIA", "anulado": 0, "tipo": "", "keytra": ""}
            for k in MONTOS:
                g[k] += f[k]
            g["numero_ini"] = min(g["numero_ini"], f["numero_doc"])
            g["numero_doc"] = max(g["numero_doc"], f["numero_doc"])
        detalle = sorted(grupos.values(), key=lambda g: (g["fechadoc"] or datetime.min.date(), g["tipo_doc"],
                                                         g["serie_doc"]))
    else:
        detalle = filas_bd

    c = _Columnas()
    c.add("serie", Columna("SERIE", ancho=8, texto=True))
    c.add("num_ini", Columna("NÚMERO INICIAL", ancho=18, texto=True))
    c.add("numero", Columna("NÚMERO FIN" if p.resumen else "NÚMERO", ancho=18, texto=True))
    c.add("fecha", Columna("FECHA", ancho=11, texto=True))
    c.add("nit", Columna(r.nomnit, ancho=14, texto=True))
    c.add("nombre", Columna("NOMBRE", ancho=40, texto=True))
    c.add("bienes", Columna("BRUTA", ancho=14))
    c.add("bienes_neto", Columna("NETA", ancho=14))
    c.add("serv", Columna("BRUTO", ancho=14))
    c.add("serv_neto", Columna("NETO", ancho=14))
    for d in docimp:
        c.add("imp:" + d.upper(), Columna(d.upper(), ancho=14))
    c.add("exento", Columna("MONTO EXENTO", ancho=14))
    c.add("iva", Columna(iva, ancho=14))
    c.add("total", Columna("TOTAL", ancho=14))
    c.add("llave", Columna("LLAVE", ancho=26, texto=True))
    c.add("tipo_iva", Columna(f"TIPO {iva}", ancho=13, texto=True))
    c.add("pct", Columna(f"% {iva}", ancho=8))
    c.add("iva_man", Columna(f"{iva} MANUAL", ancho=14))
    c.add("total_man", Columna("TOTAL MANUAL", ancho=14))
    c.add("dif_iva", Columna(f"DIFERENCIA {iva}", ancho=12))
    c.add("dif_total", Columna("DIFERENCIA TOTAL", ancho=12))
    c.add("act", Columna("ACT. CNT.", ancho=6, texto=True))
    c.add("llave_aux", Columna("LLAVE AUX.", ancho=26, texto=True))
    c.add("llave_ref", Columna("LLAVE REF.", ancho=26, texto=True))
    c.add("docref", Columna("DOCUMENTO REF.", ancho=18, texto=True))
    c.add("ret_num", Columna("NÚMERO", ancho=14, texto=True))
    c.add("ret_val", Columna("VALOR", ancho=12))
    c.add("exe_num", Columna("NÚMERO", ancho=14, texto=True))
    c.add("exe_val", Columna("VALOR", ancho=12))
    c.add("ins_num", Columna("NÚMERO", ancho=14, texto=True))
    c.add("ins_val", Columna("VALOR", ancho=12))
    n = len(c.lista)
    cols_imp = [c["imp:" + d.upper()] for d in docimp]

    refs = r.referencias(detalle) if det_cuadre else {}
    filas = []
    for f in detalle:
        anulado = f["anulado"] == 1
        tipo = f["tipo"].upper()
        v = [None] * n

        def pon(clave, valor):
            v[c[clave]] = 0.0 if anulado else round(valor, 2)

        v[c["serie"]] = f["serie_doc"]
        if p.resumen:
            ini, fin = f["numero_ini"], f["numero_doc"]
            v[c["num_ini"]] = ini.rjust(16, "0") if ini < fin else fin
            v[c["numero"]] = fin.rjust(16, "0") if fin > ini else ini
        else:
            v[c["numero"]] = f["numero_doc"].rjust(16, "0")
        v[c["fecha"]] = _texto_fecha(f["fechadoc"])
        v[c["nit"]] = " " if anulado else f["nit"]
        v[c["nombre"]] = "*** A N U L A D A ***" if anulado else f["nombre"]
        if tipo not in ("EX.DCR.29-89", "EXPORTACION"):
            pon("bienes", f["total_bienes"])
            pon("bienes_neto", f["base_gravada_bienes"])
            pon("serv", f["total_servicios"])
            pon("serv_neto", f["base_gravada_servicios"])
        if not p.resumen and tipo in ("EXPORTACION", "IMPORTACION", "FAUCA"):
            monto = f["base_exenta_bienes"] + f["base_exenta_servicios"] or f["total_doc"]
            for d in docimp:
                if f["tipodocimp"].upper().startswith(d.upper()):
                    pon("imp:" + d.upper(), monto)
        if tipo == "EX.DCR.29-89":
            pon("exento", f["base_no_gravada"] + f["base_exenta_bienes"] + f["base_exenta_servicios"]
                + f["total_bienes"])
        elif tipo != "EXPORTACION":
            pon("exento", f["base_no_gravada"])
        pon("iva", f["iva"])
        pon("total", f["total_doc"] if tipo != "EXPORTACION" else f["base_exenta_bienes"] + f["base_exenta_servicios"])

        formulas = {}
        if not p.resumen:
            v[c["llave"]] = f["keytra"]
            if det_cuadre:
                v[c["tipo_iva"]] = f["tipo"]
                if not anulado:
                    v[c["pct"]] = f["porcentaje"]
                    if tipo != "EXPORTACION":
                        base = [c["bienes_neto"], c["serv_neto"]]
                        formulas[c["iva_man"]] = ("producto", [(1, k) for k in base], c["pct"])
                        v[c["iva_man"]] = round(sum(v[k] or 0 for k in base) * f["porcentaje"] / 100, 2)
                        suma = base + cols_imp + [c["exento"], c["iva_man"]]
                        formulas[c["total_man"]] = ("lineal", [(1, k) for k in suma])
                        v[c["total_man"]] = round(sum(v[k] or 0 for k in suma), 2)
                        formulas[c["dif_iva"]] = ("resta", c["iva"], c["iva_man"])
                        v[c["dif_iva"]] = round((v[c["iva"]] or 0) - v[c["iva_man"]], 2)
                        formulas[c["dif_total"]] = ("resta", c["total"], c["total_man"])
                        v[c["dif_total"]] = round((v[c["total"]] or 0) - v[c["total_man"]], 2)
                    if f.get("usacnt") == 1:
                        v[c["act"]] = "SI" if f["actualizadocnt"] else "NO"
                v[c["llave_aux"]] = f["keyref"]
                ref = refs.get(("keyref", f["keyref"])) if f["keyref"] else \
                    refs.get(("keyaux", f["keyaux"])) if f["keyaux"] else None
                if ref:
                    if ref["usacnt"] == 1:
                        v[c["act"]] = "SI" if ref["actualizadocnt"] else "NO"
                    v[c["llave_ref"]] = _txt(ref["keytra"])
                    if ref["codtipo"] is not None:
                        v[c["docref"]] = f"{_txt(ref['codtipo'])} {_txt(ref['serie'])} {int(ref['numero'] or 0)}"
            if f["retiva_valorml"] > 0:
                v[c["ret_num"]], v[c["ret_val"]] = str(int(f["retiva_numero"])), f["retiva_valorml"]
            if f["valor_constancia_exencion"] > 0:
                v[c["exe_num"]] = str(int(f["no_constancia_exencion"]))
                v[c["exe_val"]] = f["valor_constancia_exencion"]
            if f["valor_constancia_insumos"] > 0:
                v[c["ins_num"]] = str(int(f["no_constancia_insumos"]))
                v[c["ins_val"]] = f["valor_constancia_insumos"]
        filas.append(Fila(tipo="cuenta", nivel=1, codigo=f.get("keytra", ""), cuenta=f["tipo_doc"],
                          nombre=f["serie_doc"], valores=v, formulas=formulas))
    # La serie ya va en la columna B; la columna de datos «SERIE» se oculta
    cols_total = c.rango("bienes", "total") + c.rango("iva_man", "dif_total") + [c["ret_val"], c["exe_val"],
                                                                                 c["ins_val"]]
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[0], filas[-1]), valores=[None] * n)
    for k in cols_total:
        tot.valores[k] = round(sum(f.valores[k] or 0 for f in filas if isinstance(f.valores[k], float)), 2)
    filas += [tot] + _resumen_documentos(r, filas_bd, n, c["nit"], c["nombre"])

    titulo = r.titulo("LIBRO DE VENTAS")
    rep = r.reporte(titulo, r.subtitulo(), c.lista, filas, "Ventas")
    rep.encabezado_a, rep.encabezado_b = ("TIPO", 7), ("SERIE", 8)
    rep.grupos_columnas = [(c["bienes"], c["bienes_neto"], "BIENES"), (c["serv"], c["serv_neto"], "SERVICIOS"),
                           (c["ret_num"], c["ret_val"], f"RETENCIÓN DE {iva}"),
                           (c["exe_num"], c["exe_val"], "CONSTANCIA DE EXENCIÓN"),
                           (c["ins_num"], c["ins_val"], "CONSTANCIA DE ADQ. DE INSUMOS")]
    ocultas = {c["serie"]}
    if not p.resumen:
        ocultas.add(c["num_ini"])
    if not det_cuadre:
        ocultas |= set(c.rango("llave", "ins_val"))
    rep.columnas_ocultas = ocultas
    return Libro(titulo=titulo, hojas=[rep, _resumen_ventas(r, rep, c, tot, cols_imp)],
                 nombre_archivo=rep.nombre_archivo)


def _resumen_ventas(r, rep, c, tot, cols_imp):
    iva = r.nomiva.upper()
    pct = r.porcentaje_default()

    def linea(nombre, col):
        f = Fila(tipo="cuenta", nivel=1, nombre=nombre, valores=[tot.valores[col]])
        f.formulas = {0: _celda_ref(tot, col)}
        return f

    bienes, serv = linea("VENTAS BIENES", c["bienes_neto"]), linea("VENTAS SERVICIOS", c["serv_neto"])
    sub = Fila(tipo="cuenta", nivel=1, nombre="SUBTOTAL NETO", negrita=True,
               valores=[round(bienes.valores[0] + serv.valores[0], 2)])
    sub.formulas = {0: ("refs", [(1, bienes, 0), (1, serv, 0)])}
    exp = Fila(tipo="cuenta", nivel=1, nombre="EXPORTACIONES",
               valores=[round(sum(tot.valores[k] or 0 for k in cols_imp), 2)])
    if cols_imp:
        exp.formulas = {0: ("refs", [(1, tot, k) for k in cols_imp])}
    gravado = Fila(tipo="total", negrita=True, nombre="TOTAL GRAVADO Y EXPORTACIONES",
                   valores=[round(sub.valores[0] + exp.valores[0], 2)])
    gravado.formulas = {0: ("refs", [(1, sub, 0), (1, exp, 0)])}
    fila_pct = Fila(tipo="cuenta", nivel=1, nombre=f"% {iva}", valores=[pct])
    valor = Fila(tipo="cuenta", nivel=1, nombre=f"VALOR {iva}", valores=[round(sub.valores[0] * pct, 2)])
    valor.formulas = {0: ("factor", sub, 0, pct)}
    dif = Fila(tipo="cuenta", nivel=1, nombre="DIFERENCIA CONTABILIDAD", negrita=True,
               valores=[round(valor.valores[0] - tot.valores[c["iva"]], 2)])
    dif.formulas = {0: ("refs", [(1, valor, 0), (-1, tot, c["iva"])])}
    exento = linea("EXENTOS", c["exento"])
    filas = [Fila(tipo="grupo", nombre="RESUMEN DE VENTAS"), bienes, serv, sub, exp, gravado, Fila(),
             Fila(tipo="grupo", nombre=f"VERIFICACIÓN DE {iva}"), fila_pct, valor, dif, Fila(),
             Fila(tipo="grupo", nombre="EXENTOS"), exento]
    res = r.reporte(rep.titulo, rep.subtitulo, [Columna("VALOR", ancho=18)], filas, "Resumen")
    res.encabezado_a, res.encabezado_b = ("", 3), ("CONCEPTO", 36)
    res.porcentaje_filas = {id(fila_pct)}
    return res


# ------------------------------------------------------------------ Compras por documento de referencia

def compras_referencia(p):
    """
    con_iva_compras_referencia_xls: compras del mes agrupadas por cuenta de cheques y por el
    documento que las aplica (cheque, pago, nota), con los montos del libro.
    """
    r = _Iva(p, "C")
    partes, params = [], [p.codempresa, r.fecini, r.fecfin, "C"]
    if p.codsistema_ref:
        partes.append("AND a1.codsistema = %s")
        params.append(p.codsistema_ref)
    if p.codctachq:
        partes.append("AND a1.codctachq = %s")
        params.append(p.codctachq)
    tipos = [t.strip() for t in (p.tipos_ref or "").split(",") if t.strip()]
    if tipos:
        partes.append(f"AND a1.codtipo IN ({','.join(['%s'] * len(tipos))})")
        params += tipos
    if p.sucursal:
        partes.append("AND t.sucursal = %s")
        params.append(p.sucursal)
    filas_bd = consulta(
        f"""
        SELECT t.fecha AS fechadoc, t.nit, t.nombre, t.tipo_doc_iva AS tipo_doc, t.serie AS serie_doc, t.numero,
               t.total_bienes, t.total_servicios, t.base_gravada_bienes, t.base_gravada_servicios,
               t.base_exenta_bienes, t.base_exenta_servicios, t.base_no_gravada, t.iva, t.total_doc,
               t.tipo_iva AS tipo, t.tipodocimp, t.actualizadocnt, t.keytra, t.keyref, t.keyaux, t.anulado,
               IFNULL(a1.keytra, '') AS keytra_apl, IFNULL(a1.codctachq, '') AS codctachq,
               IFNULL(a1.numero, 0) AS numerodoc, a1.fecmov AS fecmov_apl, IFNULL(a1.nombre, '') AS nombre_apl,
               IFNULL(a1.codtipo, '') AS codtipo_apl
          FROM transacciones t
          LEFT JOIN transacciones_aplica ap ON ap.keytra = t.keytra
          LEFT JOIN transacciones a1 ON a1.keytra = ap.keytra_apl
         WHERE t.codempresa = %s AND t.fecmov BETWEEN %s AND %s AND t.clasificacion_iva = %s
               AND t.tipo_doc_iva <> '' {' '.join(partes)}
        """, params)
    _sin_datos(filas_bd, "compras")
    for f in filas_bd:
        f["numero_doc"] = str(int(f["numero"] or 0))
        for k in MONTOS:
            f[k] = _f(f.get(k))
        for k in ("tipo_doc", "serie_doc", "tipo", "tipodocimp", "keytra", "keyref", "keyaux", "codctachq",
                  "keytra_apl", "nit", "nombre", "nombre_apl", "codtipo_apl"):
            f[k] = _txt(f.get(k))
        f["anulado"] = int(f["anulado"] or 0)
    r.aplica_signos(filas_bd)
    filas_bd.sort(key=lambda f: (f["codctachq"], f["numerodoc"] or 0, f["fecmov_apl"] or datetime.min.date(),
                                 f["keytra_apl"], f["serie_doc"].lower(), f["numero_doc"].lower(),
                                 f["fechadoc"] or datetime.min.date(), f["tipo_doc"]))
    refs = r.referencias([{**f, "keyaux": ""} for f in filas_bd if not f["anulado"] and not f["actualizadocnt"]])

    cols = [Columna("SERIE", ancho=8, texto=True), Columna("NÚMERO", ancho=18, texto=True),
            Columna(f"{r.nomnit} O CÉDULA", ancho=14, texto=True), Columna("NOMBRE", ancho=40, texto=True),
            Columna("NOTAS DE CRÉDITO", ancho=14), Columna("NOTAS DE CRÉDITO NETAS", ancho=14),
            Columna("OTROS EXENTOS", ancho=14), Columna("COMPRAS", ancho=14), Columna("COMPRAS NETAS", ancho=14),
            Columna("SERVICIOS", ancho=14), Columna("SERVICIOS NETOS", ancho=14),
            Columna("IMPORTACIÓN (RESTO DEL MUNDO)", ancho=14), Columna("IMPORTACIÓN (C.A. - FAUCA)", ancho=14),
            Columna(r.nomiva.upper(), ancho=14), Columna("TOTAL", ancho=14),
            Columna("OBSERVACIONES", ancho=60, texto=True)]
    n = len(cols)
    filas, datos = [], []
    cta_actual, apl_actual = None, None
    for f in filas_bd:
        if f["codctachq"] != cta_actual:
            if cta_actual is not None:
                filas.append(Fila())
            cta_actual, apl_actual = f["codctachq"], None
            filas.append(Fila(tipo="grupo", cuenta=cta_actual or "SIN CUENTA"))
        if f["keytra_apl"] != apl_actual:
            apl_actual = f["keytra_apl"]
            v = [None] * n
            v[1] = str(int(f["numerodoc"] or 0)) if f["keytra_apl"] else ""
            v[3] = f["nombre_apl"] if f["keytra_apl"] else "SIN DOCUMENTO DE REFERENCIA"
            filas.append(Fila(tipo="cuenta", nivel=1, negrita=True, cuenta=_texto_fecha(f["fecmov_apl"]),
                              nombre=f["codtipo_apl"], valores=v))
        v = [None] * n
        v[0], v[1], v[2], v[3] = f["serie_doc"], f["numero_doc"].rjust(16, "0"), f["nit"], f["nombre"]
        if f["total_doc"] < 0:
            v[4] = round(f["total_bienes"] + f["total_servicios"], 2)
            v[5] = round(f["base_gravada_bienes"] + f["base_gravada_servicios"], 2)
        elif f["tipo"].upper() in ("IMPORTACION", "FAUCA"):
            neto = round(f["base_gravada_bienes"] + f["base_gravada_servicios"], 2)
            aduana = f["tipodocimp"].upper().startswith("D.ADUANERA")
            v[11], v[12] = (neto, 0.0) if aduana else (0.0, neto)
            v[7] = v[8] = v[9] = v[10] = 0.0
        else:
            v[8], v[10] = f["base_gravada_bienes"], f["base_gravada_servicios"]
            v[7], v[9] = f["total_bienes"], f["total_servicios"]
            v[11] = v[12] = 0.0
        v[6] = round(f["base_exenta_bienes"] + f["base_exenta_servicios"], 2)
        v[13], v[14] = f["iva"], f["total_doc"]
        if f["anulado"]:
            v[15] = f"***ANULADO*** {f['keytra']}"
        elif not f["actualizadocnt"]:
            ref = refs.get(("keyref", f["keyref"])) if f["keyref"] else None
            v[15] = f"** {f['keytra']}-{f['keyref']}-{_txt(ref['keytra']) if ref else ''}"
        fila = Fila(tipo="cuenta", nivel=2, codigo=f["keytra"], cuenta=_texto_fecha(f["fechadoc"]),
                    nombre=f["tipo_doc"], valores=v)
        filas.append(fila)
        datos.append(fila)
    tot = Fila(tipo="total", negrita=True, nombre="TOTALES", rango=(filas[1], filas[-1]), valores=[None] * n)
    for k in range(4, 15):
        tot.valores[k] = round(sum(f.valores[k] or 0 for f in datos), 2)
    filas += [Fila(), tot, Fila(), Fila(tipo="grupo", nombre="NO. DOCUMENTOS")]
    cuenta = {}
    for f in filas_bd:
        cuenta[f["tipo_doc"]] = cuenta.get(f["tipo_doc"], 0) + 1
    for tipo in sorted(cuenta):
        v = [None] * n
        v[0], v[1] = tipo, str(cuenta[tipo])
        filas.append(Fila(tipo="cuenta", nivel=1, valores=v))
    rep = r.reporte((p.nombre or "LIBRO DE COMPRAS POR DOCUMENTO DE REFERENCIA").strip(), r.subtitulo(), cols, filas,
                    "Compras")
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 11), ("TIPO", 8)
    rep.grupos_columnas = [(0, 1, "FACTURA")]
    rep.columnas_ocultas = {4, 7, 9}
    return rep


# ------------------------------------------------------------------ Asiste Libros (SAT)

def _num(v):
    """Número como lo escribe VFP en el archivo: dos decimales, sin separador de miles."""
    v = round(float(v or 0), 2) + 0.0
    return f"{v:.2f}"


def _campo(valor, ancho, pipe=True):
    """THISFORM.mas(): texto recortado al ancho del campo, sin espacios, precedido de «|»."""
    if isinstance(valor, float):
        texto = _num(valor)
    else:
        texto = str(valor)
    return ("|" if pipe else "") + texto[:ancho].strip()


def _campo_v(valor, ancho, pipe=True):
    """mas() del formulario de ventas versión 2: los importes enteros van sin decimales (0, 13690)."""
    if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool) and float(valor) == int(valor):
        valor = str(int(valor))
    return _campo(valor, ancho, pipe)


def _archivo(r, sufijo, lineas):
    """Mismo nombre que VFP (AAAAMM + C/V + .asl); VFP lo grababa en la carpeta elegida, aquí se descarga."""
    return ArchivoTexto(f"{r.p.anio:04d}{r.p.mes:02d}{sufijo}.asl", lineas)


def sat_compras_2013(p):
    """sat_iva_compras_2013: libro de compras para Asiste Libros versión 2 (30 campos por documento)."""
    r = _Iva(p, "C")
    filas_bd = r.libro("sat2013")
    _sin_datos(filas_bd, "compras" if r.clasificacion == "C" else "ventas")
    sucursal = p.sucursal or 1
    lineas = []
    for f in filas_bd:
        tipo = f["tipo"].upper()
        anulado = f["anulado"] == 1
        doc = f["tipo_doc"]
        nit = f["nit"].replace("-", "")
        cero = 0
        s = _campo(f"{sucursal:>2}", 2, False) + _campo("C", 1) + _campo(doc, 10)
        s += _campo(f["serie_doc"] or 0, 16) + _campo(f["numero_doc"].replace("-", "").strip(), 25)
        s += _campo(_texto_fecha(f["fechadoc"]), 10)
        s += _campo("" if doc == "FE" else nit, 12)
        s += _campo(f["nombre"], 100) + _campo("I" if tipo == "IMPORTACION" else "L", 1)
        s += _campo("", 1) + _campo("", 1)
        if doc == "FE":
            s += _campo(f["orden_cedula"] or 0, 10) + _campo(f["registro_cedula"] or 0, 12)
        else:
            s += _campo("", 4) + _campo("", 12)
        s += _campo("", 10) + _campo("", 50)
        if tipo == "P.CONTRIB":
            s += "".join(_campo("", 15) for _ in range(8))
        elif tipo == "IMPORTACION":
            if anulado:
                s += "".join(_campo(cero, 15) for _ in range(8))
            else:
                for valor in (cero, f["total_bienes"], cero, f["total_servicios"], cero, f["base_exenta_bienes"],
                              cero, f["base_exenta_servicios"] + f["base_no_gravada"]):
                    s += _campo(valor, 15)
        else:
            for valor in ((cero, cero, cero, cero) if anulado else (f["total_bienes"], cero, f["total_servicios"],
                                                                   cero)):
                s += _campo(valor, 15)
            if doc == "FE" or anulado:
                s += "".join(_campo(cero, 15) for _ in range(4))
            else:
                for valor in (f["base_exenta_bienes"], cero, f["base_exenta_servicios"] + f["base_no_gravada"], cero):
                    s += _campo(valor, 15)
        s += _campo("", 10) + _campo("", 15) + _campo("", 15)
        if f["tipo"] != "P.CONTRIB":
            s += "".join(_campo("", 15) for _ in range(4))
        elif anulado:
            s += "".join(_campo(cero, 15) for _ in range(4))
        else:
            s += _campo(f["total_bienes"], 15) + _campo(f["total_servicios"], 15) + _campo(cero, 15) + _campo(cero, 15)
        lineas.append(s)
    return _archivo(r, "C", lineas)


def sat_ventas_2013(p):
    """sat_iva_ventas_2013: libro de ventas para Asiste Libros versión 2."""
    r = _Iva(p, "V")
    filas_bd = r.libro("sat2013")
    _sin_datos(filas_bd, "compras" if r.clasificacion == "C" else "ventas")
    sucursal = p.sucursal or 1
    lineas = []
    for f in filas_bd:
        tipo = f["tipo"].upper()
        anulado = f["anulado"] == 1
        doc = f["tipo_doc"]
        nit = f["nit"].replace("-", "")
        if doc in ("DA", "FA", "FE") or nit == "C/F" or tipo == "EXPORTACION":
            nit = "CF"
        cero = 0
        s = _campo_v(f"{sucursal:>2}", 2, False) + _campo_v("V", 1) + _campo_v(doc, 10)
        s += _campo_v(f["serie_doc"] or 0, 16) + _campo_v(f["numero_doc"].replace("-", "").strip(), 25)
        s += _campo_v(_texto_fecha(f["fechadoc"]), 10) + _campo_v("" if doc == "FE" else nit, 12)
        s += _campo_v(f["nombre"], 100) + _campo_v("E" if tipo == "EXPORTACION" else "L", 1)
        if tipo == "EXPORTACION":
            s += _campo_v("BIEN" if f["base_exenta_bienes"] > 0 else "SERVICIO", 10)
        else:
            s += _campo_v("", 1)
        s += _campo_v("A" if anulado else "E", 1)
        if doc == "FE":
            s += _campo_v(f["orden_cedula"] or 0, 10) + _campo_v(f["registro_cedula"] or 0, 12)
        else:
            s += _campo_v("", 4) + _campo_v("", 12)
        if tipo == "EXPORTACION" and f["base_exenta_bienes"] > 0:
            s += _campo_v("FAUCA" if f["tipodocimp"] == "FAUCA" else "DUA", 10)
            s += _campo_v(f["numdocimp"] or "0", 50)
        else:
            s += _campo_v("", 10) + _campo_v("", 50)
        if anulado:
            s += "".join(_campo_v("", 15) for _ in range(8))
        elif tipo == "P.CONTRIB":
            s += "".join(_campo_v(cero, 15) for _ in range(8))
        elif tipo == "EXPORTACION":
            for valor in (cero, f["total_bienes"], cero, f["total_servicios"], cero, f["base_exenta_bienes"], cero,
                          f["base_exenta_servicios"] + f["base_no_gravada"]):
                s += _campo_v(valor, 15)
        else:
            s += _campo_v(f["total_bienes"], 15) + _campo_v(cero, 15) + _campo_v(f["total_servicios"], 15) + _campo_v(cero, 15)
            if doc == "FE":
                s += "".join(_campo_v(cero, 15) for _ in range(4))
            else:
                for valor in (f["base_exenta_bienes"], cero, f["base_exenta_servicios"] + f["base_no_gravada"], cero):
                    s += _campo_v(valor, 15)
        if f["retiva_numero"] > 0:
            s += _campo_v("CRIVA", 10) + _campo_v(int(f["retiva_numero"]), 15) + _campo_v(f["retiva_valorml"], 15)
        elif f["no_constancia_exencion"] > 0:
            s += _campo_v("CEXE", 10) + _campo_v(int(f["no_constancia_exencion"]), 15)
            s += _campo_v(f["valor_constancia_exencion"], 15)
        else:
            s += _campo_v("", 10) + _campo_v("", 15) + _campo_v("", 15)
        if f["tipo"] != "P.CONTRIB":
            s += "".join(_campo_v("", 15) for _ in range(4))
        else:
            s += _campo_v(f["total_bienes"], 15) + _campo_v(f["total_servicios"], 15) + _campo_v(cero, 15) + _campo_v(cero, 15)
        lineas.append(s)
    return _archivo(r, "V", lineas)


def _encabezado_sat(r):
    return f"{r.emp.nit.strip().replace('-', '')}|01|{r.p.mes:02d}|{r.p.anio:04d}"


def sat_compras(p):
    """sat_iva_compras: libro de compras para Asiste Libros (versión 1, con encabezado NIT|01|MM|AAAA)."""
    r = _Iva(p, "C")
    filas_bd = r.libro("sat1_compras")
    _sin_datos(filas_bd, "compras" if r.clasificacion == "C" else "ventas")
    r.aplica_signos(filas_bd)
    lineas = [_encabezado_sat(r)]
    for f in filas_bd:
        doc = f["tipo_doc"]
        nit = "CF" if doc in ("DA", "FA", "FE") else f["nit"].replace("-", "")
        s = _campo("C", 1, False) + _campo(doc, 10) + _campo(f["serie_doc"] or 0, 10)
        s += _campo(f["numero_doc"].replace("-", "").strip(), 15) + _campo(_texto_fecha(f["fechadoc"]), 10)
        s += _campo(nit, 12) + _campo(f["nombre"], 100)
        s += _campo("I" if f["tipo"] == "IMPORTACION" else "L", 1) + _campo("", 1)
        if doc == "FE":
            s += _campo(f["orden_cedula"] or 0, 10) + _campo(f["registro_cedula"] or 0, 12)
        else:
            s += _campo(0, 4) + _campo(0, 12)
        s += _campo("", 10) + _campo("", 50)
        for valor in (f["base_gravada_bienes"], f["base_gravada_servicios"], f["base_exenta_bienes"],
                      f["base_exenta_servicios"] + f["base_no_gravada"]):
            s += _campo(valor, 15)
        s += _campo(0, 15) + _campo(0, 15) + _campo(f["iva"], 15)
        lineas.append(s)
    return _archivo(r, "C", lineas)


def sat_ventas(p):
    """sat_iva_ventas: libro de ventas para Asiste Libros (versión 1)."""
    r = _Iva(p, "V")
    filas_bd = r.libro("sat1_ventas", sucursal_por_serie=True, orden_sql="t.tipo_doc_iva, t.serie, t.numero")
    _sin_datos(filas_bd, "compras" if r.clasificacion == "C" else "ventas")
    r.aplica_signos(filas_bd)
    lineas = [_encabezado_sat(r)]
    for f in filas_bd:
        doc, tipo = f["tipo_doc"], f["tipo"]
        tipodocimp, numdocimp, nombre = f["tipodocimp"], f["numdocimp"], f["nombre"]
        if f["anulado"] == 1:
            estado, nit, nombre, tipodocimp, numdocimp, tipo = "A", "CF", "ANULADA", "", "", "L"
        else:
            estado, nit = "E", f["nit"].replace("-", "")
            if tipodocimp:
                tipodocimp = "FA" if tipodocimp == "FAUCA" else "DA"
                nit = "CF"
                numdocimp = numdocimp or f["numero_doc"][:10].strip()
        s = _campo("V", 1, False) + _campo(doc, 10) + _campo(f["serie_doc"] or 0, 10) + _campo(f["numero_doc"], 15)
        s += _campo(_texto_fecha(f["fechadoc"]), 10) + _campo(nit, 12) + _campo(nombre, 100)
        s += _campo(tipo[:1], 1) + _campo(estado, 1) + _campo(0, 4) + _campo(0, 12)
        s += _campo(tipodocimp, 10) + _campo(numdocimp, 50)
        anulado = f["anulado"] == 1
        if f["tipo"][:1] == "L":
            montos = (0, 0, 0, 0) if anulado else (f["base_gravada_bienes"], f["base_gravada_servicios"],
                                                   f["base_exenta_bienes"], f["base_exenta_servicios"])
        else:
            montos = (0, 0) + ((0, 0) if anulado else (f["base_exenta_bienes"], f["base_exenta_servicios"]))
        s += "".join(_campo(m, 15) for m in montos)
        s += _campo(0, 15) + _campo(0, 15) + _campo(f["iva"], 15)
        lineas.append(s)
    return _archivo(r, "V", lineas)
