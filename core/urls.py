from django.urls import path

from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("cambiar-empresa/", views.cambiar_empresa, name="cambiar_empresa"),
]
