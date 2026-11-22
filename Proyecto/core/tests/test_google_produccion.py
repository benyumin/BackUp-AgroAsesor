"""Inicio de sesión con Google en producción (Vercel): botón condicionado a las variables y URL de retorno correcta.

Causas que se cubren: el botón se oculta (a propósito) si faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en el entorno de
Vercel, y sin ``SECURE_PROXY_SSL_HEADER`` la URL de retorno salía con http:// y Google la rechazaba
(``redirect_uri_mismatch``). Ninguna prueba sale a internet: el canje del código con Google se simula.
"""

import io
import json
import urllib.error
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from core import google_auth
from core.tests.test_google_auth import CLIENT_ID, id_token

CREDENCIALES = {'GOOGLE_CLIENT_ID': CLIENT_ID, 'GOOGLE_CLIENT_SECRET': 'secreto-de-prueba', 'GOOGLE_REDIRECT_URI': ''}
SIN_PROXY = {'SECURE_PROXY_SSL_HEADER': None}
CON_PROXY = {'SECURE_PROXY_SSL_HEADER': ('HTTP_X_FORWARDED_PROTO', 'https')}
RETORNO = '/cuenta/google/retorno/'


class BotonCondicionadoTests(TestCase):
    def setUp(self):
        google_auth._aviso_emitido = False

    @override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', DEBUG=False)
    def test_sin_variables_en_produccion_no_hay_boton_y_el_log_dice_cuales_faltan(self):
        with self.assertLogs('core.google_auth', 'WARNING') as registro:
            html = self.client.get('/login/').content.decode()
        self.assertNotIn('Continuar con Google', html)
        self.assertNotIn('/cuenta/google/', html)
        self.assertIn('GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET', ' '.join(registro.output))

    @override_settings(GOOGLE_CLIENT_ID=CLIENT_ID, GOOGLE_CLIENT_SECRET='   ', DEBUG=False)
    def test_una_sola_variable_o_con_solo_espacios_tampoco_activa_el_boton(self):
        self.assertEqual(google_auth.google_variables_faltantes(), ['GOOGLE_CLIENT_SECRET'])
        with self.assertLogs('core.google_auth', 'WARNING') as registro:
            html = self.client.get('/login/').content.decode()
        self.assertNotIn('Continuar con Google', html)
        salida = ' '.join(registro.output)
        self.assertIn('GOOGLE_CLIENT_SECRET', salida)
        self.assertNotIn(CLIENT_ID, salida)  # el log nombra variables, nunca valores

    @override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', DEBUG=False)
    def test_el_aviso_se_escribe_una_sola_vez_por_instancia(self):
        with self.assertLogs('core.google_auth', 'WARNING') as registro:
            for _ in range(3):
                self.client.get('/login/')
                self.client.get('/login/?mode=register')
        self.assertEqual(len(registro.output), 1)

    @override_settings(**CREDENCIALES, DEBUG=False)
    def test_con_ambas_variables_el_boton_aparece_en_login_y_registro(self):
        for url in ('/login/', '/login/?mode=register'):
            html = self.client.get(url).content.decode()
            self.assertIn('Continuar con Google', html)
            self.assertIn('action="/cuenta/google/"', html)
            self.assertIn('csrfmiddlewaretoken', html)

    @override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', DEBUG=False)
    def test_sin_variables_el_flujo_tampoco_funciona_por_url_directa(self):
        r = self.client.post('/cuenta/google/')
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        self.assertNotIn('accounts.google.com', r['Location'])
        mensaje = ' '.join(str(m) for m in r.wsgi_request._messages)
        self.assertIn('GOOGLE_CLIENT_ID', mensaje)
        self.assertNotIn('.env', mensaje.replace('el archivo .env', ''))  # ya no habla solo del archivo .env


class DireccionDeRetornoTests(SimpleTestCase):
    def peticion(self, **extra):
        return RequestFactory().get('/login/', HTTP_HOST='agroasesor.example', **extra)

    @override_settings(GOOGLE_REDIRECT_URI='', SITE_URL='', ALLOWED_HOSTS=['agroasesor.example'], **CON_PROXY)
    def test_detras_del_proxy_de_vercel_usa_https_y_el_dominio_visitado(self):
        url = google_auth.direccion_de_retorno(self.peticion(HTTP_X_FORWARDED_PROTO='https'))
        self.assertEqual(url, 'https://agroasesor.example' + RETORNO)

    @override_settings(GOOGLE_REDIRECT_URI='', SITE_URL='', ALLOWED_HOSTS=['agroasesor.example'], **SIN_PROXY)
    def test_sin_el_ajuste_del_proxy_salia_con_http_causa_del_redirect_uri_mismatch(self):
        url = google_auth.direccion_de_retorno(self.peticion(HTTP_X_FORWARDED_PROTO='https'))
        self.assertEqual(url, 'http://agroasesor.example' + RETORNO)

    @override_settings(GOOGLE_REDIRECT_URI='', SITE_URL='https://agroasesor.example/', ALLOWED_HOSTS=['*'], **SIN_PROXY)
    def test_site_url_fija_la_direccion_sin_depender_de_las_cabeceras(self):
        peticion = RequestFactory().get('/login/', HTTP_HOST='otro-dominio.vercel.app')  # p. ej. la URL de la rama
        self.assertEqual(google_auth.direccion_de_retorno(peticion), 'https://agroasesor.example' + RETORNO)

    @override_settings(GOOGLE_REDIRECT_URI='https://dominio.example/cuenta/google/retorno/', SITE_URL='https://x.example')
    def test_la_variable_explicita_manda_sobre_todo(self):
        self.assertEqual(
            google_auth.direccion_de_retorno(self.peticion()), 'https://dominio.example/cuenta/google/retorno/'
        )


@override_settings(**CREDENCIALES, SITE_URL='https://agroasesor.example', DEBUG=False)
class FlujoConDominioPublicoTests(TestCase):
    def test_la_redireccion_a_google_lleva_la_url_de_retorno_https_del_sitio(self):
        r = self.client.post('/cuenta/google/')
        self.assertEqual(r.status_code, 302)
        consulta = parse_qs(urlparse(r['Location']).query)
        self.assertEqual(consulta['redirect_uri'], ['https://agroasesor.example' + RETORNO])
        self.assertEqual(consulta['client_id'], [CLIENT_ID])
        for obligatorio in ('state', 'nonce', 'code_challenge'):
            self.assertTrue(consulta[obligatorio][0])
        self.assertEqual(consulta['code_challenge_method'], ['S256'])
        self.assertNotIn('secreto-de-prueba', r['Location'])  # el secreto nunca viaja por el navegador

    def test_el_canje_usa_la_misma_url_que_la_autorizacion_y_la_sesion_guarda_el_flujo(self):
        r = self.client.post('/cuenta/google/')
        consulta = parse_qs(urlparse(r['Location']).query)
        with mock.patch(
            'core.google_auth.canjear_codigo', return_value={'id_token': id_token(nonce=consulta['nonce'][0])}
        ) as canje:
            self.client.get(RETORNO, {'code': 'codigo-123', 'state': consulta['state'][0]})
        enviado = canje.call_args.args[0]
        self.assertEqual(enviado['redirect_uri'], 'https://agroasesor.example' + RETORNO)
        self.assertEqual(enviado['client_secret'], 'secreto-de-prueba')  # solo viaja servidor → Google
        self.assertEqual(enviado['grant_type'], 'authorization_code')
        self.assertTrue(enviado['code_verifier'])

    def test_el_post_de_inicio_exige_token_csrf(self):
        r = Client(enforce_csrf_checks=True).post('/cuenta/google/')  # sin token: la vista de fallo CSRF vuelve al login
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        self.assertNotIn('accounts.google.com', r['Location'])

    def test_el_retorno_sin_sesion_se_rechaza_y_el_log_explica_la_causa(self):
        with self.assertLogs('core.google_auth', 'WARNING') as registro:
            r = Client().get(RETORNO, {'code': 'x', 'state': 'y'})
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        self.assertIn('cookie de sesión', ' '.join(registro.output))

    def test_un_state_distinto_se_rechaza(self):
        self.client.post('/cuenta/google/')
        with self.assertLogs('core.google_auth', 'WARNING') as registro, mock.patch(
            'core.google_auth.canjear_codigo'
        ) as canje:
            self.client.get(RETORNO, {'code': 'x', 'state': 'falso'})
        canje.assert_not_called()
        self.assertIn('state no coincide', ' '.join(registro.output))

    def test_google_rechaza_el_canje_y_el_log_dice_el_motivo_sin_secretos(self):
        r = self.client.post('/cuenta/google/')
        estado = parse_qs(urlparse(r['Location']).query)['state'][0]
        cuerpo = io.BytesIO(json.dumps({'error': 'redirect_uri_mismatch', 'error_description': 'secreto-de-prueba'}).encode())
        rechazo = urllib.error.HTTPError(google_auth.URL_TOKEN, 400, 'Bad Request', {}, cuerpo)
        with self.assertLogs('core.google_auth', 'WARNING') as registro, mock.patch(
            'urllib.request.urlopen', side_effect=rechazo
        ):
            respuesta = self.client.get(RETORNO, {'code': 'x', 'state': estado})
        self.assertRedirects(respuesta, '/login/', fetch_redirect_response=False)
        salida = ' '.join(registro.output)
        self.assertIn('redirect_uri_mismatch', salida)  # el motivo más común al configurar Google Cloud
        self.assertNotIn('secreto-de-prueba', salida)
        self.assertNotIn('codigo', salida)


class CodigoDeErrorTests(SimpleTestCase):
    def error(self, cuerpo, codigo=400):
        return urllib.error.HTTPError('u', codigo, 'x', {}, io.BytesIO(cuerpo) if cuerpo is not None else None)

    def test_codigos_conocidos_de_google(self):
        for codigo in ('redirect_uri_mismatch', 'invalid_client', 'invalid_grant'):
            cuerpo = json.dumps({'error': codigo}).encode()
            self.assertEqual(google_auth._codigo_de_error(self.error(cuerpo)), codigo)

    def test_cuerpos_raros_caen_al_estado_http_sin_filtrar_contenido(self):
        for cuerpo in (b'<html>x</html>', b'{"error": {"a": 1}}', b'{"error": "con espacios y <b>"}', b'\xff', None):
            with self.subTest(cuerpo=cuerpo):
                self.assertEqual(google_auth._codigo_de_error(self.error(cuerpo)), 'HTTP 400')
