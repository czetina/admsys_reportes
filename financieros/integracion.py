"""
Integración de cuentas contables (menú 35 – Libros Auxiliares de vfmenuc).

  con_dmg_integracion     Por cuenta: saldo inicial, cada partida del rango y saldo final.
  con_dmg_integracion_td  Hoja "Datos" plana (Tabla de Excel) + hoja "Analisis" por cuenta y módulo.

Respecto a VFP:
* Una consulta para el detalle y UNA para los saldos iniciales de todas las cuentas
  (VFP ejecutaba 2 a 4 consultas por cuenta en _calcular_saldos_cuenta).
* El saldo inicial de enero suma la póliza de apertura cuando ésta no se lista en el
  detalle; en VFP la fecha final tenía fijo el año 2010 y la apertura nunca se sumaba.
* El saldo inicial excluye pólizas anuladas.
* "Solo saldo inicial": el saldo va en la columna SALDO INICIAL (VFP lo ponía en VALOR DOC.).
* TD: la utilidad del ejercicio se agrega como fila propia; VFP sobrescribía cargo/abono
  de una partida real de la cuenta de ganancia si ya tenía movimientos.
* TD: la tabla dinámica nativa se reemplaza por la hoja "Analisis" ya calculada (misma
  estructura: cuenta principal > cuenta, columnas por módulo); "Datos" queda como Tabla de
  Excel para crear otras dinámicas con un clic.
"""
from collections import OrderedDict, defaultdict
from datetime import date

from core.db import consulta, consulta_uno

from . import contexto as ctx
from .contexto import ErrorReporte, fin_de_mes
from .modelo import Columna, Fila, Libro
from .motor import ORDEN_ER
from .reportes import Base
from .saldos import Movimientos, utilidad

ORDENES = {
    1: "d.codcuenta, t.fecmov, t.codtipo, t.serie, t.numero",
    2: "d.codcuenta, d.coddirectorio, t.fecmov, t.codtipo, t.serie, t.numero",
    3: "d.codcuenta, g.razonsocial, t.fecmov, t.codtipo, t.serie, t.numero",
    4: "d.codcuenta, t.codtipo, t.serie, t.numero, t.fecmov",
}

SQL_DETALLE = """
SELECT d.codcuenta, d.cargo, d.abono, d.codperiodo, d.descripcioncont,
       d.coddirectorio AS coddir_pol, d.codclasifica AS clasif_pol,
       d.keyref AS keyref_pol, d.keyaux AS keyaux_pol,
       IFNULL(g.razonsocial, '') AS nombre_pol,
       n.nomcta, n.naturalezacta, n.codctamayor, IFNULL(n1.nomcta, '') AS nomcta_mayor,
       t.fecmov, t.coddirectorio, t.nombre, t.codclasifica, t.codtipo, t.serie, t.numero, t.codmoneda,
       t.valordocml, t.usacnt, t.actualizadocnt, t.codsistema, t.cuenta_aux, t.keytra, t.keyref, t.keyaux,
       t.descontable, t.docref, t.nit,
       IFNULL(g2.razonsocial, '') AS nombre_dir, IFNULL(g2.nit, '') AS nit_dir
       {cxp_campos}
  FROM dettransacciones d
  JOIN transacciones t ON t.codempresa = d.codempresa AND t.keytra = d.keytra
  JOIN empnomenclatura n
    ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
  LEFT JOIN empnomenclatura n1
    ON n1.codempresa = n.codempresa AND n1.codperiodo = n.codperiodo AND n1.codcuenta = n.codctamayor
  LEFT JOIN grudirectorio g
    ON g.codgrupo = d.codgrupo AND g.coddirectorio = d.coddirectorio AND d.coddirectorio <> ''
  LEFT JOIN grudirectorio g2
    ON g2.codgrupo = t.codgrupo AND g2.coddirectorio = t.coddirectorio AND t.coddirectorio <> ''
  {cxp_join}
 WHERE t.codempresa = %s
   AND t.fecmov BETWEEN %s AND %s
   {filtros}
 ORDER BY {orden}
"""

SQL_CXP_CAMPOS = ", IFNULL(q.codpresup, '') AS codpresup, IFNULL(gc.razonsocial, '') AS nombre_cliente"
SQL_CXP_JOIN = """
  LEFT JOIN liq_quedan q ON q.keytra_proveedor = t.keytra
  LEFT JOIN grudirectorio gc ON gc.codgrupo = q.codgrupo AND gc.coddirectorio = q.coddirectoriocli"""


class _Integracion(Base):
    def __init__(self, p):
        super().__init__(p)
        self.fecha_fin = self.corte.fecha
        if p.anio_ini and p.mes_ini:                     # "Mostrar rango de periodos"
            self.fecha_ini = date(p.anio_ini, p.mes_ini, 1)
        else:                                            # "Meses a reportar"
            meses = max(1, min(p.meses or 1, 12))
            a, m = p.anio, p.mes - (meses - 1)
            while m < 1:
                a, m = a - 1, m + 12
            self.fecha_ini = date(a, m, 1)
        if self.fecha_ini > self.fecha_fin:
            raise ErrorReporte("La fecha inicial es posterior a la fecha final.")
        self.periodo_ini = ctx.periodo(p.codempresa, self.fecha_ini.year, self.fecha_ini.month)
        # Póliza de apertura: se excluye del detalle al iniciar en el primer mes del ejercicio
        # (salvo "Incluir póliza de apertura") y entonces forma parte del saldo inicial.
        self.excluye_apertura = (self.fecha_ini == self.periodo_ini.fecini and not p.incluye_apertura)

    def _filtros(self, alias_cuenta="d.codcuenta"):
        p, partes, params = self.p, [], []
        for valor, sql in ((p.cuenta_desde, f"{alias_cuenta} >= %s"), (p.cuenta_hasta, f"{alias_cuenta} <= %s")):
            if valor:
                partes.append("AND " + sql)
                params.append(valor)
        return partes, params

    def detalle(self, cxp=False):
        p = self.p
        partes, params = self._filtros()
        for valor, sql in ((p.directorio_desde, "t.coddirectorio >= %s"), (p.directorio_hasta, "t.coddirectorio <= %s")):
            if valor:
                partes.append("AND " + sql)
                params.append(valor)
        if not p.incluye_desactualizados:
            partes.append("AND t.actualizadocnt = 1")
        if self.excluye_apertura:
            partes.append("AND t.codtipodoctos NOT IN ('AP', 'PA')")
        sql = SQL_DETALLE.format(
            cxp_campos=SQL_CXP_CAMPOS if cxp else "", cxp_join=SQL_CXP_JOIN if cxp else "",
            filtros="\n   ".join(partes), orden=ORDENES.get(p.orden, ORDENES[1]),
        )
        return consulta(sql, (self.emp.codempresa, self.fecha_ini, self.fecha_fin, *params))

    def saldos_iniciales(self, cuentas=None):
        """{cuenta: saldo} antes de fecha_ini dentro del ejercicio, según la naturaleza (D/A) de la cuenta."""
        partes, params = self._filtros()
        apertura = ""
        if self.excluye_apertura:
            apertura = "OR (t.codtipodoctos IN ('AP', 'PA') AND t.fecmov BETWEEN %s AND %s)"
            params_ap = [self.fecha_ini, fin_de_mes(self.fecha_ini.year, self.fecha_ini.month)]
        else:
            params_ap = []
        filtro_ctas = ""
        if cuentas:
            filtro_ctas = f"AND d.codcuenta IN ({','.join(['%s'] * len(cuentas))})"
        filas = consulta(
            f"""
            SELECT d.codcuenta, MAX(n.naturalezacta) AS naturaleza,
                   SUM(d.cargo) AS cargo, SUM(d.abono) AS abono
              FROM dettransacciones d
              JOIN transacciones t ON t.codempresa = d.codempresa AND t.keytra = d.keytra
              JOIN empnomenclatura n
                ON n.codempresa = d.codempresa AND n.codperiodo = d.codperiodo AND n.codcuenta = d.codcuenta
             WHERE t.codempresa = %s AND d.codperiodo = %s
               AND t.actualizadocnt = 1 AND t.anulado = 0
               AND (t.fecmov < %s {apertura})
               {' '.join(partes)} {filtro_ctas}
             GROUP BY d.codcuenta
            """,
            (self.emp.codempresa, self.periodo_ini.codperiodo, self.fecha_ini, *params_ap, *params, *(cuentas or [])),
        )
        out = {}
        for f in filas:
            c, a = float(f["cargo"] or 0), float(f["abono"] or 0)
            out[f["codcuenta"]] = round(c - a if (f["naturaleza"] or "").strip() == "D" else a - c, 2)
        return out

    def subtitulo(self):
        return f"PERIODO: {self.fecha_ini.strftime('%d/%m/%Y')} - {self.fecha_fin.strftime('%d/%m/%Y')}"


def _texto(v):
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def integracion_cuentas(p):
    """con_dmg_integracion."""
    r = _Integracion(p)
    cxp = p.mostrar_cxp
    filas_bd = r.detalle(cxp)
    nomnit = (consulta_uno("SELECT nomnit FROM empparametrosgenerales WHERE codempresa = %s",
                           (r.emp.codempresa,)) or {}).get("nomnit") or "NIT"

    columnas = [
        Columna("NOMBRE", ancho=43, texto=True), Columna("CLASIFICACIÓN", ancho=20, texto=True),
        Columna("NOMBRE CLIENTE", ancho=30, texto=True), Columna("PRESUPUESTO", ancho=14, texto=True),
        Columna("TIPO", ancho=9, texto=True), Columna("SERIE", ancho=10, texto=True),
        Columna("NÚMERO", ancho=14, texto=True), Columna("MON", ancho=6, texto=True),
        Columna("VALOR DOC."), Columna("SALDO INICIAL"), Columna("CARGO"), Columna("ABONO"), Columna("SALDO FINAL"),
        Columna("CNT", ancho=6, texto=True), Columna("ACT", ancho=6, texto=True), Columna("SIS.", ancho=6, texto=True),
        Columna("CUENTA AUX.", ancho=20, texto=True), Columna("LLAVE PRINCIPAL", ancho=30, texto=True),
        Columna("LLAVE REF.", ancho=30, texto=True), Columna("LLAVE AUX.", ancho=30, texto=True),
        Columna("CONCEPTO", ancho=44, texto=True), Columna("DOC. REF", ancho=30, texto=True),
        Columna(nomnit.strip().upper(), ancho=16, texto=True),
    ]
    ncols = len(columnas)

    por_cuenta = OrderedDict()
    for f in filas_bd:
        por_cuenta.setdefault(f["codcuenta"], []).append(f)

    if not por_cuenta:
        # VFP: con una sola cuenta ofrece ver únicamente el saldo inicial
        if not (p.cuenta_desde and p.cuenta_desde == p.cuenta_hasta):
            raise ErrorReporte("No se encontraron registros con los parámetros ingresados.")
        nom = ctx.nomenclatura(r.emp.codempresa, r.periodo_ini.codperiodo)
        cta = next((c for c in nom if c.codigo == p.cuenta_desde), None)
        if cta is None:
            raise ErrorReporte("La cuenta no existe en la nomenclatura del periodo.")
        naturaleza = (consulta_uno("SELECT naturalezacta FROM empnomenclatura WHERE codempresa=%s AND codperiodo=%s "
                                   "AND codcuenta=%s", (r.emp.codempresa, r.periodo_ini.codperiodo, cta.codigo))
                      or {}).get("naturalezacta", "D")
        cuentas_info = {cta.codigo: (cta.nombre, naturaleza)}
        por_cuenta[cta.codigo] = []
    else:
        cuentas_info = {c: (fs[0]["nomcta"], fs[0]["naturalezacta"]) for c, fs in por_cuenta.items()}

    saldos = r.saldos_iniciales(list(por_cuenta))
    filas, encabezados, detalles = [], [], []
    for cuenta, movs in por_cuenta.items():
        nombre, naturaleza = cuentas_info[cuenta]
        deudora = (naturaleza or "").strip() == "D"
        enc = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=cuenta, cuenta="", nombre=cuenta,
                   valores=[None] * ncols)
        enc.valores[0] = nombre
        filas.append(enc)
        bloque = []
        for f in movs:
            v = [None] * ncols
            v[0] = _texto(f["nombre_pol"]) or _texto(f["nombre"]) or _texto(f["nombre_dir"])
            v[1] = _texto(f["clasif_pol"]) or _texto(f["codclasifica"])
            if cxp:
                v[2], v[3] = _texto(f.get("nombre_cliente")), _texto(f.get("codpresup"))
            v[4], v[5] = _texto(f["codtipo"]), _texto(f["serie"])
            v[6] = str(int(f["numero"] or 0))
            v[7] = _texto(f["codmoneda"])
            v[8] = float(f["valordocml"] or 0)
            v[10] = float(f["cargo"]) if f["cargo"] else None
            v[11] = float(f["abono"]) if f["abono"] else None
            v[13] = "SI" if f["usacnt"] == 1 else "NO"
            v[14] = "SI" if f["actualizadocnt"] == 1 else "NO"
            v[15], v[16] = _texto(f["codsistema"]), _texto(f["cuenta_aux"])
            v[17] = _texto(f["keytra"])
            v[18] = _texto(f["keyref_pol"]) or _texto(f["keyref"])
            v[19] = _texto(f["keyaux_pol"]) or _texto(f["keyaux"])
            v[20] = _texto(f["descripcioncont"]) or _texto(f["descontable"])[:60]
            v[21], v[22] = _texto(f["docref"]), _texto(f["nit"]) or _texto(f["nit_dir"])
            fecha = f["fecmov"].strftime("%d/%m/%Y") if hasattr(f["fecmov"], "strftime") else _texto(f["fecmov"])
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=cuenta, cuenta=fecha,
                               nombre=_texto(f["coddir_pol"]) or _texto(f["coddirectorio"]), valores=v))
        filas += bloque
        filas.append(Fila())

        saldo_ini = saldos.get(cuenta, 0.0)
        cargos = round(sum(b.valores[10] or 0 for b in bloque), 2)
        abonos = round(sum(b.valores[11] or 0 for b in bloque), 2)
        enc.valores[8] = round(sum(b.valores[8] or 0 for b in bloque), 2)
        enc.valores[9] = saldo_ini
        enc.valores[10], enc.valores[11] = cargos, abonos
        enc.valores[12] = round(saldo_ini + cargos - abonos if deudora else saldo_ini - cargos + abonos, 2)
        enc.formulas[12] = ("lineal", [(1, 9), (1, 10), (-1, 11)] if deudora else [(1, 9), (-1, 10), (1, 11)])
        if bloque:
            for k in (8, 10, 11):
                enc.formulas[k] = ("subtotal", bloque[0], bloque[-1])
        encabezados.append(enc)
        detalles += bloque

    total = Fila(tipo="total", nombre="TOTALES", negrita=True, valores=[None] * ncols)
    for k in (8, 9, 10, 11, 12):
        total.valores[k] = round(sum((e.valores[k] or 0) for e in encabezados), 2)
    total.rango = (filas[0], filas[-2])
    filas.append(total)

    rep = r.reporte("INTEGRACION DE CUENTAS CONTABLES", r.subtitulo(), columnas, filas)
    rep.encabezado_a, rep.encabezado_b = ("FECHA", 13), ("CÓDIGO", 22)
    rep.grupos_columnas = [(2, 3, "CLIENTE ASOCIADO CXP"), (4, 6, "DOCUMENTO")]
    rep.columnas_ocultas = set()
    if not p.mostrar_llaves:
        rep.columnas_ocultas |= {17, 18, 19}
    if not cxp:
        rep.columnas_ocultas |= {2, 3}
    rep.fecha_hora = None
    return rep


def integracion_cuentas_td(p):
    """con_dmg_integracion_td: hoja Datos plana + hoja Analisis (cuenta principal > cuenta x módulo)."""
    r = _Integracion(p)
    filas_bd = r.detalle()
    activo = r.nom.grupos.get("activo") or "1"
    titulo = (p.nombre or "INTEGRACION DE CUENTAS CONTABLES").strip()

    datos = []
    for f in filas_bd:
        c, a = float(f["cargo"] or 0), float(f["abono"] or 0)
        deudora = (f["naturalezacta"] or "").strip() == "D"
        ym = f["fecmov"].year * 100 + f["fecmov"].month
        datos.append({
            "anio": str(ym // 100), "mes": f"{ym % 100:02d}-{ym // 100}", "fecha": f["fecmov"].strftime("%d/%m/%Y"),
            "codigo": _texto(f["coddir_pol"]) or _texto(f["coddirectorio"]),
            "no_cuenta_principal": _texto(f["codctamayor"]), "cuenta_principal": _texto(f["nomcta_mayor"]),
            "no_cuenta": f["codcuenta"], "cuenta": _texto(f["nomcta"]),
            "nombre": _texto(f["nombre_pol"]) or _texto(f["nombre"]),
            "clasificacion": _texto(f["clasif_pol"]) or _texto(f["codclasifica"]),
            "tipo": _texto(f["codtipo"]), "serie": _texto(f["serie"]), "numero": str(int(f["numero"] or 0)),
            "mon": _texto(f["codmoneda"]), "valor_doc": float(f["valordocml"] or 0),
            "neto": round(c - a if f["codcuenta"].startswith(activo) else a - c, 2),
            "cnt": "SI" if f["usacnt"] == 1 else "NO", "act": "SI" if f["actualizadocnt"] == 1 else "NO",
            "sis": _texto(f["codsistema"]), "cuenta_aux": _texto(f["cuenta_aux"]),
            "concepto": (_texto(f["descripcioncont"]) or _texto(f["descontable"]))[:60],
            "naturaleza_cuenta": _texto(f["naturalezacta"]),
            "cargo": round(c if deudora else -c, 2), "abono": round(-a if deudora else a, 2),
        })

    # Utilidad del rango en la cuenta de ganancia del ejercicio (fila propia)
    ganancia = r.periodo_ini.gananciaejercicio
    movs = Movimientos(r.emp.codempresa, r.fecha_ini, r.fecha_fin,
                       r.nom.prefijos(*[g for _, gs in ORDEN_ER for g in gs]), r.logn)
    ym_ini, ym_fin = r.fecha_ini.year * 100 + r.fecha_ini.month, r.fecha_fin.year * 100 + r.fecha_fin.month
    util = utilidad(movs, r.nom, ym_ini, ym_fin)
    if ganancia and util:
        nom = {c.codigo: c for c in ctx.nomenclatura(r.emp.codempresa, r.corte.periodo.codperiodo)}
        cta = nom.get(ganancia)
        mayor = nom.get(cta.mayor) if cta else None
        cargo, abono = (0.0, util) if util > 0 else (abs(util), 0.0)
        datos.append({
            "anio": str(r.fecha_fin.year), "mes": f"{r.fecha_fin.month:02d}-{r.fecha_fin.year}",
            "fecha": r.fecha_fin.strftime("%d/%m/%Y"), "codigo": "", "no_cuenta_principal": cta.mayor if cta else "",
            "cuenta_principal": mayor.nombre if mayor else "", "no_cuenta": ganancia, "cuenta": cta.nombre if cta else "",
            "nombre": "UTILIDAD DEL EJERCICIO", "clasificacion": "", "tipo": "", "serie": "", "numero": "", "mon": "",
            "valor_doc": 0.0, "neto": round(abono - cargo, 2), "cnt": "SI", "act": "NO", "sis": "CNT",
            "cuenta_aux": "", "concepto": "", "naturaleza_cuenta": "", "cargo": cargo, "abono": abono,
        })
    if not datos:
        raise ErrorReporte("No se encontraron registros con los parámetros ingresados.")

    campos = ["fecha", "codigo", "no_cuenta_principal", "cuenta_principal", "no_cuenta", "cuenta", "nombre",
              "clasificacion", "tipo", "serie", "numero", "mon", "valor_doc", "neto", "cnt", "act", "sis",
              "cuenta_aux", "concepto", "naturaleza_cuenta", "cargo", "abono"]
    numericos = {"valor_doc", "neto", "cargo", "abono"}
    anchos = {"concepto": 44, "cuenta": 34, "cuenta_principal": 34, "nombre": 34}
    hoja_datos = r.reporte(titulo, r.subtitulo(),
                           [Columna(c.upper(), ancho=anchos.get(c, 14), texto=c not in numericos) for c in campos],
                           [Fila(tipo="cuenta", cuenta=d["anio"], nombre=d["mes"], valores=[d[c] for c in campos])
                            for d in datos])
    hoja_datos.hoja, hoja_datos.tabla_plana = "Datos", True
    hoja_datos.encabezado_a, hoja_datos.encabezado_b = ("ANIO", 8), ("MES", 10)
    hojas = [hoja_datos]

    if p.genera_td:
        hojas.insert(0, _analisis(r, titulo, datos))
    return Libro(titulo, hojas, f"{titulo} {r.emp.codempresa} {p.anio}-{p.mes:02d}.xlsx")


def _analisis(r, titulo, datos):
    """Suma de NETO por cuenta principal > cuenta (filas) y módulo SIS (columnas), como la tabla dinámica de VFP."""
    modulos = sorted({d["sis"] for d in datos})
    suma = defaultdict(lambda: defaultdict(float))
    nombres, principal = {}, {}
    for d in datos:
        suma[d["no_cuenta"]][d["sis"]] += d["neto"]
        nombres[d["no_cuenta"]] = d["cuenta"]
        principal[d["no_cuenta"]] = (d["no_cuenta_principal"], d["cuenta_principal"])

    columnas = [Columna(m or "(SIN MÓDULO)", ancho=16) for m in modulos]
    columnas.append(Columna("TOTAL", formula=("suma", 0, len(modulos) - 1)))
    n = len(columnas)
    filas, padres = [], []
    for cod_p in sorted({v[0] for v in principal.values()}):
        hijos = sorted(c for c, v in principal.items() if v[0] == cod_p)
        padre = Fila(tipo="cuenta", nivel=1, negrita=True, codigo=cod_p, cuenta=r.nom.formatea(cod_p),
                     nombre=principal[hijos[0]][1] or cod_p, valores=[0.0] * n)
        filas.append(padre)
        bloque = []
        for cod in hijos:
            v = [round(suma[cod].get(m, 0.0), 2) for m in modulos]
            v.append(round(sum(v), 2))
            bloque.append(Fila(tipo="cuenta", nivel=2, codigo=cod, cuenta=r.nom.formatea(cod),
                               nombre="  " + nombres[cod], valores=v))
        filas += bloque
        padre.valores = [round(sum(b.valores[k] for b in bloque), 2) for k in range(n)]
        padre.hasta = bloque[-1]
        padres.append(padre)
    total = Fila(tipo="total", nombre="TOTAL GENERAL", negrita=True, rango=(filas[0], filas[-1]),
                 valores=[round(sum(pd.valores[k] for pd in padres), 2) for k in range(n)])
    filas += [Fila(), total]
    rep = r.reporte(titulo, f"{r.subtitulo()} · Suma de VALOR_NETO por MÓDULO", columnas, filas)
    rep.hoja = "Analisis"
    rep.fecha_hora = None
    return rep
