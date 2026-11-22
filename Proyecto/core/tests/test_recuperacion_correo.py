"""Recuperación de contraseña por correo en producción (Vercel).

Antes: el formulario estándar de Django tragaba los errores de envío (la persona siempre veía «solicitud recibida»), el
backend por defecto era el de consola (el correo no salía a ningún lado) y el enlace salía con http y el dominio de la
petición. Ahora: error claro cuando el correo no puede salir, enlace con el dominio público en https y la misma
respuesta exista o no la cuenta.

Nunca se envía un correo de verdad ni se conecta a un servidor SMTP: se usa ``locmem`` o backends de prueba.
"""

import contextlib
import io
import re
import smtplib
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings

from core import recuperacion

User = get_user_model()
PASSWORD = 'Campo-Seguro-2026!'
LOCMEM = {'default': {'BACKEND': 'django.core.mail.backends.locmem.EmailBackend'}}
CONSOLA = {'default': {'BACKEND': 'django.core.mail.backends.console.EmailBackend', 'OPTIONS': {}}}
SMTP_REAL = {
    'default': {
        'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
        'OPTIONS': {'host': 'smtp.proveedor.example', 'port': 587, 'use_tls': True, 'username': 'u', 'password': 'p'},
    }
}
REMITENTE = 'AgroAsesor <no-responder@agroasesor.example>'
DESCONOCIDA = 'nadie@example.com'


class SinConexion(BaseEmailBackend):
    """Servidor de correo caído: no se puede ni abrir la conexión."""

    def open(self):
        raise ConnectionRefusedError('el servidor no responde')

    def send_messages(self, email_messages):
        raise AssertionError('no debería llegar a enviar')


class RechazaDestinatarios(BaseEmailBackend):
    """Servidor que acepta la conexión pero rechaza el envío (por ejemplo, remitente no verificado)."""

    def send_messages(self, email_messages):
        raise smtplib.SMTPRecipientsRefused({'x': (550, b'sender not verified')})


def mailers(clase):
    return {'default': {'BACKEND': f'core.tests.test_recuperacion_correo.{clase}'}}


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('ana@example.com', email='ana@example.com', password=PASSWORD)

    def enviar(self, correo=None, **extra):
        return self.client.post('/cuenta/recuperar/', {'email': correo or self.user.email}, **extra)

    def enlace(self, indice=0):
        return re.search(r'https?://[^/\s]+(/cuenta/restablecer/\S+)', mail.outbox[indice].body).group(1)


@override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE)
class EnlaceDelCorreoTests(Base):
    @override_settings(SITE_URL='https://agroasesor.example')
    def test_el_enlace_usa_el_dominio_publico_en_https(self):
        self.assertRedirects(self.enviar(), '/cuenta/enviado/')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('https://agroasesor.example/cuenta/restablecer/', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertEqual(mail.outbox[0].from_email, REMITENTE)

    @override_settings(SITE_URL='https://agroasesor.example')
    def test_email_incluye_diseno_html_y_conserva_texto_plano(self):
        self.assertRedirects(self.enviar(), '/cuenta/enviado/')
        self.assertEqual(len(mail.outbox), 1)
        mensaje = mail.outbox[0]
        self.assertEqual(len(mensaje.alternatives), 1)
        html = mensaje.alternatives[0].content
        self.assertEqual(mensaje.alternatives[0].mimetype, 'text/html')
        self.assertIn('Restablecer contraseña', html)
        self.assertIn('https://agroasesor.example/cuenta/restablecer/', html)
        self.assertIn('https://agroasesor.example/cuenta/restablecer/', mensaje.body)
        self.assertNotIn('http://', html)

    @override_settings(SITE_URL='https://agroasesor.example')
    def test_la_cabecera_host_de_la_peticion_no_cambia_el_dominio_del_enlace(self):
        with override_settings(ALLOWED_HOSTS=['*']):
            self.enviar(HTTP_HOST='atacante.example')
        self.assertNotIn('atacante.example', mail.outbox[0].body)
        self.assertIn('https://agroasesor.example/', mail.outbox[0].body)

    @override_settings(
        SITE_URL='', ALLOWED_HOSTS=['agroasesor.example'], SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')
    )
    def test_detras_del_proxy_de_vercel_el_enlace_sale_con_https(self):
        self.enviar(HTTP_HOST='agroasesor.example', HTTP_X_FORWARDED_PROTO='https')
        self.assertIn('https://agroasesor.example/cuenta/restablecer/', mail.outbox[0].body)

    @override_settings(SITE_URL='', ALLOWED_HOSTS=['agroasesor.example'], SECURE_PROXY_SSL_HEADER=None)
    def test_sin_configurar_el_proxy_el_enlace_salia_con_http(self):
        # Documenta la causa original: sin SECURE_PROXY_SSL_HEADER Django cree que la petición fue por http.
        self.enviar(HTTP_HOST='agroasesor.example', HTTP_X_FORWARDED_PROTO='https')
        self.assertIn('http://agroasesor.example/cuenta/restablecer/', mail.outbox[0].body)

    @override_settings(SITE_URL='https://agroasesor.example')
    def test_el_flujo_completo_con_el_enlace_del_correo_cambia_la_contrasena(self):
        self.enviar()
        url = self.enlace()
        paso = self.client.get(url)  # Django guarda el token en la sesión y redirige al formulario
        self.assertEqual(paso.status_code, 302)
        self.assertContains(self.client.get(paso.url), 'name="new_password1"')
        nueva = 'Otra-Clave-Segura-2027!'
        hecho = self.client.post(paso.url, {'new_password1': nueva, 'new_password2': nueva}, follow=True)
        self.assertEqual(hecho.redirect_chain[-1][0], '/cuenta/restablecida/')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(nueva))
        self.assertFalse(self.user.check_password(PASSWORD))

    def test_el_enlace_es_de_un_solo_uso(self):
        self.enviar()
        url = self.enlace()
        paso = self.client.get(url)
        nueva = 'Otra-Clave-Segura-2027!'
        self.client.post(paso.url, {'new_password1': nueva, 'new_password2': nueva})
        reuso = self.client.get(url)  # el token depende de la contraseña anterior: ya no sirve
        self.assertContains(reuso, 'Enlace No Disponible')

    def test_un_enlace_manipulado_no_sirve(self):
        self.enviar()
        url = self.enlace()
        malo = re.sub(r'/[^/]+/$', '/token-falso/', url)
        self.assertContains(self.client.get(malo), 'Enlace No Disponible')

    def test_usuario_inactivo_o_sin_contrasena_utilizable_no_recibe_correo(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertRedirects(self.enviar(), '/cuenta/enviado/')
        self.assertEqual(len(mail.outbox), 0)


@override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE)
class NoRevelaSiLaCuentaExisteTests(Base):
    def test_cuenta_existente_e_inexistente_reciben_la_misma_respuesta(self):
        existente = self.enviar(follow=True)
        inexistente = self.enviar(DESCONOCIDA, follow=True)
        self.assertEqual(existente.redirect_chain, inexistente.redirect_chain)
        self.assertEqual(existente.status_code, inexistente.status_code)
        self.assertEqual(len(mail.outbox), 1)  # solo salió el de la cuenta real
        for respuesta in (existente, inexistente):
            self.assertContains(respuesta, 'Si existe una cuenta activa con ese correo')
        self.assertEqual(
            re.sub(r'csrfmiddlewaretoken\S+', '', existente.content.decode()),
            re.sub(r'csrfmiddlewaretoken\S+', '', inexistente.content.decode()),
        )

    @override_settings(MAILERS=CONSOLA)
    def test_sin_servidor_de_correo_el_error_es_igual_exista_o_no_la_cuenta(self):
        with self.assertLogs('core.recuperacion', 'ERROR'):
            existente = self.enviar()
        with self.assertLogs('core.recuperacion', 'ERROR'):
            inexistente = self.enviar(DESCONOCIDA)
        for respuesta in (existente, inexistente):
            self.assertEqual(respuesta.status_code, 200)
            self.assertContains(respuesta, recuperacion.MENSAJE_SIN_CORREO)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(MAILERS=mailers('SinConexion'))
    def test_servidor_caido_da_el_mismo_error_a_cualquier_direccion(self):
        for correo in (self.user.email, DESCONOCIDA):
            with self.subTest(correo), self.assertLogs('core.recuperacion', 'ERROR') as registro:
                respuesta = self.enviar(correo)
            self.assertEqual(respuesta.status_code, 200)
            self.assertContains(respuesta, recuperacion.MENSAJE_SIN_CORREO)
            self.assertNotIn(correo, ' '.join(registro.output))  # el log nunca lleva la dirección


@override_settings(DEBUG=False)
class ErroresDeEnvioTests(Base):
    @override_settings(MAILERS=CONSOLA)
    def test_en_produccion_el_backend_de_consola_se_detecta_y_se_avisa(self):
        with self.assertLogs('core.recuperacion', 'ERROR') as registro:
            respuesta = self.enviar()
        self.assertContains(respuesta, recuperacion.MENSAJE_SIN_CORREO)
        self.assertIn('EMAIL_HOST', ' '.join(registro.output))  # el log dice qué variable falta

    @override_settings(MAILERS=CONSOLA, DEBUG=True)
    def test_en_desarrollo_la_consola_sigue_funcionando(self):
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):  # el backend de consola escribe el correo en la terminal
            self.assertRedirects(self.enviar(), '/cuenta/enviado/')
        self.assertIn('/cuenta/restablecer/', salida.getvalue())

    @override_settings(MAILERS={'default': {'BACKEND': SMTP_REAL['default']['BACKEND'], 'OPTIONS': {'host': ''}}})
    def test_smtp_sin_host_se_detecta(self):
        with self.assertLogs('core.recuperacion', 'ERROR') as registro:
            respuesta = self.enviar()
        self.assertContains(respuesta, recuperacion.MENSAJE_SIN_CORREO)
        self.assertIn('EMAIL_HOST', ' '.join(registro.output))

    @override_settings(MAILERS=SMTP_REAL, DEFAULT_FROM_EMAIL='AgroAsesor <noreply@localhost>')
    def test_smtp_con_remitente_de_ejemplo_se_detecta(self):
        with self.assertLogs('core.recuperacion', 'ERROR') as registro:
            respuesta = self.enviar()
        self.assertContains(respuesta, recuperacion.MENSAJE_SIN_CORREO)
        self.assertIn('DEFAULT_FROM_EMAIL', ' '.join(registro.output))

    @override_settings(MAILERS=SMTP_REAL, DEFAULT_FROM_EMAIL=REMITENTE)
    def test_credenciales_rechazadas_por_el_proveedor_no_filtran_claves_al_log(self):
        error = smtplib.SMTPAuthenticationError(535, b'Authentication failed for user u password p')
        with mock.patch('django.core.mail.backends.smtp.EmailBackend.open', side_effect=error):
            with self.assertLogs('core.recuperacion', 'ERROR') as registro:
                respuesta = self.enviar()
        self.assertContains(respuesta, recuperacion.MENSAJE_SIN_CORREO)
        salida = ' '.join(registro.output)
        self.assertIn('SMTPAuthenticationError', salida)
        for secreto in ('password p', 'Authentication failed', self.user.email):
            self.assertNotIn(secreto, salida)

    @override_settings(MAILERS=SMTP_REAL, DEFAULT_FROM_EMAIL=REMITENTE)
    def test_con_smtp_correcto_se_envia_por_una_sola_conexion(self):
        enviados = []

        def enviar(self_, mensajes):
            enviados.extend(mensajes)
            return len(mensajes)

        with mock.patch('django.core.mail.backends.smtp.EmailBackend.open', return_value=True) as abrir, \
                mock.patch('django.core.mail.backends.smtp.EmailBackend.close') as cerrar, \
                mock.patch('django.core.mail.backends.smtp.EmailBackend.send_messages', enviar):
            self.assertRedirects(self.enviar(), '/cuenta/enviado/')
        self.assertEqual(len(enviados), 1)
        self.assertEqual(enviados[0].to, [self.user.email])
        self.assertEqual(abrir.call_count, 1)
        self.assertEqual(cerrar.call_count, 1)

    @override_settings(MAILERS=mailers('RechazaDestinatarios'))
    def test_si_el_proveedor_rechaza_el_envio_queda_en_el_log_y_la_pantalla_sigue_neutra(self):
        # Mostrar el fallo aquí revelaría que la cuenta existe; por eso solo se registra en el servidor.
        with self.assertLogs('core.recuperacion', 'ERROR') as registro:
            respuesta = self.enviar(follow=True)
        self.assertEqual(respuesta.redirect_chain[-1][0], '/cuenta/enviado/')
        salida = ' '.join(registro.output)
        self.assertIn('SMTPRecipientsRefused', salida)
        self.assertNotIn(self.user.email, salida)
        self.assertNotIn('sender not verified', salida)
        self.assertNotIn('restablecer', salida)  # tampoco el enlace con el token

    @override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE)
    def test_dato_invalido_sigue_mostrando_el_error_del_campo(self):
        respuesta = self.client.post('/cuenta/recuperar/', {'email': 'no-es-correo'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'class="errorlist"')
        self.assertEqual(len(mail.outbox), 0)


class VerificacionDeConfiguracionTests(TestCase):
    @override_settings(DEBUG=False, MAILERS=LOCMEM)
    def test_locmem_y_smtp_completo_son_validos(self):
        self.assertIsNone(recuperacion.motivo_correo_no_disponible())
        with override_settings(MAILERS=SMTP_REAL, DEFAULT_FROM_EMAIL=REMITENTE):
            self.assertIsNone(recuperacion.motivo_correo_no_disponible())

    @override_settings(DEBUG=False)
    def test_los_backends_de_desarrollo_no_sirven_en_produccion(self):
        for backend in ('console', 'dummy', 'filebased'):
            with self.subTest(backend), override_settings(
                MAILERS={'default': {'BACKEND': f'django.core.mail.backends.{backend}.EmailBackend'}}
            ):
                self.assertIn('EMAIL_HOST', recuperacion.motivo_correo_no_disponible())
