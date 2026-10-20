"""
Menú 30 – Libros Fiscales de Contabilidad.

Son los mismos cálculos de los reportes ya migrados, con lo que piden los libros autorizados:
folio inicial, número de resolución de la SAT en el encabezado y, si el mes no tiene movimientos,
una hoja «Período sin movimientos» para que la numeración de folios no se corte.

  con_libro_diario                     Libro Diario (Crystal)              libros.libro_diario
  con_libro_diario_resumen_xls         Libro Diario resumido               balances.libro_diario_resumido
  con_libro_mayor                      Libro Mayor (Crystal Dmg_Nombre)    libros.libro_mayor
  con_libro_mayor_resumen_xls          Libro Mayor resumido                balances.libro_mayor_resumido
  con_libro_mayor_resumen_xls_diario   Libro Mayor movimientos diarios     balances.libro_mayor_por_dia
  con_dmg_resumido                     DMG resumido por día (Crystal)      libros.diario_mayor_resumido
  con_libro_inventario_xls             Libro de Inventario                 balances.libro_inventario
  con_libro_estfin_bg_xls / _er_xls    Libro de Estados Financieros        aquí
  con_ef_balance_de_saldos_xls         Balance de Saldos - EF              aquí
"""
from . import reportes as r
from .balances import balance_de_saldos
from .contexto import ErrorReporte
from .libros import _sin_movimientos
from .modelo import Columna


def _libro(p, funcion, titulo):
    p.libro_fiscal = True
    try:
        rep = funcion(p)
    except ErrorReporte:
        b = r.Base(p)
        return _sin_movimientos(b, titulo, f"DEL MES [{p.mes:02d}/{p.anio}]", [Columna("")], ("CUENTA", 17),
                                ("NOMBRE", 60))
    rep.folio = p.folio or 0
    rep.resolucion = p.resolucion
    return rep


def libro_estados_bg(p):
    """con_libro_estfin_bg_xls: Balance General (acumulado o del mes) con folio y resolución."""
    return _libro(p, r.balance_general, "LIBRO DE ESTADOS FINANCIEROS - BALANCE GENERAL")


def libro_estados_er(p):
    """con_libro_estfin_er_xls: Estado de Resultados (acumulado o del mes) con folio y resolución."""
    return _libro(p, r.estado_resultados, "LIBRO DE ESTADOS FINANCIEROS - ESTADO DE RESULTADOS")


def balance_de_saldos_ef(p):
    """con_ef_balance_de_saldos_xls: el Balance de Saldos con folio (VFP no ofrece hoja sin movimientos)."""
    rep = balance_de_saldos(p)
    rep.folio = p.folio or 0
    return rep
