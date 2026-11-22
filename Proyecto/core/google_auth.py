"""Inicio de sesión con Google (OAuth 2.0 + OpenID Connect, flujo «authorization code» con PKCE).

Django sigue siendo quien autentica y guarda la sesión; Google solo confirma el correo de la persona.
No hay dependencias nuevas ni tablas nuevas: se usa la biblioteca estándar de Python.

Flujo
1. ``google_iniciar`` (POST con token CSRF): genera ``state``, ``nonce`` y el verificador PKCE, los guarda en la
   sesión de Django y redirige a la pantalla de consentimiento de Google.
2. ``google_callback``: comprueba que ``state`` coincide (protege contra CSRF en el retorno), canjea el ``code``
   directamente con Google por HTTPS (con ``client_secret`` y el verificador PKCE) y valida el ``id_token``:
   emisor, audiencia (= nuestro client_id), vencimiento, ``nonce`` y ``email_verified``.
   Como el ``id_token`` llega directo desde el endpoint de tokens de Google por TLS, OpenID Connect Core
   (§3.1.3.7, punto 6) permite validar el emisor por TLS en vez de verificar la firma.
3. Si ya existe una cuenta con ese correo, entra a esa cuenta (sin duplicar usuarios ni tocar roles) y va a la
   pantalla de su rol: el panel si es administradora, el inicio si es agricultora (``core.auth.destino_inicial``).
   Si no existe, se pide completar el registro de agricultor (``google_completar``) con los mismos datos
   obligatorios del registro normal y la aceptación de la política.

Configuración (variables de entorno, nunca en el código ni en GitHub):
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y, opcional, GOOGLE_REDIRECT_URI.
"""

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .auth import destino_inicial
from .forms import RegistroGoogleForm
from .models import AceptacionPoliticas

URL_AUTORIZACION = 'https://accounts.google.com/o/oauth2/v2/auth'
URL_TOKEN = 'https://oauth2.googleapis.com/token'
EMISORES = {'accounts.google.com', 'https://accounts.google.com'}
CLAVE_FLUJO = 'google_oauth'
CLAVE_REGISTRO = 'google_registro'
VIGENCIA_FLUJO = 10 * 60  # segundos para volver desde Google
VIGENCIA_REGISTRO = 30 * 60  # segundos para completar el registro
BACKEND = 'core.auth.EmailBackend'

MENSAJE_FALLO = 'No se pudo completar el inicio de sesión con Google. Inténtalo de nuevo.'


class ErrorGoogle(Exception):
    """La respuesta de Google no es válida o no se pudo confirmar."""


def google_configurado():
    return bool(getattr(settings, 'GOOGLE_CLIENT_ID', '') and getattr(settings, 'GOOGLE_CLIENT_SECRET', ''))


def google_login_url():
    """Ruta del botón «Continuar con Google».

    Con credenciales, el botón inicia el flujo real. Sin credenciales solo se muestra en desarrollo (DEBUG), junto a
    un aviso de configuración; en producción se oculta para no ofrecer un acceso que no funciona.
    """
    return reverse('google_iniciar') if google_configurado() or settings.DEBUG else ''


def _redirect_uri(request):
    return getattr(settings, 'GOOGLE_REDIRECT_URI', '') or request.build_absolute_uri(reverse('google_callback'))


def _b64url(datos):
    return base64.urlsafe_b64encode(datos).rstrip(b'=').decode('ascii')


def _decodificar_jwt(token):
    """Payload de un JWT (sin verificar la firma: ver la nota del módulo)."""
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (AttributeError, IndexError, ValueError) as exc:
        raise ErrorGoogle('id_token ilegible') from exc


def canjear_codigo(datos):
    """POST al endpoint de tokens de Google. Separado para poder simularlo en las pruebas."""
    cuerpo = urllib.parse.urlencode(datos).encode('ascii')
    solicitud = urllib.request.Request(
        URL_TOKEN,
        data=cuerpo,
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(solicitud, timeout=10) as respuesta:
            return json.loads(respuesta.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ErrorGoogle('no se pudo canjear el código') from exc


def validar_id_token(token, nonce):
    """Devuelve (correo, nombre) si el id_token es de Google, para esta app, vigente y con correo verificado."""
    datos = _decodificar_jwt(token)
    ahora = time.time()
    if datos.get('iss') not in EMISORES:
        raise ErrorGoogle('emisor inválido')
    audiencia = datos.get('aud')
    audiencias = audiencia if isinstance(audiencia, list) else [audiencia]
    if settings.GOOGLE_CLIENT_ID not in audiencias:
        raise ErrorGoogle('audiencia inválida')
    if not isinstance(datos.get('exp'), (int, float)) or datos['exp'] < ahora - 60:
        raise ErrorGoogle('token vencido')
    if not nonce or not secrets.compare_digest(str(datos.get('nonce', '')), nonce):
        raise ErrorGoogle('nonce inválido')
    correo = str(datos.get('email', '')).strip().lower()
    if not correo or datos.get('email_verified') not in (True, 'true'):
        raise ErrorGoogle('correo no verificado')
    nombre = str(datos.get('name') or ' '.join(filter(None, [datos.get('given_name'), datos.get('family_name')])))
    return correo, nombre.strip()


def _cuenta_por_correo(correo):
    """Cuenta existente con ese correo (o con ese correo como usuario); primero la que lo usa como usuario."""
    model = get_user_model()
    candidatos = list(
        model.objects.filter(email__iexact=correo).order_by('pk')[:10]
    ) + list(model.objects.filter(username__iexact=correo).order_by('pk')[:1])
    candidatos.sort(key=lambda u: (0 if u.username.lower() == correo else 1, u.pk))
    return candidatos[0] if candidatos else None


@require_POST
def google_iniciar(request):
    if not google_configurado():
        messages.warning(
            request,
            'El inicio de sesión con Google todavía no está configurado: faltan GOOGLE_CLIENT_ID y '
            'GOOGLE_CLIENT_SECRET en el archivo .env.',
        )
        return redirect('login')
    verificador = secrets.token_urlsafe(64)
    flujo = {
        'state': secrets.token_urlsafe(32),
        'nonce': secrets.token_urlsafe(32),
        'verificador': verificador,
        'redirect_uri': _redirect_uri(request),
        'creado': time.time(),
    }
    request.session[CLAVE_FLUJO] = flujo
    parametros = {
        'client_id': settings.GOOGLE_CLIENT_ID,
        'redirect_uri': flujo['redirect_uri'],
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': flujo['state'],
        'nonce': flujo['nonce'],
        'code_challenge': _b64url(hashlib.sha256(verificador.encode('ascii')).digest()),
        'code_challenge_method': 'S256',
        'prompt': 'select_account',
        'access_type': 'online',
    }
    return redirect(f'{URL_AUTORIZACION}?{urllib.parse.urlencode(parametros)}')


@never_cache
def google_callback(request):
    flujo = request.session.pop(CLAVE_FLUJO, None)
    if request.GET.get('error'):
        messages.warning(request, 'Cancelaste el inicio de sesión con Google o Google no lo autorizó.')
        return redirect('login')
    state = request.GET.get('state', '')
    codigo = request.GET.get('code', '')
    if (
        not google_configurado()
        or not flujo
        or not state
        or not codigo
        or not secrets.compare_digest(state, flujo.get('state', ''))
        or time.time() - flujo.get('creado', 0) > VIGENCIA_FLUJO
    ):
        messages.warning(request, MENSAJE_FALLO)
        return redirect('login')
    try:
        tokens = canjear_codigo(
            {
                'code': codigo,
                'client_id': settings.GOOGLE_CLIENT_ID,
                'client_secret': settings.GOOGLE_CLIENT_SECRET,
                'redirect_uri': flujo['redirect_uri'],
                'grant_type': 'authorization_code',
                'code_verifier': flujo['verificador'],
            }
        )
        if not isinstance(tokens, dict) or 'id_token' not in tokens:
            raise ErrorGoogle('respuesta sin id_token')
        correo, nombre = validar_id_token(tokens['id_token'], flujo.get('nonce', ''))
    except ErrorGoogle:
        messages.warning(request, MENSAJE_FALLO)
        return redirect('login')

    user = _cuenta_por_correo(correo)
    if user is not None:
        if not user.is_active:
            messages.warning(request, 'Esta cuenta está desactivada. Contacta al administrador de AgroAsesor.')
            return redirect('login')
        login(request, user, backend=BACKEND)  # misma sesión de Django; roles y permisos intactos
        return redirect(destino_inicial(user))
    request.session[CLAVE_REGISTRO] = {'correo': correo, 'nombre': nombre, 'creado': time.time()}
    return redirect('google_completar')


@never_cache
def google_completar(request):
    """Datos obligatorios del agricultor para quien entra por primera vez con Google."""
    pendiente = request.session.get(CLAVE_REGISTRO)
    if request.user.is_authenticated:
        return redirect(destino_inicial(request.user))
    if not pendiente or time.time() - pendiente.get('creado', 0) > VIGENCIA_REGISTRO:
        request.session.pop(CLAVE_REGISTRO, None)
        messages.warning(request, 'Vuelve a continuar con Google para crear tu cuenta.')
        return redirect('login')
    correo = pendiente['correo']
    form = RegistroGoogleForm(
        request.POST if request.method == 'POST' else None,
        correo=correo,
        initial={'nombre_completo': pendiente.get('nombre', '')},
    )
    error = ''
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
                AceptacionPoliticas.objects.create(usuario=user, version=AceptacionPoliticas.VERSION_ACTUAL)
        except IntegrityError:
            error = 'Este correo electrónico ya está registrado. Inicia sesión con tu cuenta.'
        else:
            request.session.pop(CLAVE_REGISTRO, None)
            login(request, user, backend=BACKEND)
            messages.success(request, 'Tu cuenta se creó correctamente con Google. Ya iniciaste sesión.')
            return redirect(destino_inicial(user))
    return render(request, 'registration/google_completar.html', {'form': form, 'correo': correo, 'error': error})
