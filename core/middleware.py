from django.shortcuts import redirect

from .auth import usuario_actual

RUTAS_LIBRES = ("/login", "/static/")


class UsuarioSistemaMiddleware:
    """Exige sesión iniciada (usuario + empresa) en todo excepto el login."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.usuario = usuario_actual(request)
        if request.usuario is None and not request.path.startswith(RUTAS_LIBRES):
            return redirect("login")
        return self.get_response(request)
