"""Límite de intentos para el inicio de sesión, el registro y la recuperación de contraseña.

Sin dependencias nuevas ni tablas nuevas: los contadores se guardan en la caché compartida firmada
(``core.cache_compartida``), que es común a todas las instancias de Vercel. Cada contador es una ventana fija:
el primer intento abre la ventana y, al llegar al máximo, se bloquea hasta que la ventana vence.

* Las claves son códigos HMAC opacos: en la base no quedan direcciones IP, correos ni nombres de usuario.
* Si la base falla, el límite se omite (se deja constancia en el log) en lugar de impedir el acceso a todos.
* La IP del visitante solo se toma de ``X-Forwarded-For`` cuando hay un proxy de confianza (Vercel o
  ``DJANGO_CONFIAR_PROXY_HTTPS``); si no, se usa ``REMOTE_ADDR``, que el navegador no puede falsificar.

Los límites (máximo, ventana) se configuran en ``settings.LIMITE_*``.
"""

import logging
import math
import time

from django.conf import settings
from django.db import DatabaseError

from . import cache_compartida as almacen

logger = logging.getLogger(__name__)


def activo():
    return bool(getattr(settings, 'LIMITE_INTENTOS_ACTIVO', True))


def ip_cliente(request):
    """Dirección IP del visitante (cadena vacía si no se conoce)."""
    if request is None:
        return ''
    if getattr(settings, 'SECURE_PROXY_SSL_HEADER', None):
        reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if reenviada:
            return reenviada.split(',')[0].strip()
        real = request.META.get('HTTP_X_REAL_IP', '').strip()
        if real:
            return real
    return request.META.get('REMOTE_ADDR', '') or ''


def _clave(ambito, valor):
    return almacen.clave_opaca(f'lim-{ambito}', str(valor).strip().lower())


def espera(ambito, valor, limite, ahora=None):
    """Segundos que faltan para volver a intentar, o 0 si se puede intentar ahora."""
    if not activo() or not valor:
        return 0
    maximo, _ventana = limite
    ahora = int(time.time() if ahora is None else ahora)
    try:
        registro = almacen.leer(_clave(ambito, valor))
    except DatabaseError:
        logger.warning('Límite de intentos: no se pudo leer el contador (%s); se omite.', ambito)
        return 0
    if registro is None or registro.vence <= ahora:
        return 0
    if int((registro.valor or {}).get('n', 0)) >= maximo:
        return registro.vence - ahora
    return 0


def registrar(ambito, valor, limite, ahora=None):
    """Suma un intento al contador (abre una ventana nueva si no hay una vigente)."""
    if not activo() or not valor:
        return
    _maximo, ventana = limite
    ahora = int(time.time() if ahora is None else ahora)
    clave = _clave(ambito, valor)
    try:
        registro = almacen.leer(clave)
        if registro is None or registro.vence <= ahora:
            almacen.guardar(clave, {'n': 1}, ahora, ahora + ventana)
        else:
            n = int((registro.valor or {}).get('n', 0)) + 1
            almacen.guardar(clave, {'n': n}, registro.obtenido, registro.vence)
    except DatabaseError:
        logger.warning('Límite de intentos: no se pudo guardar el contador (%s); se omite.', ambito)


def limpiar(ambito, valor):
    if not activo() or not valor:
        return
    try:
        almacen.liberar(_clave(ambito, valor))
    except DatabaseError:
        logger.warning('Límite de intentos: no se pudo reiniciar el contador (%s).', ambito)


def minutos(segundos):
    return max(1, math.ceil(segundos / 60))


# --- Inicio de sesión -------------------------------------------------------------------------------------------

def espera_login(request, identificador):
    """Segundos de bloqueo para este usuario/correo o para esta IP (el mayor de los dos)."""
    return max(
        espera('login-cuenta', identificador, settings.LIMITE_LOGIN_CUENTA),
        espera('login-ip', ip_cliente(request), settings.LIMITE_LOGIN_IP),
    )


def fallo_login(request, identificador):
    registrar('login-cuenta', identificador, settings.LIMITE_LOGIN_CUENTA)
    registrar('login-ip', ip_cliente(request), settings.LIMITE_LOGIN_IP)


def exito_login(identificador):
    """Un acceso correcto reinicia el contador de esa cuenta (no el de la IP)."""
    limpiar('login-cuenta', identificador)


def mensaje_bloqueo(segundos):
    return (
        f'Demasiados intentos. Por seguridad, espera {minutos(segundos)} minuto(s) antes de volver a intentarlo.'
    )
