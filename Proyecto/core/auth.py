"""Acceso y sesión: entrar con correo o usuario, y que la página no se «caiga» si la sesión vence.

* Formularios normales: si falta o venció la verificación CSRF (típico al borrar las cookies con la página abierta),
  en vez de la pantalla de error de Django se vuelve a la página anterior (o al login) con un aviso claro.
* Llamadas de la propia página (fetch/AJAX): responden JSON ``{"codigo": "sesion_vencida"}`` en vez de HTML,
  y ``static/js/layout.js`` lleva a la persona al login sin pantallas rotas.
* Roles: ``es_administrador`` es la única regla del servidor para saber quién es administrador; ``destino_inicial``
  la usa para elegir la pantalla al entrar (panel o inicio) y ``administrador_requerido`` para proteger las vistas
  del panel. El rol nunca se lee de la petición: solo de la cuenta autenticada.
"""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme

from . import limites


class EmailBackend(ModelBackend):
    """Acceso con correo o con nombre de usuario (sin distinguir mayúsculas).

    Si por datos antiguos varias cuentas comparten el mismo correo, se prueba la contraseña con cada una
    (primero la que tenga exactamente ese nombre de usuario) y entra la que coincida, en vez de rechazar
    a todas. Así una cuenta registrada siempre puede volver a entrar.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        identificador = username.strip()
        # Límite de intentos (también protege /admin/, que usa este mismo backend). PermissionDenied detiene la
        # autenticación sin probar la contraseña; la vista muestra el aviso con limites.espera_login.
        if limites.espera_login(request, identificador):
            raise PermissionDenied
        user = self._autenticar(identificador, password)
        if user is None:
            limites.fallo_login(request, identificador)
        else:
            limites.exito_login(identificador)
        return user

    def _autenticar(self, identificador, password):
        model = get_user_model()
        candidatos = list(
            model.objects.filter(Q(username__iexact=identificador) | Q(email__iexact=identificador)).order_by('pk')[:10]
        )
        # Primero la cuenta cuyo nombre de usuario es justo lo escrito; luego las que coinciden por correo.
        candidatos.sort(key=lambda u: 0 if u.username.lower() == identificador.lower() else 1)
        if not candidatos:
            model().set_password(password)  # tiempo de respuesta parecido al de una cuenta real
            return None
        for user in candidatos:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None


def es_administrador(user):
    """Única regla del servidor para saber si una cuenta es administradora de AgroAsesor.

    Se exige sesión iniciada, cuenta activa e ``is_staff``: es el mismo criterio que ya usaban el panel, la carga de
    cultivos base y la pasarela de la API, así que no se amplía ningún permiso. ``is_superuser`` no decide por sí solo
    (da permisos sobre todos los modelos de Django Admin, que es otra cosa). Las cuentas que se registran, con
    formulario o con Google, siempre nacen con ambos en ``False``; los administradores se asignan solo desde el
    servidor (``manage.py administradores`` o ``createsuperuser``).
    """
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


def destino_inicial(user):
    """Ruta a la que va una persona al entrar: el panel si es administradora, el inicio si es agricultora.

    Devuelve siempre el nombre de una ruta fija, nunca un valor que venga de la petición (``next``, campos del
    formulario), así que no hay redirecciones abiertas ni forma de elegir el rol desde el navegador.
    """
    return 'admin_panel' if es_administrador(user) else 'dashboard'


def administrador_requerido(vista):
    """Protege una vista del panel: sin sesión va al login y con sesión pero sin ser administrador responde 403."""

    @login_required
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not es_administrador(request.user):
            raise PermissionDenied
        return vista(request, *args, **kwargs)

    return envoltura


MENSAJE_VENCIDA = 'Tu sesión venció o se borraron las cookies. Vuelve a iniciar sesión para continuar.'
MENSAJE_REINTENTAR = 'La página estuvo abierta demasiado tiempo o se borraron las cookies. Inténtalo de nuevo.'


def es_ajax(request):
    tipo = request.META.get('CONTENT_TYPE', '')
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
        or tipo.startswith('application/json')
    )


def _json_vencida(status):
    return JsonResponse(
        {
            'error': MENSAJE_VENCIDA,
            'codigo': 'sesion_vencida',
            'login_url': resolve_url(settings.LOGIN_URL),
        },
        status=status,
    )


def _volver(request):
    """Página anterior si es de este mismo sitio; si no, None."""
    anterior = request.META.get('HTTP_REFERER', '')
    if anterior and url_has_allowed_host_and_scheme(anterior, {request.get_host()}, request.is_secure()):
        return anterior
    return None


def fallo_csrf(request, reason=''):
    """Sustituye la pantalla 403 de Django (CSRF_FAILURE_VIEW)."""
    if es_ajax(request):
        return _json_vencida(403)
    login = resolve_url(settings.LOGIN_URL)
    if request.user.is_authenticated:
        messages.warning(request, MENSAJE_REINTENTAR)
        return redirect(_volver(request) or destino_inicial(request.user))
    messages.warning(request, MENSAJE_VENCIDA if request.path != login else MENSAJE_REINTENTAR)
    return redirect(login)


class SesionVencidaMiddleware:
    """Si una llamada AJAX sin sesión sería redirigida al login, responde 401 JSON (no el HTML del login)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        if respuesta.status_code in (301, 302) and es_ajax(request) and not request.user.is_authenticated:
            login = resolve_url(settings.LOGIN_URL)
            if respuesta.headers.get('Location', '').split('?')[0] == login:
                return _json_vencida(401)
        return respuesta
