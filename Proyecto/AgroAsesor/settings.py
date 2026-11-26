from pathlib import Path
import os
import sys
from urllib.parse import urlparse
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
# En Vercel (que siempre define VERCEL=1) el valor por defecto es False: si alguien olvida DJANGO_DEBUG, producción no
# queda con DEBUG activo (trazas de error públicas, cookies sin Secure). En local se mantiene True por comodidad.
DEBUG = (os.getenv('DJANGO_DEBUG') or ('False' if os.getenv('VERCEL') else 'True')).strip().lower() == 'true'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Configura DJANGO_SECRET_KEY cuando DEBUG=False.')
    SECRET_KEY = get_random_secret_key()

# SECURITY WARNING: don't run with debug turned on in production!

ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
CSRF_TRUSTED_ORIGINS = [o for o in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if o]

# GitHub Codespaces: la página se abre por una dirección https://<nombre>-8000.app.github.dev
# (no por localhost), así que hay que aceptar ese dominio y su origen para los formularios.
if os.getenv('CODESPACES') == 'true':
    ALLOWED_HOSTS += ['.app.github.dev']
    CSRF_TRUSTED_ORIGINS += ['https://*.app.github.dev']
    USE_X_FORWARDED_HOST = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Vercel (o cualquier proxy HTTPS): el proxy entrega la petición a Django por HTTP aunque el navegador usó HTTPS. Sin
# esta línea ``request.is_secure()`` es falso y las direcciones absolutas (retorno de Google, enlace del correo de
# recuperación) salen con http:// y no coinciden con las autorizadas. Vercel envía X-Forwarded-Proto (https en
# producción); solo se confía en esa cabecera cuando la plataforma lo indica (VERCEL=1) o se pide expresamente.
if os.getenv('VERCEL') or os.getenv('DJANGO_CONFIAR_PROXY_HTTPS', '').strip().lower() == 'true':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


def _direccion_publica(texto):
    """``https://dominio`` válido o cadena vacía (un valor mal escrito no debe tumbar el sitio)."""
    texto = (texto or '').strip().rstrip('/')
    partes = urlparse(texto)
    if texto and partes.scheme in ('http', 'https') and partes.hostname and not partes.path.strip('/'):
        return f'{partes.scheme}://{partes.netloc}'
    if texto:
        print(
            'AVISO: SITE_URL debe ser solo el dominio con https, por ejemplo https://agroasesor.vercel.app; se ignora.',
            file=sys.stderr,
        )
    return ''


# Dirección pública del sitio (recomendado en producción): fija el dominio de los enlaces del correo de recuperación y
# la URL de retorno de Google, sin depender de las cabeceras de la petición. También se acepta como host y origen de
# confianza. Ejemplo: SITE_URL=https://agroasesor.vercel.app (sin barra final ni ruta).
SITE_URL = _direccion_publica(os.getenv('SITE_URL'))
if SITE_URL:
    _sitio = urlparse(SITE_URL)
    if _sitio.hostname not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_sitio.hostname)
    if SITE_URL not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(SITE_URL)
MAP_TILE_URL = os.getenv(
    'MAP_TILE_URL',
    'https://tile.openstreetmap.de/{z}/{x}/{y}.png',
).strip()
# Google Maps JavaScript API (mapa de predios y potreros). La clave se lee SOLO de variables de entorno (.env no
# versionado); también se acepta VITE_GOOGLE_MAPS_API_KEY. Sin clave se usa el mapa de respaldo (MAP_TILE_URL).
GOOGLE_MAPS_API_KEY = (os.getenv('GOOGLE_MAPS_API_KEY') or os.getenv('VITE_GOOGLE_MAPS_API_KEY') or '').strip()
# Map ID propio de Google Cloud (marcadores avanzados). Vacío: las pruebas usan DEMO_MAP_ID; en producción defínelo.
GOOGLE_MAPS_MAP_ID = os.getenv('GOOGLE_MAPS_MAP_ID', '').strip()
MAP_TILE_ATTRIBUTION = os.getenv(
    'MAP_TILE_ATTRIBUTION',
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
).strip()
LOGIN_URL = 'login'
CSRF_FAILURE_VIEW = 'core.auth.fallo_csrf'
LOGIN_REDIRECT_URL = 'dashboard'
AUTHENTICATION_BACKENDS = ['core.auth.EmailBackend']
# Inicio de sesión con Google (OAuth 2.0). Las credenciales se leen SOLO de variables de entorno (.env no versionado).
# Sin ellas el botón «Continuar con Google» no aparece. Ver .env.example y el README.
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', '').strip()  # opcional: fija la URL de retorno
# HTTPS estricto en producción (DEBUG=False). La cabecera HSTS solo se envía en peticiones HTTPS, así que en local por
# http no tiene efecto. La redirección http→https solo se activa detrás de un proxy de confianza (Vercel o
# DJANGO_CONFIAR_PROXY_HTTPS): sin él, Django no sabe si la petición original era HTTPS y entraría en un bucle.
if not DEBUG:
    SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_HSTS_SEGUNDOS', '2592000') or 0)  # 30 días; 0 la desactiva
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('DJANGO_HSTS_SUBDOMINIOS', '').strip().lower() == 'true'
    SECURE_SSL_REDIRECT = globals().get('SECURE_PROXY_SSL_HEADER') is not None
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'core.auth.SesionVencidaMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'AgroAsesor.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # <-- Agrega esto aquí
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'AgroAsesor.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

import dj_database_url
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        # SSL obligatorio para PostgreSQL (Supabase), igual que antes. SQLite no admite «sslmode»: sin esta
        # excepción las pruebas automáticas (que usan SQLite) no pueden ni arrancar.
        ssl_require=not os.getenv("DATABASE_URL", "").startswith("sqlite"),
    )
}

# Solo existe UNA base oficial: la del volumen de Docker. Si se arranca sin DJANGO_DB_PATH (por ejemplo con
# «python manage.py runserver» directo) se usaría un db.sqlite3 local distinto; se avisa para no dispersar datos.
if not os.getenv('DJANGO_DB_PATH') and any(a in sys.argv for a in ('runserver', 'migrate', 'createsuperuser')):
    print(
        'AVISO: se está usando el db.sqlite3 LOCAL, que NO es la base oficial del proyecto. '
        'Para trabajar con la oficial usa «docker compose up -d».',
        file=sys.stderr,
    )


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'es-cl'

TIME_ZONE = 'America/Santiago'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = []
# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

# Las opciones del servidor (host, clave, etc.) solo sirven para SMTP; el backend de consola no las acepta.
# Sin EMAIL_BACKEND: si hay EMAIL_HOST se envía por SMTP (producción); si no, los correos solo se escriben en el log
# (desarrollo). Puerto 465 = SSL implícito; 587 u otros = STARTTLS. Nunca se escriben claves en el código ni en el log.
def _env_entero(nombre, defecto, minimo=1, maximo=65535):
    try:
        return min(max(int(os.getenv(nombre, '').strip() or defecto), minimo), maximo)
    except ValueError:
        return defecto


def _env_si(nombre, defecto):
    valor = os.getenv(nombre, '').strip().lower()
    return defecto if not valor else valor in ('1', 'true', 'si', 'sí', 'yes')


CORREO_HOST = os.getenv('EMAIL_HOST', '').strip()  # (no se llama EMAIL_HOST: Django 6.1 lo prohíbe junto a MAILERS)
CORREO_BACKEND = os.getenv('EMAIL_BACKEND', '').strip() or (
    'django.core.mail.backends.smtp.EmailBackend' if CORREO_HOST else 'django.core.mail.backends.console.EmailBackend'
)
CORREO_OPCIONES = {}
if CORREO_BACKEND.endswith('smtp.EmailBackend'):
    _puerto_correo = _env_entero('EMAIL_PORT', 587)
    _usa_ssl = _env_si('EMAIL_USE_SSL', _puerto_correo == 465)
    CORREO_OPCIONES = {
        'host': CORREO_HOST,
        'port': _puerto_correo,
        'use_tls': False if _usa_ssl else _env_si('EMAIL_USE_TLS', True),
        'use_ssl': _usa_ssl,
        'username': os.getenv('EMAIL_HOST_USER', '').strip(),
        'password': os.getenv('EMAIL_HOST_PASSWORD', '').strip(),
        'timeout': _env_entero('EMAIL_TIMEOUT', 10, 3, 30),
    }
MAILERS = {
    'default': {
        'BACKEND': CORREO_BACKEND,
        'OPTIONS': CORREO_OPCIONES,
    },
}
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'AgroAsesor <noreply@localhost>')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# API Flask (servicio interno de solo lectura detrás de Django). Opcional: sin estas variables, /api/v1/ responde 503
# y el resto de AgroAsesor funciona igual. La clave debe tener al menos 32 caracteres y NO se guarda en el código.
AGROASESOR_API_URL = os.getenv('AGROASESOR_API_URL', '').rstrip('/')
AGROASESOR_INTERNAL_SECRET = os.getenv('AGROASESOR_INTERNAL_SECRET', '')
AGROASESOR_API_TIMEOUT = float(os.getenv('AGROASESOR_API_TIMEOUT', '10'))

# Clima (Open-Meteo). Todo es opcional: sin variables se usan estos valores. Ver core/clima_compartido.py.
# Open-Meteo gratuito: 600 consultas/minuto, 5 000/hora y 10 000/día por dirección IP, y en Vercel muchas apps comparten
# direcciones de salida; por eso el resultado se guarda en una caché COMPARTIDA y se respeta el aviso de límite (429).
CLIMA_CACHE_SEGUNDOS = _env_entero('CLIMA_CACHE_SEGUNDOS', 900, 0, 86400)  # cuánto se reutiliza un pronóstico
CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS = _env_entero('CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS', 6 * 3600, 0, 7 * 86400)  # tope «datos anteriores»
CLIMA_PRECISION_COORDENADAS = _env_entero('CLIMA_PRECISION_COORDENADAS', 2, 1, 4)  # 2 decimales ≈ 1,1 km: predios cercanos comparten
CLIMA_TIMEOUT_SEGUNDOS = _env_entero('CLIMA_TIMEOUT_SEGUNDOS', 6, 2, 20)
CLIMA_ESPERA_LIMITE_SEGUNDOS = _env_entero('CLIMA_ESPERA_LIMITE_SEGUNDOS', 120, 30, 3600)  # pausa si Open-Meteo no indica cuánto
CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS = _env_entero('CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS', 1800, 60, 7200)
CLIMA_ESPERA_CONCURRENTE_SEGUNDOS = _env_entero('CLIMA_ESPERA_CONCURRENTE_SEGUNDOS', 3, 0, 10)  # espera acotada si otra petición ya consulta
CLIMA_CACHE_COMPARTIDA = _env_si('CLIMA_CACHE_COMPARTIDA', True)  # tabla en la base de datos; False = solo memoria local
CLIMA_API_KEY = os.getenv('OPEN_METEO_API_KEY', '').strip()

# Límite de intentos (ver core/limites.py). Se guarda en la tabla de caché compartida, común a todas las instancias.
# Cada valor es (máximo de intentos, ventana en segundos). LIMITE_INTENTOS_ACTIVO=False lo desactiva por completo.
LIMITE_INTENTOS_ACTIVO = _env_si('LIMITE_INTENTOS_ACTIVO', True)
LIMITE_LOGIN_CUENTA = (_env_entero('LIMITE_LOGIN_CUENTA', 8, 1, 1000), 15 * 60)  # fallos por usuario/correo
LIMITE_LOGIN_IP = (_env_entero('LIMITE_LOGIN_IP', 30, 1, 10000), 15 * 60)  # fallos por dirección IP
LIMITE_REGISTRO_IP = (_env_entero('LIMITE_REGISTRO_IP', 10, 1, 10000), 60 * 60)  # registros intentados por IP
LIMITE_RECUPERAR_IP = (_env_entero('LIMITE_RECUPERAR_IP', 10, 1, 10000), 60 * 60)  # solicitudes por IP
LIMITE_RECUPERAR_CORREO = (_env_entero('LIMITE_RECUPERAR_CORREO', 3, 1, 1000), 60 * 60)  # correos a una dirección  # opcional: clave de pago de Open-Meteo (cuenta comercial)
