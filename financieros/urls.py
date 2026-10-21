from django.urls import path

from . import buscar, views, views_junta

urlpatterns = [
    path("buscar/<str:tipo>/", buscar.buscar, name="buscar"),
    path("junta/", views_junta.paquete, name="junta"),
    path("<str:comando>/", views.reporte, name="reporte"),
]
