from django.urls import include, path

urlpatterns = [
    path("", include("core.urls")),
    path("financieros/", include("financieros.urls")),
]
