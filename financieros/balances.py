"""
Balances del menú 35 – Libros Auxiliares:

  con_balance_de_saldos_xls             Balance de Saldos
  con_balance_de_comprobacion_xls       Balance de Comprobación (misma lógica en VFP)
  con_balance_de_saldos_analitico_xls   Balance Analítico: cuentas CXC/CXP abiertas por directorio

Columnas: saldo anterior, cargos, abonos, saldo del mes y saldo actual, para todas las
cuentas con movimiento en el ejercicio. En el primer mes del ejercicio la póliza de
apertura (codtipo AP/PA) forma el saldo anterior y no se cuenta como movimiento.

Una sola consulta de movimientos (separando la apertura) en lugar de la vista
v_balance_saldos, que agrupa todas las empresas y meses (1.3 s por consulta en 003).
"""
from collections import defaultdict

from core.db import consulta

from .contexto import ErrorReporte
from .modelo import Columna, Fila
from .motor import finaliza
from .reportes import Base
from .saldos import FiltroMovs, Movimientos

DEUDORAS = ("activo", "regularizacionpasivo", "costos", "gastos", "egresos", "otrosegresos", "ordendeudoras")
ACREEDORAS = ("pasivo", "capital", "regularizacionactivo", "ingresos", "otrosingresos", "ordenacreedoras")


class _Balance(Base):
    def cargar(self):
        c = self.corte
        prefijos = self.nom.prefijos(*DEUDORAS, *ACREEDORAS)
        self.movs = Movimientos(self.emp.codempresa, c.periodo.fecini, c.fecha, prefijos, self.logn,
                                FiltroMovs(separa_apertura=True))
        self.deudoras = set(self.nom.prefijos(*DEUDORAS))
        self.prefijos = set(prefijos)
        activas = {cta for cta in self.movs.cuentas(c.ini, c.fin) if cta[: self.logn] in self.prefijos}
        if not activas:
            raise ErrorReporte("No existen datos para los parámetros indicados.")
        return activas

    def valores(self, cuenta):
        """[saldo anterior, cargos, abonos, saldo del mes, saldo actual] como en VFP."""
        c, m = self.corte, self.corte.fin
        deud = cuenta[: self.logn] in self.deudoras
        if m == c.ini:     # primer mes del ejercicio: saldo anterior = póliza de apertura
            anterior = self.movs.neto(cuenta, c.ini, m, deud, cc="AP")
        else:
            anterior = self.movs.neto(cuenta, c.ini, self._mes_anterior(m), deud)
        cargos, abonos = self.movs.sumas(cuenta, m, m, cc="")
        mes = round(cargos - abonos if deud else abonos - cargos, 2)
        return [anterior, cargos, abonos, mes, round(anterior + mes, 2)]

    @staticmethod
    def _mes_anterior(ym):
        return ym - 89 if ym % 100 == 1 else ym - 1

    def columnas(self):
        return [Columna("SALDO ANTERIOR"), Columna("CARGOS"), Columna("ABONOS"),
                Columna("SALDO DEL MES"), Columna("SALDO ACTUAL")]

    def lineas(self, activas):
        con_hijos = {a[:i] for a in activas for i in range(1, len(a) + 1)}
        return [cta for cta in self.nomenclatura() if cta.codigo[: self.logn] in self.prefijos and cta.codigo in con_hijos]

    def armar(self, lineas, activas, expandir=None):
        """Filas en orden de código, línea en blanco antes de cada cuenta de nivel 1, subtotales por mayor."""
        filas = []
        for cta in lineas:
            if len(cta.codigo) == self.logn and filas:
                filas.append(Fila())
            fila = Fila(tipo="cuenta", codigo=cta.codigo, cuenta=self.nom.formatea(cta.codigo),
                        nombre=" " * len(cta.codigo) + cta.nombre, nivel=cta.nivel, oculta=cta.nivel > self.nivel)
            if cta.ultimo:
                fila.valores = self.valores(cta.codigo) if cta.codigo in activas else [None] * 5
            else:
                fila.hasta = fila
                fila.negrita = cta.nivel < self.nivel
            filas.append(fila)
            if cta.ultimo and expandir:
                hijos = expandir(cta, fila)
                if hijos:
                    fila.hasta, fila.negrita = fila, True
                    filas += hijos
        cuentas = [f for f in filas if f.tipo == "cuenta"]
        total = Fila(tipo="total", nombre="TOTALES", negrita=True, rango=(cuentas[0], cuentas[-1]))
        filas += [Fila(), total]
        finaliza(filas, self.columnas())
        total.valores = [None, total.valores[1], total.valores[2], None, None]
        return filas


def _balance_simple(p, titulo):
    b = _Balance(p)
    activas = b.cargar()
    filas = b.armar(b.lineas(activas), activas)
    c = b.corte
    return b.reporte(titulo, f"DEL MES [{c.fecha.month:02d}/{c.fecha.year}]", b.columnas(), filas)


def balance_de_saldos(p):
    """con_balance_de_saldos_xls."""
    return _balance_simple(p, "BALANCE DE SALDOS")


def balance_de_comprobacion(p):
    """con_balance_de_comprobacion_xls (idéntico en cálculo al Balance de Saldos)."""
    return _balance_simple(p, "BALANCE DE COMPROBACIÓN")


SQL_AUXILIAR = """
SELECT t.cuenta_aux, t.coddirectorio, MAX(g.razonsocial) AS razonsocial,
       SUM(IF(t.filectacorriente = %s AND t.fecmov <  %s, t.valordocml - t.noaplica_ml, 0)) AS cargos_ini,
       SUM(IF(t.filectacorriente = %s AND t.fecmov <  %s, t.valordocml - t.noaplica_ml, 0)) AS abonos_ini,
       SUM(IF(t.filectacorriente = %s AND t.fecmov >= %s, t.valordocml - t.noaplica_ml, 0)) AS cargos,
       SUM(IF(t.filectacorriente = %s AND t.fecmov >= %s, t.valordocml - t.noaplica_ml, 0)) AS abonos
  FROM transacciones t
  JOIN grudirectorio g ON g.codgrupo = t.codgrupo AND g.coddirectorio = t.coddirectorio
 WHERE t.codempresa = %s
   AND t.usactacorriente = %s
   AND t.filectacorriente IN (%s, %s)
   AND t.numero <> 0
   AND t.anulado = 0
   AND TRIM(t.codsistema) <> 'PFA'
   AND t.fecmov <= %s
   AND t.cuenta_aux IN ({cuentas})
 GROUP BY t.cuenta_aux, t.coddirectorio
"""


def balance_analitico(p):
    """
    con_balance_de_saldos_analitico_xls: Balance de Saldos donde cada cuenta con auxiliar
    de cuenta corriente (empnomenclatura.codsistema CXC/CXP y usaauxiliarcta = 1) se abre por
    cliente/proveedor (valordocml de los documentos MOVDE/MOVHA, como _saldos_cuenta_corriente),
    más una fila de ajuste para cuadrar con contabilidad.
    Una consulta para todos los auxiliares; VFP llamaba al estado de cuenta por cada cuenta.
    """
    b = _Balance(p)
    activas = b.cargar()
    c = b.corte
    inicio_mes = c.fecha.replace(day=1)
    aux = {f["codcuenta"]: f["codsistema"].upper() for f in consulta(
        """SELECT codcuenta, codsistema FROM empnomenclatura
            WHERE codempresa = %s AND codperiodo = %s AND usaauxiliarcta = 1
              AND FIND_IN_SET(codsistema, 'CXC,CXP')""",
        (b.emp.codempresa, c.periodo.codperiodo))}
    aux = {cta: sis for cta, sis in aux.items() if cta in activas}

    detalle = defaultdict(list)
    for sistema in ("CXC", "CXP"):
        cuentas = [cta for cta, sis in aux.items() if sis == sistema]
        if not cuentas:
            continue
        de, ha = f"{sistema}MOVDE", f"{sistema}MOVHA"
        sql = SQL_AUXILIAR.format(cuentas=",".join(["%s"] * len(cuentas)))
        for f in consulta(sql, (de, inicio_mes, ha, inicio_mes, de, inicio_mes, ha, inicio_mes,
                                b.emp.codempresa, sistema[-1], de, ha, c.fecha, *cuentas)):
            detalle[f["cuenta_aux"]].append(f)

    def expandir(cta, fila_cuenta):
        sistema = aux.get(cta.codigo)
        if not sistema:
            return []
        cxc = sistema == "CXC"
        formulas = {3: ("resta", 1, 2) if cxc else ("resta", 2, 1),
                    4: ("lineal", [(1, 0), (1, 1), (-1, 2)] if cxc else [(1, 0), (-1, 1), (1, 2)])}
        hijos, sumas = [], [0.0, 0.0, 0.0]
        for f in sorted(detalle.get(cta.codigo, []), key=lambda x: x["coddirectorio"]):
            ci, ai = float(f["cargos_ini"] or 0), float(f["abonos_ini"] or 0)
            ini = round(ci - ai if cxc else ai - ci, 2)
            car, abo = round(float(f["cargos"] or 0), 2), round(float(f["abonos"] or 0), 2)
            hijos.append(_fila_aux(cta, f["coddirectorio"], f["razonsocial"] or "", ini, car, abo, cxc, formulas))
            sumas = [sumas[0] + ini, sumas[1] + car, sumas[2] + abo]
        cnt = b.valores(cta.codigo)
        ajuste = [round(cnt[0] - sumas[0], 2), round(cnt[1] - sumas[1], 2), round(cnt[2] - sumas[2], 2)]
        hijos.append(_fila_aux(cta, "", "*** AJUSTE POR DIFERENCIA (CONTABILIDAD Y AUXILIAR) ***",
                               *ajuste, cxc, formulas))
        return hijos

    filas = b.armar(b.lineas(activas), activas, expandir)
    return b.reporte("BALANCE ANALÍTICO", f"DEL MES [{c.fecha.month:02d}/{c.fecha.year}]", b.columnas(), filas)


def _fila_aux(cta, coddirectorio, nombre, ini, cargos, abonos, cxc, formulas):
    mes = round(cargos - abonos if cxc else abonos - cargos, 2)
    return Fila(tipo="cuenta", codigo=f"{cta.codigo}|{coddirectorio}", cuenta=coddirectorio,
                nombre=" " * (len(cta.codigo) + 2) + nombre.strip(), nivel=cta.nivel + 1,
                valores=[ini, cargos, abonos, mes, round(ini + mes, 2)], formulas=dict(formulas))


# ------------------------------------------------------------ Libro mayor resumido por directorio

SQL_DIR_MOVS = """
SELECT x.codcuenta, x.coddirectorio, MAX(x.codclasifica) AS codclasifica,
       MAX(IFNULL(g.razonsocial, '')) AS razonsocial, MAX(IFNULL(dc.cuenta_ref, '')) AS cuenta_ref,
       SUM(IF(x.fecmov <  %s, x.cargo, 0)) AS cargos_ini, SUM(IF(x.fecmov <  %s, x.abono, 0)) AS abonos_ini,
       SUM(IF(x.fecmov >= %s, x.cargo, 0)) AS cargos,     SUM(IF(x.fecmov >= %s, x.abono, 0)) AS abonos
  FROM (SELECT d.codcuenta, t.fecmov, d.cargo, d.abono,
               IF(d.keyref <> '', r.codgrupo, t.codgrupo) AS codgrupo,
               IF(d.keyref <> '', r.coddirectorio, t.coddirectorio) AS coddirectorio,
               IF(d.keyref <> '', r.codclasifica, t.codclasifica) AS codclasifica
          FROM transacciones t
          JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
          LEFT JOIN transacciones r ON r.keytra = d.keyref AND d.keyref <> ''
         WHERE t.codempresa = %s AND t.anulado = 0
           AND ((t.usacnt = 1 AND t.actualizadocnt = 1) OR (t.usacnt = 0 AND t.actualizadocnt = 0))
           AND t.fecmov BETWEEN %s AND %s
           AND d.codcuenta IN ({cuentas})) x
  LEFT JOIN grudirectorio g ON g.codgrupo = x.codgrupo AND g.coddirectorio = x.coddirectorio
  LEFT JOIN grudirectorioclasifica dc
    ON dc.codgrupo = x.codgrupo AND dc.coddirectorio = x.coddirectorio AND dc.codclasifica = x.codclasifica
 WHERE IFNULL(x.coddirectorio, '') <> ''
 GROUP BY x.codcuenta, x.coddirectorio
"""

SQL_DIR_DOCS = """
SELECT d.codcuenta, d.cargo, d.abono, t.fecmov, t.serie, t.numero, t.docref, LEFT(t.descontable, 80) AS descontable,
       IF(d.keyref <> '', r.coddirectorio, t.coddirectorio) AS coddirectorio
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
  LEFT JOIN transacciones r ON r.keytra = d.keyref AND d.keyref <> ''
 WHERE t.codempresa = %s AND t.anulado = 0
   AND ((t.usacnt = 1 AND t.actualizadocnt = 1) OR (t.usacnt = 0 AND t.actualizadocnt = 0))
   AND t.fecmov BETWEEN %s AND %s
   AND d.codcuenta IN ({cuentas})
 ORDER BY t.fecmov, t.serie, t.numero, d.iddettransacciones
"""


def _limpia(texto):
    return " ".join((texto or "").split())


def mayor_por_directorio(p):
    """
    con_dmg_integracion_x_directorio (plantilla «DMG Resumido x Directorio.xls»): balance de saldos del
    mes; las cuentas CXC/CXP con auxiliar se abren por directorio (cliente / proveedor) y las de bancos
    (CHQ) muestran sus documentos del mes. «Ver detalladamente» agrega los documentos de cada directorio.

    VFP: 3 a 6 consultas por cuenta y por directorio; aquí, tres en total. Diferencias:
    * El saldo por directorio sale de las partidas contables del ejercicio (incluida la apertura cuando
      trae directorio); lo que no se puede asignar a un directorio va en una fila de ajuste, así la
      cuenta cuadra con contabilidad. VFP sumaba la póliza de apertura completa a CADA directorio cuando
      la cuenta no tenía clasificaciones, y el saldo de cuenta corriente no coincide con contabilidad
      en cuentas reclasificadas (003, 1.01.02.01.03: 18.5 millones de diferencia).
    * Las partidas que aplican a otro documento (keyref) se filtran por la fecha de la partida; VFP usaba
      la fecha del documento referido.
    """
    b = _Balance(p)
    activas = {cta for cta in b.cargar() if (not p.cuenta_desde or cta >= p.cuenta_desde)
               and (not p.cuenta_hasta or cta <= p.cuenta_hasta)}
    if not activas:
        raise ErrorReporte("No existen datos para los parámetros indicados.")
    c = b.corte
    inicio_mes, inicio_ej = c.fecha.replace(day=1), c.periodo.fecini
    aux = {f["codcuenta"]: f["codsistema"].strip().upper() for f in consulta(
        """SELECT codcuenta, codsistema FROM empnomenclatura
            WHERE codempresa = %s AND codperiodo = %s AND usaauxiliarcta = 1
              AND FIND_IN_SET(codsistema, 'CXC,CXP,CHQ')""", (b.emp.codempresa, c.periodo.codperiodo))
        if f["codcuenta"] in activas}
    con_dir = [cta for cta, s in aux.items() if s in ("CXC", "CXP")]
    con_docs = [cta for cta, s in aux.items() if s == "CHQ"] + (con_dir if p.ver_detallado else [])

    directorios = defaultdict(dict)        # cuenta -> coddirectorio -> datos
    if con_dir:
        marcas = ",".join(["%s"] * len(con_dir))
        for f in consulta(SQL_DIR_MOVS.format(cuentas=marcas),
                          (inicio_mes, inicio_mes, inicio_mes, inicio_mes, b.emp.codempresa, inicio_ej, c.fecha,
                           *con_dir)):
            directorios[f["codcuenta"]][f["coddirectorio"]] = dict(f)
    documentos = defaultdict(list)          # cuenta CHQ | (cuenta, coddirectorio) -> partidas del mes
    if con_docs:
        for f in consulta(SQL_DIR_DOCS.format(cuentas=",".join(["%s"] * len(con_docs))),
                          (b.emp.codempresa, inicio_mes, c.fecha, *con_docs)):
            clave = f["codcuenta"] if aux[f["codcuenta"]] == "CHQ" else (f["codcuenta"], f["coddirectorio"])
            documentos[clave].append(f)

    columnas = b.columnas() + [Columna("DOCUMENTO", ancho=16, texto=True),
                               Columna("REFERENCIA", ancho=18, texto=True), Columna("FECHA", ancho=11, texto=True)]
    vacio = [None] * len(columnas)

    def fila_doc(cta, f, nivel):
        v = list(vacio)
        v[1] = float(f["cargo"]) if f["cargo"] else None
        v[2] = float(f["abono"]) if f["abono"] else None
        numero = str(int(f["numero"] or 0))
        v[5:] = [f"{(f['serie'] or '').strip()} {numero}".strip(), (f["docref"] or "").strip(),
                 f["fecmov"].strftime("%d/%m/%Y")]
        return Fila(tipo="cuenta", nivel=nivel, codigo=f"{cta.codigo}|doc", valores=v, oculta=nivel > b.nivel,
                    nombre=" " * (len(cta.codigo) + 8) + _limpia(f["descontable"]))

    lineas = b.lineas(activas)
    valores = {cta.codigo: b.valores(cta.codigo) for cta in lineas if cta.ultimo and cta.codigo in activas}
    if p.no_saldo_cero:     # «Ocultar cuentas sin movimiento»: VFP quitaba las de saldo actual cero
        valores = {k: v for k, v in valores.items() if v[4]}
        lineas = [cta for cta in lineas if any(h.startswith(cta.codigo) for h in valores)]
    filas, hojas, mayores = [], [], []
    for cta in lineas:
        if len(cta.codigo) == b.logn and filas:
            filas.append(Fila())
        fila = Fila(tipo="cuenta", codigo=cta.codigo, cuenta=b.nom.formatea(cta.codigo),
                    nombre=" " * len(cta.codigo) + cta.nombre, nivel=cta.nivel, oculta=cta.nivel > b.nivel,
                    negrita=cta.nivel < b.nivel)
        filas.append(fila)
        if not cta.ultimo:
            mayores.append(fila)
            continue
        fila.valores = valores[cta.codigo] + [None, None, None]
        hojas.append(fila)
        deud = cta.codigo[: b.logn] in b.deudoras
        hijos = []
        if aux.get(cta.codigo) == "CHQ":
            hijos = [fila_doc(cta, f, cta.nivel + 1) for f in documentos.get(cta.codigo, [])]
            if hijos:
                fila.formulas = {1: ("subtotal", hijos[0], hijos[-1]), 2: ("subtotal", hijos[0], hijos[-1])}
        elif cta.codigo in directorios:
            sumas = [0.0, 0.0, 0.0]
            datos = sorted(directorios[cta.codigo].values(),
                           key=lambda d: ((d["codclasifica"] or "").strip(),
                                          (d["cuenta_ref"] or "").strip() or d["coddirectorio"]))
            for d in datos:
                ci, ai = float(d["cargos_ini"] or 0), float(d["abonos_ini"] or 0)
                ini = round(ci - ai if deud else ai - ci, 2)
                car, abo = round(float(d["cargos"] or 0), 2), round(float(d["abonos"] or 0), 2)
                mes = round(car - abo if deud else abo - car, 2)
                h = Fila(tipo="cuenta", nivel=cta.nivel + 1, codigo=f"{cta.codigo}|{d['coddirectorio']}",
                         cuenta=(d["cuenta_ref"] or "").strip() or d["coddirectorio"],
                         nombre=" " * (len(cta.codigo) + 4) + (d["razonsocial"] or "").strip(),
                         valores=[ini, car, abo, mes, round(ini + mes, 2), None, None, None],
                         oculta=cta.nivel + 1 > b.nivel)
                hijos.append(h)
                sumas = [sumas[0] + ini, sumas[1] + car, sumas[2] + abo]
                if p.ver_detallado:
                    docs = [fila_doc(cta, f, cta.nivel + 2)
                            for f in documentos.get((cta.codigo, d["coddirectorio"]), [])]
                    if docs:
                        h.negrita = True
                        h.formulas = {1: ("subtotal", docs[0], docs[-1]), 2: ("subtotal", docs[0], docs[-1])}
                        hijos += docs
            v = fila.valores
            ajuste = [round(v[0] - sumas[0], 2), round(v[1] - sumas[1], 2), round(v[2] - sumas[2], 2)]
            if any(ajuste):
                mes = round(ajuste[1] - ajuste[2] if deud else ajuste[2] - ajuste[1], 2)
                hijos.append(Fila(tipo="cuenta", nivel=cta.nivel + 1, codigo=f"{cta.codigo}|",
                                  nombre=" " * (len(cta.codigo) + 4)
                                  + "*** AJUSTE POR DIFERENCIA (CONTABILIDAD Y AUXILIAR) ***",
                                  valores=[*ajuste, mes, round(ajuste[0] + mes, 2), None, None, None],
                                  oculta=cta.nivel + 1 > b.nivel))
            if hijos:
                fila.negrita = True
                fila.formulas = {k: ("subtotal", hijos[0], hijos[-1]) for k in range(5)}
        filas += hijos

    # Cuentas mayores: suma de sus cuentas de detalle; en Excel SUBTOTAL hasta su último descendiente
    for fila in mayores:
        hijas = [h for h in hojas if h.codigo.startswith(fila.codigo)]
        fila.valores = [round(sum(h.valores[k] for h in hijas), 2) for k in range(5)] + [None, None, None]
        j = filas.index(fila)
        while j + 1 < len(filas) and filas[j + 1].tipo == "cuenta" and filas[j + 1].codigo.startswith(fila.codigo):
            j += 1
        fila.hasta = filas[j]
    total = Fila(tipo="total", nombre="TOTALES", negrita=True, rango=(filas[0], filas[-1]),
                 valores=[None, round(sum(h.valores[1] for h in hojas), 2),
                          round(sum(h.valores[2] for h in hojas), 2), None, None, None, None, None])
    filas += [Fila(), total]
    rep = b.reporte("LIBRO MAYOR GENERAL", f"DEL MES [{c.fecha.month:02d}/{c.fecha.year}]", columnas, filas)
    if not con_docs:
        rep.columnas_ocultas = {5, 6, 7}
    return rep


# ------------------------------------------------------------ Libros fiscales (menú 30) sobre el balance

def _libro_balance(p, titulo, clase=None):
    """
    Libro Diario / Mayor resumido y Libro de Inventario: el Balance de Saldos (mismas cuentas, misma
    regla de apertura) con folio y resolución; si el mes no tiene movimientos, la hoja «sin movimientos».
    """
    from .libros import _sin_movimientos
    b = (clase or _Balance)(p)
    try:
        activas = b.cargar()
    except ErrorReporte:
        c = b.corte
        return _sin_movimientos(b, titulo, f"DEL MES [{c.fecha.month:02d}/{c.fecha.year}]", b.columnas(),
                                ("CUENTA", 17), ("NOMBRE", 60))
    return b, activas


def _cierra_libro(b, titulo, subtitulo, columnas, filas):
    rep = b.reporte(titulo, subtitulo, columnas, filas)
    rep.folio = b.p.folio or 0
    rep.resolucion = b.p.resolucion
    return rep


def libro_diario_resumido(p):
    """con_libro_diario_resumen_xls (Libro Diario - Resumido.xls)."""
    return _libro_resumido(p, "LIBRO DIARIO")


def libro_mayor_resumido(p):
    """con_libro_mayor_resumen_xls: en VFP es una copia exacta del diario resumido con otra plantilla."""
    return _libro_resumido(p, "LIBRO MAYOR")


def _libro_resumido(p, titulo):
    r = _libro_balance(p, titulo)
    if not isinstance(r, tuple):
        return r
    b, activas = r
    c = b.corte
    return _cierra_libro(b, titulo, f"DEL MES [{c.fecha.month:02d}/{c.fecha.year}]", b.columnas(),
                         b.armar(b.lineas(activas), activas))


SQL_DIAS = """
SELECT d.codcuenta, t.fecmov, SUM(d.cargo) AS cargo, SUM(d.abono) AS abono
  FROM transacciones t
  JOIN dettransacciones d ON d.keytra = t.keytra AND d.codempresa = t.codempresa
 WHERE t.codempresa = %s AND t.actualizadocnt = 1 AND t.anulado = 0 AND d.codcuenta <> ''
   AND t.fecmov BETWEEN %s AND %s AND t.codtipo NOT IN ('AP', 'PA')
   AND LEFT(d.codcuenta, %s) IN ({prefijos})
 GROUP BY d.codcuenta, t.fecmov
 ORDER BY d.codcuenta, t.fecmov
"""


class _BalanceDias(_Balance):
    """Balance con dos columnas más para los cargos / abonos de cada día bajo la cuenta."""

    def columnas(self):
        return super().columnas() + [Columna("CARGOS DEL DÍA"), Columna("ABONOS DEL DÍA")]

    def valores(self, cuenta):
        return super().valores(cuenta) + [None, None]


def libro_mayor_por_dia(p):
    """
    con_libro_mayor_resumen_xls_diario (Libro Mayor movimientos diarios): el balance del mes y, bajo
    cada cuenta de detalle, una fila por día con sus cargos y abonos. Los del día van en columnas
    propias para que los subtotales de las cuentas mayores no los sumen dos veces.
    """
    titulo = "LIBRO MAYOR MOVIMIENTOS DIARIOS"
    r = _libro_balance(p, titulo, _BalanceDias)
    if not isinstance(r, tuple):
        return r
    b, activas = r
    c = b.corte
    inicio = c.fecha.replace(day=1)
    prefijos = sorted(b.prefijos)
    dias = defaultdict(list)
    for f in consulta(SQL_DIAS.format(prefijos=",".join(["%s"] * len(prefijos))),
                      (b.emp.codempresa, inicio, c.fecha, b.logn, *prefijos)):
        dias[f["codcuenta"]].append(f)

    filas = []
    for f in b.armar(b.lineas(activas), activas):
        filas.append(f)
        if f.tipo in ("cuenta", "total"):
            # los del día solo se leen en las filas de cada día (sin subtotales que los repitan)
            f.valores = (f.valores + [None, None])[:5] + [None, None]
        if f.tipo == "cuenta" and f.hasta is None:
            for d in dias.get(f.codigo, []):
                v = [None] * 7
                v[5], v[6] = round(float(d["cargo"] or 0), 2), round(float(d["abono"] or 0), 2)
                filas.append(Fila(tipo="detalle", nivel=f.nivel + 1, oculta=f.oculta, valores=v,
                                  nombre=" " * (len(f.codigo) + 2) + d["fecmov"].strftime("%d/%m/%Y")))
    return _cierra_libro(b, titulo, f"DEL MES [{c.fecha.month:02d}/{c.fecha.year}]", b.columnas(), filas)


INGRESOS = ("ingresos", "otrosingresos")
EGRESOS = ("costos", "gastos", "egresos", "otrosegresos")


class _Inventario(_Balance):
    """Balance de saldos con la utilidad del ejercicio en la cuenta de ganancia del periodo."""

    def cargar(self):
        activas = super().cargar()
        c = self.corte
        self.ganancia = c.periodo.gananciaejercicio
        ing, egr = set(self.nom.prefijos(*INGRESOS)), set(self.nom.prefijos(*EGRESOS))

        def resultado(desde, hasta):
            if hasta < desde:
                return 0.0
            return round(self.movs.neto_grupo(ing, self.logn, desde, hasta, False)
                         - self.movs.neto_grupo(egr, self.logn, desde, hasta, True), 2)

        self.u_anterior = resultado(c.ini, self._mes_anterior(c.fin)) if c.fin != c.ini else 0.0
        self.u_mes = round(resultado(c.ini, c.fin) - self.u_anterior, 2)
        if self.ganancia and (self.u_anterior or self.u_mes):
            activas.add(self.ganancia)
        return activas

    def valores(self, cuenta):
        v = super().valores(cuenta) if self.movs.tiene(cuenta, self.corte.ini, self.corte.fin) else [0.0] * 5
        if cuenta == self.ganancia:
            # resultado del ejercicio: anterior al mes + resultado del mes (no toca cargos / abonos)
            v[0] = round(v[0] + self.u_anterior, 2)
            v[3] = round(v[3] + self.u_mes, 2)
            v[4] = round(v[0] + v[3], 2)
        return v


def libro_inventario(p):
    """con_libro_inventario_xls (Libro de Inventarios.xls)."""
    titulo = "LIBRO DE INVENTARIO"
    r = _libro_balance(p, titulo, _Inventario)
    if not isinstance(r, tuple):
        return r
    b, activas = r
    c = b.corte
    if p.incluir_todas:
        lineas = [cta for cta in b.nomenclatura() if cta.codigo[: b.logn] in b.prefijos]
    else:
        lineas = b.lineas(activas)
    return _cierra_libro(b, titulo, f"AL {c.fecha.strftime('%d/%m/%Y')}", b.columnas(),
                         b.armar(lineas, activas))
