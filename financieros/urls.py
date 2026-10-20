from django.urls import path

from . import buscar, views

urlpatterns = [
    path("buscar/<str:tipo>/", buscar.buscar, name="buscar"),
    path("<str:comando>/", views.reporte, name="reporte"),
]
