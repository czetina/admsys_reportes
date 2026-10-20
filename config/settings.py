"""
Reportes financieros ADMSys (migración desde Visual FoxPro 9).

La base vadmon se lee con PyMySQL directo (core/db.py), no con el ORM:
Django 5.2 ya no soporta MariaDB 10.4 y así no se crean tablas en producción.
Las sesiones viven en una cookie firmada, por eso no hay migraciones.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# ADMSYS_PRODUCCION=1 (definida en el servicio del servidor, no en un archivo): no se lee ningún
# .env, todos los secretos deben venir de variables de entorno o del gestor de secretos, DEBUG
# queda apagado y se activan las protecciones de HTTPS. Sin ella, todo funciona como en desarrollo.
PRODUCCION = os.environ.get("ADMSYS_PRODUCCION", "0") == "1"

if not PRODUCCION:
    from dotenv import load_dotenv
    # ADMSYS_ENV elige otro archivo de ambiente (p. ej. .env.servirsa para probar contra otra base)
    load_dotenv(BASE_DIR / os.environ.get("ADMSYS_ENV", ".env"))


def _obligatoria(nombre, minimo=1):
    """En producción, una variable sin valor (o demasiado corta) detiene el arranque."""
    valor = os.environ.get(nombre, "")
    if PRODUCCION and len(valor) < minimo:
        raise ImproperlyConfigured(f"Falta la variable de entorno {nombre}"
                                   + (f" (mínimo {minimo} caracteres)" if minimo > 1 else "") + ".")
    return valor


# La sesión vive en una cookie firmada con SECRET_KEY: quien la conozca puede fabricar sesiones.
SECRET_KEY = _obligatoria("DJANGO_SECRET_KEY", 50) or "solo-para-desarrollo-cambiar"
DEBUG = not PRODUCCION and os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in (_obligatoria("DJANGO_ALLOWED_HOSTS") or "localhost,127.0.0.1").split(",")
                 if h.strip()]

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "financieros",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.UsuarioSistemaMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.sesion",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Sin base para Django: todo el acceso a vadmon pasa por core.db
DATABASES = {}

VADMON = {
    "host": _obligatoria("VADMON_HOST") or "localhost",
    "port": int(os.environ.get("VADMON_PORT", "3306")),
    "database": _obligatoria("VADMON_DB") or "vadmon",
    "user": _obligatoria("VADMON_USER"),          # en producción: usuario con solo SELECT (deploy/)
    "password": _obligatoria("VADMON_PASSWORD"),
}
# Clave con la que VFP cifra las contraseñas de vfusers: en producción no hay valor por defecto
VADMON_BASE_ENCRYPT = _obligatoria("VADMON_BASE_ENCRYPT")       # secreto: solo en .env / variables de entorno

# Franja que indica a qué base está conectada la web: "desarrollo", "produccion" o "pruebas".
# Vacía o sin definir = no se muestra (así queda al publicar en producción).
ADMSYS_AMBIENTE = os.environ.get("ADMSYS_AMBIENTE", "").strip().lower()

# Carpeta conta\plantillas del sistema VFP: de ahí se leen datos que las plantillas fijan
# (p. ej. las cuentas de Inversión de "Ejecucion Presupuestaria Detalle.xls").
VFP_PLANTILLAS_DIR = os.environ.get("VFP_PLANTILLAS_DIR", r"C:\vfappn\admsys\conta\plantillas")
# Respaldo si no se encuentra la plantilla: "cuenta:nombre;cuenta:nombre"
PRESUPUESTO_CUENTAS_INVERSION = os.environ.get("PRESUPUESTO_CUENTAS_INVERSION", "")

SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_HTTPONLY = True
# Nombre propio por ambiente: las cookies no distinguen puerto, así dos servidores locales
# contra bases distintas no se pisan la sesión.
SESSION_COOKIE_NAME = os.environ.get("DJANGO_SESSION_COOKIE", "sessionid")
CSRF_COOKIE_NAME = SESSION_COOKIE_NAME + "_csrf"
SESSION_COOKIE_AGE = 8 * 60 * 60
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

if PRODUCCION:
    # Solo HTTPS: redirige http, cookies que no viajan sin cifrar, HSTS y cabeceras de seguridad
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SSL_REDIRECT", "1") == "1"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = True
    # Empezar con poco (1 hora) y subir a 31536000 (un año) cuando HTTPS esté probado
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if not h.startswith(".")]
    # Detrás de IIS / nginx que termina el HTTPS y avisa con X-Forwarded-Proto
    if os.environ.get("DJANGO_DETRAS_DE_PROXY", "0") == "1":
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Sesión más corta y que termina al cerrar el navegador
    SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_HORAS", "4")) * 60 * 60
    SESSION_EXPIRE_AT_BROWSER_CLOSE = True

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Guatemala"
USE_I18N = True
USE_TZ = False

STATIC_URL = "static/"
# Destino de collectstatic al publicar (lo sirve IIS / nginx); en desarrollo no se usa
STATIC_ROOT = os.environ.get("DJANGO_STATIC_ROOT") or BASE_DIR / "staticfiles"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"financieros": {"handlers": ["console"], "level": "INFO"}},
}
