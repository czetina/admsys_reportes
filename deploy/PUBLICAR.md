# Publicar la web de reportes en producción

Mientras se desarrolla y prueba, todo sigue igual: `.env` / `.env.servirsa` y `runserver`.
Esta lista se aplica **solo** al publicar.

## 0. Dominio

Dominio previsto: **`admsys-tecno.consultesa.com`**, con guion medio. Con guion bajo
(`admsys_tecno`) no funciona: Django rechaza el nombre (400 Bad Request en todas las páginas, aunque
esté en `ALLOWED_HOSTS`) y las autoridades certificadoras no emiten certificados HTTPS para nombres
con guion bajo desde 2019.

## 1. Paquete para el instalador

```bash
python deploy/empaquetar.py
```

Genera `dist/admsys_reportes_<commit>.zip` a partir del último commit: solo código, plantillas,
estáticos, `requirements.txt` y esta guía. No lleva `.env`, `.env.servirsa`, `.venv`, pruebas ni el
README de desarrollo, y se cancela si encuentra alguno. Las credenciales **no** van en el paquete:
se entregan al instalador por un canal aparte y él las define como variables de entorno (paso 3).

En el servidor (Python 3.12):

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python manage.py collectstatic --noinput
.venv\Scripts\python manage.py check --deploy
```

No hay `migrate`: la web no crea tablas (sesiones en cookie firmada, lecturas directas a la base).

## 2. Usuario de base de datos de solo lectura

El DBA ejecuta `deploy/usuario_solo_lectura.sql` (se le entrega aparte, no va en el paquete) con la
IP del servidor web y una contraseña nueva.

**Mientras el DBA crea ese usuario**, se publica con el usuario actual de `.env.servirsa`. La web
no puede escribir aunque ese usuario tenga permisos: cada conexión abre la sesión con
`SET SESSION TRANSACTION READ ONLY` (`core/db.py`). Cuando llegue `admsys_web`, solo se cambian
`VADMON_USER` y `VADMON_PASSWORD` en el servidor, se reinicia el servicio y se cambia la contraseña
del usuario anterior (ya circuló fuera del servidor de base).

## 3. Variables de entorno en el servidor (no archivos `.env`)

Se definen en el servicio que ejecuta Django (variables del pool de aplicaciones en IIS, servicio
de Windows, `systemd` en Linux) o en un gestor de secretos (Azure Key Vault, AWS Secrets Manager,
Vault). **No copiar** `.env` ni `.env.servirsa` al servidor.

| Variable | Valor |
|---|---|
| `ADMSYS_PRODUCCION` | `1` — activa el modo seguro; sin ella la web corre como en desarrollo |
| `DJANGO_SECRET_KEY` | 50+ caracteres aleatorios, nuevos (generar con el comando de abajo) |
| `DJANGO_ALLOWED_HOSTS` | `admsys-tecno.consultesa.com` (ver nota del dominio) |
| `VADMON_HOST`, `VADMON_PORT`, `VADMON_DB` | servidor, puerto y `NOMBRE_BASE_PRODUCCION` |
| `VADMON_USER`, `VADMON_PASSWORD` | `admsys_web` y la contraseña del paso 2 (mientras tanto, los de `.env.servirsa`) |
| `VADMON_BASE_ENCRYPT` | clave con la que VFP cifra las contraseñas de `vfusers` |
| `DJANGO_DETRAS_DE_PROXY` | `1` si IIS / nginx recibe el HTTPS y reenvía a Django |
| `DJANGO_HSTS_SECONDS` | `3600` al inicio; `31536000` cuando HTTPS esté probado |
| `DJANGO_SESSION_HORAS` | duración de la sesión (por defecto 4) |
| `DJANGO_STATIC_ROOT` | carpeta donde `collectstatic` deja `/static/` para IIS / nginx |
| `VFP_PLANTILLAS_DIR` | carpeta `conta\plantillas` del sistema VFP, si el servidor la ve por red |
| `PRESUPUESTO_CUENTAS_INVERSION` | si no la ve: `cuenta:nombre;cuenta:nombre` de la hoja PRESUPUESTO de *Ejecucion Presupuestaria Detalle.xls* |
| `ADMSYS_AMBIENTE` | vacía (sin franja de ambiente) |

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Si falta una variable obligatoria, Django **no arranca** y dice cuál falta (mejor que arrancar con
una clave de ejemplo).

## 4. Qué cambia con `ADMSYS_PRODUCCION=1`

* No se lee ningún archivo `.env`.
* `DEBUG` siempre apagado (los errores no muestran código ni variables).
* Solo HTTPS: redirección desde http, cookies de sesión y CSRF marcadas `Secure`, HSTS,
  `nosniff`, sin iframes (`X-Frame-Options: DENY`).
* Sesión de 4 horas que termina al cerrar el navegador.
* Sin valores por defecto para la clave de sesión ni para la clave de cifrado de VFP.

## 5. Antes de abrirla al público

* Cambiar las contraseñas que se usaron en desarrollo: la del usuario de base de `.env.servirsa`
  y la del usuario de prueba de la aplicación.
* No usar `runserver`: servidor WSGI (waitress en Windows, gunicorn en Linux) detrás de IIS / nginx
  con certificado HTTPS. Los archivos estáticos (`/static/`) los sirve IIS / nginx después de
  `collectstatic` (definir `STATIC_ROOT`).
* Que la carpeta del proyecto no esté dentro de la raíz pública del sitio.
* Verificación: `python manage.py check --deploy` con las variables de producción definidas.
