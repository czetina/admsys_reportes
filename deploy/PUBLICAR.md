# Publicar la web de reportes en producción

Mientras se desarrolla y prueba, todo sigue igual: `.env` / `.env.servirsa` y `runserver`.
Esta lista se aplica **solo** al publicar.

## 0. Dominio

Dominio previsto: **`admsys-tecno.consultesa.com`**, con guion medio. Con guion bajo
(`admsys_tecno`) no funciona: Django rechaza el nombre (400 Bad Request en todas las páginas, aunque
esté en `ALLOWED_HOSTS`) y las autoridades certificadoras no emiten certificados HTTPS para nombres
con guion bajo desde 2019.

## 1. Usuario de base de datos de solo lectura

El DBA ejecuta [`usuario_solo_lectura.sql`](usuario_solo_lectura.sql) con la IP del servidor web y
una contraseña nueva. La web deja de usar el usuario actual de `.env.servirsa`.

## 2. Variables de entorno en el servidor (no archivos `.env`)

Se definen en el servicio que ejecuta Django (variables del pool de aplicaciones en IIS, servicio
de Windows, `systemd` en Linux) o en un gestor de secretos (Azure Key Vault, AWS Secrets Manager,
Vault). **No copiar** `.env` ni `.env.servirsa` al servidor.

| Variable | Valor |
|---|---|
| `ADMSYS_PRODUCCION` | `1` — activa el modo seguro; sin ella la web corre como en desarrollo |
| `DJANGO_SECRET_KEY` | 50+ caracteres aleatorios, nuevos (generar con el comando de abajo) |
| `DJANGO_ALLOWED_HOSTS` | `admsys-tecno.consultesa.com` (ver nota del dominio) |
| `VADMON_HOST`, `VADMON_PORT`, `VADMON_DB` | servidor, puerto y `NOMBRE_BASE_PRODUCCION` |
| `VADMON_USER`, `VADMON_PASSWORD` | `admsys_web` y la contraseña del paso 1 |
| `VADMON_BASE_ENCRYPT` | clave con la que VFP cifra las contraseñas de `vfusers` |
| `DJANGO_DETRAS_DE_PROXY` | `1` si IIS / nginx recibe el HTTPS y reenvía a Django |
| `DJANGO_HSTS_SECONDS` | `3600` al inicio; `31536000` cuando HTTPS esté probado |
| `DJANGO_SESSION_HORAS` | duración de la sesión (por defecto 4) |

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Si falta una variable obligatoria, Django **no arranca** y dice cuál falta (mejor que arrancar con
una clave de ejemplo).

## 3. Qué cambia con `ADMSYS_PRODUCCION=1`

* No se lee ningún archivo `.env`.
* `DEBUG` siempre apagado (los errores no muestran código ni variables).
* Solo HTTPS: redirección desde http, cookies de sesión y CSRF marcadas `Secure`, HSTS,
  `nosniff`, sin iframes (`X-Frame-Options: DENY`).
* Sesión de 4 horas que termina al cerrar el navegador.
* Sin valores por defecto para la clave de sesión ni para la clave de cifrado de VFP.

## 4. Antes de abrirla al público

* Cambiar las contraseñas que se usaron en desarrollo: la del usuario de base de `.env.servirsa`
  y la del usuario de prueba de la aplicación.
* No usar `runserver`: servidor WSGI (waitress en Windows, gunicorn en Linux) detrás de IIS / nginx
  con certificado HTTPS. Los archivos estáticos (`/static/`) los sirve IIS / nginx después de
  `collectstatic` (definir `STATIC_ROOT`).
* Que la carpeta del proyecto no esté dentro de la raíz pública del sitio.
* Verificación: `python manage.py check --deploy` con las variables de producción definidas.
