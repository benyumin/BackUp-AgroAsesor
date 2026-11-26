"""Recuperación de contraseña por correo.

Se conserva el flujo de Django (rutas ``password_reset*``, plantillas y tokens de un solo uso). Este módulo solo
corrige lo que fallaba en producción:

* El formulario estándar de Django traga cualquier error de envío (lo deja en el log) y la persona siempre ve «solicitud
  recibida», aunque el correo nunca salga. Aquí, antes de buscar la cuenta, se comprueba que el correo PUEDE salir
  (configuración y conexión con el servidor SMTP). Como esa comprobación no depende de la dirección escrita, avisar
  del fallo no revela si una dirección está registrada: la respuesta ante una cuenta existente y una inexistente es la
  misma (mensaje neutro). Un rechazo del servidor para un destinatario concreto solo queda en el log, nunca en pantalla.
* El enlace del correo usa el dominio público (``SITE_URL``) y ``https``, no el que llegue en la petición.
* Nunca se escriben en el log direcciones de correo, claves ni el enlace con el token.

Variables que necesita el servidor de correo (ver ``.env.example``): EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER,
EMAIL_HOST_PASSWORD, DEFAULT_FROM_EMAIL y, si hace falta, EMAIL_USE_SSL / EMAIL_USE_TLS.
"""

import logging
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordResetForm
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponseRedirect
from django.template import loader

from . import limites

logger = logging.getLogger(__name__)

MENSAJE_SIN_CORREO = (
    'No pudimos enviar el correo de recuperación en este momento. Inténtalo de nuevo más tarde; '
    'si el problema continúa, contacta al administrador de AgroAsesor.'
)
# Backends que no entregan correos a nadie (solo sirven para desarrollo).
BACKENDS_DE_DESARROLLO = ('console.EmailBackend', 'dummy.EmailBackend', 'filebased.EmailBackend')


class ErrorEnvioCorreo(Exception):
    """El servidor de correo no está configurado o no responde (no depende de la dirección escrita)."""


def motivo_correo_no_disponible():
    """Texto para el log si el correo NO puede salir con la configuración actual; ``None`` si sirve.

    En desarrollo (DEBUG) no se exige nada: el backend de consola escribe el correo en la terminal a propósito.
    """
    if settings.DEBUG:
        return None
    configuracion = getattr(settings, 'MAILERS', {}).get('default', {})
    backend = str(configuracion.get('BACKEND', ''))
    if backend.endswith(BACKENDS_DE_DESARROLLO):
        return 'el backend de correo es de desarrollo (consola o archivo): defina EMAIL_HOST y las credenciales SMTP'
    if backend.endswith('smtp.EmailBackend'):
        if not configuracion.get('OPTIONS', {}).get('host'):
            return 'falta EMAIL_HOST'
        remitente = str(getattr(settings, 'DEFAULT_FROM_EMAIL', ''))
        if '@' not in remitente or '@localhost' in remitente:
            return 'DEFAULT_FROM_EMAIL no tiene un remitente real (el proveedor rechaza @localhost)'
    return None


class RecuperarContrasenaForm(PasswordResetForm):
    """``PasswordResetForm`` con verificación previa del correo y envío por una sola conexión."""

    _conexion = None

    def save(self, domain_override=None, **kwargs):
        motivo = motivo_correo_no_disponible()
        if motivo:
            logger.error('Recuperación de contraseña imposible: %s.', motivo)
            raise ErrorEnvioCorreo(motivo)
        sitio = urlparse(str(getattr(settings, 'SITE_URL', '') or ''))
        if sitio.netloc and not domain_override:
            domain_override = sitio.netloc  # enlace con el dominio público, no con la cabecera Host de la petición
            kwargs['use_https'] = sitio.scheme == 'https'
        try:
            conexion = mail.mailers['default']
            conexion.open()  # abre la conexión (y autentica) ANTES de mirar si la cuenta existe
        except Exception as exc:
            logger.error('Recuperación de contraseña: el servidor de correo no respondió (%s).', type(exc).__name__)
            raise ErrorEnvioCorreo(type(exc).__name__) from exc
        self._conexion = conexion
        try:
            return super().save(domain_override=domain_override, **kwargs)
        finally:
            self._conexion = None
            conexion.close()

    def send_mail(
        self, subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name=None
    ):
        if self._conexion is None:  # uso directo, fuera de save(): comportamiento estándar de Django
            return super().send_mail(
                subject_template_name, email_template_name, context, from_email, to_email, html_email_template_name
            )
        asunto = ''.join(loader.render_to_string(subject_template_name, context).splitlines())
        mensaje = EmailMultiAlternatives(
            asunto, loader.render_to_string(email_template_name, context), from_email, [to_email]
        )
        if html_email_template_name is not None:
            mensaje.attach_alternative(loader.render_to_string(html_email_template_name, context), 'text/html')
        try:
            self._conexion.send_messages([mensaje])
        except Exception as exc:
            # Pantalla neutra a propósito (ver el módulo): solo el log dice que el servidor rechazó este envío.
            logger.error(
                'No se pudo enviar el correo de recuperación (cuenta %s): %s.', context['user'].pk, type(exc).__name__
            )


class RecuperarContrasenaView(auth_views.PasswordResetView):
    form_class = RecuperarContrasenaForm

    def form_valid(self, form):
        # Límite de solicitudes: por IP se avisa (no revela nada de la cuenta); por correo se responde igual que
        # siempre («revisa tu correo») pero no se envía otro mensaje, así no se puede inundar un buzón ni saber si
        # la dirección está registrada.
        ip = limites.ip_cliente(self.request)
        bloqueo = limites.espera('recuperar-ip', ip, settings.LIMITE_RECUPERAR_IP)
        if bloqueo:
            form.add_error(None, limites.mensaje_bloqueo(bloqueo))
            respuesta = self.form_invalid(form)
            respuesta.status_code = 429
            return respuesta
        limites.registrar('recuperar-ip', ip, settings.LIMITE_RECUPERAR_IP)
        correo = form.cleaned_data.get('email', '')
        if limites.espera('recuperar-correo', correo, settings.LIMITE_RECUPERAR_CORREO):
            return HttpResponseRedirect(self.get_success_url())
        limites.registrar('recuperar-correo', correo, settings.LIMITE_RECUPERAR_CORREO)
        try:
            return super().form_valid(form)
        except ErrorEnvioCorreo:
            form.add_error(None, MENSAJE_SIN_CORREO)
            return self.form_invalid(form)
