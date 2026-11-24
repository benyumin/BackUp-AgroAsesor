"""AgroAsesor como aplicación instalable (PWA): manifest, service worker y pantalla sin conexión.

Todo es público (no requiere sesión: el navegador pide el manifest y el service worker sin credenciales) y no
guarda ni muestra datos de ninguna cuenta. El service worker solo conserva archivos estáticos públicos (CSS, JS,
imágenes, tipografías e íconos de CDN con versión fija) y la pantalla sin conexión; jamás páginas HTML de
la cuenta, la API, el inicio de sesión, Google ni la recuperación de contraseña.
"""

import hashlib
import json
import os

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_safe

NOMBRE = 'AgroAsesor'
COLOR_TEMA = '#0b6b4b'  # --green de tema-figma.css (barra de estado y barra del título en modo app)
COLOR_FONDO = '#f6f8f7'  # --bg de tema-figma.css (pantalla de inicio de Android)
DESCRIPCION = 'Gestión agrícola: predios, actividades, calendario, calculadoras y catálogo, en tu teléfono.'

# Atajos al mantener pulsado el ícono (Android): (nombre de la ruta, nombre largo, nombre corto).
ATAJOS = (
    ('predios', 'Mis predios', 'Predios'),
    ('actividad_list', 'Actividades', 'Actividades'),
    ('calendario', 'Calendario agrícola', 'Calendario'),
    ('calculadoras', 'Calculadoras', 'Calculadoras'),
)


# Rutas que el service worker deja pasar directo a la red, sin tocarlas: sesión, Google, recuperación de contraseña,
# administración y API. Una prueba comprueba que cubren todas las rutas de autenticación reales.
RUTAS_SIN_INTERCEPTAR = (
    '/login/',
    '/logout/',
    '/entrar-admin/',
    '/cuenta/google/',
    '/cuenta/recuperar/',
    '/cuenta/enviado/',
    '/cuenta/restablecer/',
    '/cuenta/restablecida/',
    '/admin/',
    '/api/',
)


def _icono(archivo, lado, proposito):
    return {
        'src': static(f'pwa/{archivo}'),
        'sizes': f'{lado}x{lado}',
        'type': 'image/png',
        'purpose': proposito,
    }


def datos_manifest():
    icono_atajo = [_icono('icon-192.png', 192, 'any')]
    return {
        'id': '/',
        'name': NOMBRE,
        'short_name': NOMBRE,
        'description': DESCRIPCION,
        'lang': 'es-CL',
        'dir': 'ltr',
        'start_url': '/?source=pwa',
        'scope': '/',
        'display': 'standalone',
        'display_override': ['standalone', 'minimal-ui'],
        'background_color': COLOR_FONDO,
        'theme_color': COLOR_TEMA,
        'categories': ['productivity', 'business'],
        'icons': [
            _icono('icon-192.png', 192, 'any'),
            _icono('icon-512.png', 512, 'any'),
            _icono('icon-maskable-192.png', 192, 'maskable'),
            _icono('icon-maskable-512.png', 512, 'maskable'),
        ],
        'shortcuts': [
            {'name': largo, 'short_name': corto, 'url': reverse(ruta), 'icons': icono_atajo}
            for ruta, largo, corto in ATAJOS
        ],
    }


@require_safe
def manifest(request):
    cuerpo = json.dumps(datos_manifest(), ensure_ascii=False, indent=2)
    respuesta = HttpResponse(cuerpo, content_type='application/manifest+json; charset=utf-8')
    respuesta['Cache-Control'] = 'public, max-age=3600'
    return respuesta


def _contexto_service_worker(version):
    return {
        'version': version,
        'offline_url': reverse('pwa_offline'),
        'sin_interceptar': RUTAS_SIN_INTERCEPTAR,
        'static_url': settings.STATIC_URL,
    }


def version_service_worker():
    """Cambia en cada despliegue (commit de Vercel) y cada vez que cambia el código del service worker o de la
    pantalla sin conexión: así el navegador instala la versión nueva, borra los archivos viejos y la app se actualiza sola."""
    commit = (os.getenv('VERCEL_GIT_COMMIT_SHA') or os.getenv('PWA_VERSION') or '').strip()[:12]
    contenido = render_to_string('pwa/service-worker.js', _contexto_service_worker('base'))
    contenido += render_to_string('pwa/sin_conexion.html')
    huella = hashlib.sha256(contenido.encode('utf-8')).hexdigest()[:10]
    return f'{commit}-{huella}' if commit else huella


@require_safe
def service_worker(request):
    cuerpo = render_to_string('pwa/service-worker.js', _contexto_service_worker(version_service_worker()))
    respuesta = HttpResponse(cuerpo, content_type='text/javascript; charset=utf-8')
    # Sin caché: el navegador debe poder ver una versión nueva en cada comprobación.
    respuesta['Cache-Control'] = 'no-cache, max-age=0'
    respuesta['Service-Worker-Allowed'] = '/'
    return respuesta


@require_safe
def sin_conexion(request):
    """Pantalla que muestra el service worker cuando no se puede llegar al servidor. Sin sesión, sin cookies, sin datos."""
    respuesta = HttpResponse(render_to_string('pwa/sin_conexion.html'))
    respuesta['Cache-Control'] = 'no-cache, max-age=0'
    return respuesta
