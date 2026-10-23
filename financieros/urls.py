from django.urls import path

from . import buscar, views, views_junta, views_tablero

urlpatterns = [
    path("buscar/<str:tipo>/", buscar.buscar, name="buscar"),
    path("junta/", views_junta.paquete, name="junta"),
    path("tablero-cfo/", views_tablero.pagina, name="tablero_cfo"),
    path("tablero-cfo/datos/", views_tablero.datos, name="tablero_cfo_datos"),
    path("tablero-cfo/excel/", views_tablero.excel, name="tablero_cfo_excel"),
    path("<str:comando>/", views.reporte, name="reporte"),
]
