"""Inicio de sesión con Google (OAuth 2.0 + OpenID Connect con PKCE).

Google no se contacta en las pruebas: se simula su endpoint de tokens (``core.google_auth.canjear_codigo``) con
un id_token construido aquí. Se comprueban el botón, el estado (state), el nonce, PKCE, la validación del
id_token, la vinculación con cuentas existentes sin duplicar, el registro de agricultor y la conservación de roles.
"""

import base64
import hashlib
import json
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from core import google_auth
from core.models import AceptacionPoliticas, Agricultor

User = get_user_model()
CLIENT_ID = 'cliente-de-prueba.apps.googleusercontent.com'
GOOGLE = {'GOOGLE_CLIENT_ID': CLIENT_ID, 'GOOGLE_CLIENT_SECRET': 'secreto-de-prueba', 'GOOGLE_REDIRECT_URI': ''}


def _b64(datos):
    return base64.urlsafe_b64encode(json.dumps(datos).encode()).rstrip(b'=').decode()


def id_token(**valores):
    datos = {
        'iss': 'https://accounts.google.com',
        'aud': CLIENT_ID,
        'exp': time.time() + 300,
        'iat': time.time(),
        'sub': '1234567890',
        'email': 'nueva@gmail.com',
        'email_verified': True,
        'name': 'Camila Rojas',
    }
    datos.update(valores)
    return f'{_b64({"alg": "RS256"})}.{_b64(datos)}.firma'


@override_settings(**GOOGLE)
class GoogleOAuthTests(TestCase):
    def iniciar(self, client=None):
        c = client or self.client
        r = c.post('/cuenta/google/')
        self.assertEqual(r.status_code, 302)
        return parse_qs(urlparse(r['Location']).query), r['Location']

    def volver(self, client=None, token_kwargs=None, state=None, respuesta=None):
        """Simula que Google redirige de vuelta con un código válido."""
        c = client or self.client
        params, _ = self.iniciar(c)
        nonce = params['nonce'][0]
        datos = respuesta if respuesta is not None else {'id_token': id_token(nonce=nonce, **(token_kwargs or {}))}
        with mock.patch('core.google_auth.canjear_codigo', return_value=datos) as canje:
            r = c.get('/cuenta/google/retorno/', {'code': 'codigo-123', 'state': state or params['state'][0]})
        return r, canje, params

    # ---- Botón e inicio del flujo ----

    def test_boton_aparece_en_login_y_registro_y_envia_post_con_csrf(self):
        for url in ('/login/', '/login/?mode=register'):
            html = self.client.get(url).content.decode()
            self.assertIn('O continúa con', html, url)
            self.assertIn('<form method="post" action="/cuenta/google/" class="access-social">', html, url)
            bloque = html[html.index('action="/cuenta/google/"') :]
            self.assertLess(bloque.index('csrfmiddlewaretoken'), bloque.index('</form>'))
            self.assertIn('Continuar con Google', bloque)
            self.assertIn('fill="#4285F4"', bloque)  # logotipo oficial de Google
            self.assertNotIn('secreto-de-prueba', html)  # el secreto nunca llega al navegador

    @override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='')
    def test_sin_credenciales_no_hay_boton_ni_flujo(self):
        html = self.client.get('/login/').content.decode()
        self.assertNotIn('Continuar con Google', html)
        r = self.client.post('/cuenta/google/', follow=True)
        self.assertContains(r, 'todavía no está configurado')

    @override_settings(GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', DEBUG=True)
    def test_en_desarrollo_sin_credenciales_el_boton_se_ve_con_el_aviso_de_configuracion(self):
        for url in ('/login/', '/login/?mode=register'):
            html = self.client.get(url).content.decode()
            self.assertIn('Continuar con Google', html, url)
            self.assertIn('class="access-nota"', html, url)
            self.assertIn('<code>GOOGLE_CLIENT_ID</code>', html, url)
        r = self.client.post('/cuenta/google/', follow=True)  # no simula un ingreso: vuelve con el aviso
        self.assertContains(r, 'faltan GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_con_credenciales_no_aparece_el_aviso_de_configuracion(self):
        self.assertNotContains(self.client.get('/login/'), 'class="access-nota"')

    def test_iniciar_exige_post_y_csrf(self):
        self.assertEqual(self.client.get('/cuenta/google/').status_code, 405)
        estricto = Client(enforce_csrf_checks=True)
        r = estricto.post('/cuenta/google/')
        self.assertNotIn('accounts.google.com', r.get('Location', ''))

    def test_redirige_a_google_con_state_nonce_y_pkce(self):
        params, destino = self.iniciar()
        self.assertTrue(destino.startswith('https://accounts.google.com/o/oauth2/v2/auth?'))
        self.assertEqual(params['client_id'], [CLIENT_ID])
        self.assertEqual(params['response_type'], ['code'])
        self.assertEqual(params['scope'], ['openid email profile'])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertEqual(params['redirect_uri'], ['http://testserver/cuenta/google/retorno/'])
        flujo = self.client.session[google_auth.CLAVE_FLUJO]
        self.assertEqual(params['state'], [flujo['state']])
        self.assertEqual(params['nonce'], [flujo['nonce']])
        esperado = base64.urlsafe_b64encode(hashlib.sha256(flujo['verificador'].encode()).digest()).rstrip(b'=')
        self.assertEqual(params['code_challenge'], [esperado.decode()])
        self.assertNotIn('client_secret', destino)

    # ---- Retorno desde Google ----

    def test_cuenta_existente_entra_sin_duplicar_y_conserva_su_rol(self):
        admin = User.objects.create_user('admin@example.com', email='Admin@Example.com', password='x', is_staff=True)
        r, canje, params = self.volver(token_kwargs={'email': 'admin@example.com'})
        self.assertRedirects(r, '/panel-admin/', fetch_redirect_response=False)  # el administrador entra a su panel
        self.assertEqual(int(self.client.session['_auth_user_id']), admin.pk)
        self.assertEqual(User.objects.filter(email__iexact='admin@example.com').count(), 1)
        admin.refresh_from_db()
        self.assertTrue(admin.is_staff)  # roles y permisos intactos
        self.assertTrue(admin.check_password('x'))  # su contraseña sigue funcionando
        enviado = canje.call_args.args[0]
        self.assertEqual(enviado['grant_type'], 'authorization_code')
        self.assertEqual(enviado['client_secret'], 'secreto-de-prueba')
        self.assertEqual(enviado['redirect_uri'], params['redirect_uri'][0])
        self.assertTrue(enviado['code_verifier'])
        self.assertNotIn(google_auth.CLAVE_FLUJO, self.client.session)  # el state no se puede reutilizar

    def test_agricultor_existente_entra_a_su_cuenta(self):
        user = User.objects.create_user('ana@example.com', email='ana@example.com', password='x')
        r, _, _ = self.volver(token_kwargs={'email': 'ANA@example.com'})
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertFalse(User.objects.get(pk=user.pk).is_staff)

    def test_cuenta_desactivada_no_entra(self):
        User.objects.create_user('baja@example.com', email='baja@example.com', password='x', is_active=False)
        r, _, _ = self.volver(token_kwargs={'email': 'baja@example.com'})
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_state_distinto_se_rechaza_sin_canjear_el_codigo(self):
        r, canje, _ = self.volver(state='otro-state')
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        canje.assert_not_called()
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_retorno_sin_haber_iniciado_se_rechaza(self):
        with mock.patch('core.google_auth.canjear_codigo') as canje:
            r = self.client.get('/cuenta/google/retorno/', {'code': 'c', 'state': 's'})
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        canje.assert_not_called()

    def test_flujo_vencido_se_rechaza(self):
        params, _ = self.iniciar()
        sesion = self.client.session
        sesion[google_auth.CLAVE_FLUJO]['creado'] = time.time() - google_auth.VIGENCIA_FLUJO - 5
        sesion.save()
        with mock.patch('core.google_auth.canjear_codigo') as canje:
            r = self.client.get('/cuenta/google/retorno/', {'code': 'c', 'state': params['state'][0]})
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        canje.assert_not_called()

    def test_cancelar_en_google_vuelve_al_login_con_aviso(self):
        self.iniciar()
        r = self.client.get('/cuenta/google/retorno/', {'error': 'access_denied'}, follow=True)
        self.assertContains(r, 'Cancelaste el inicio de sesión con Google')

    def test_id_token_invalido_se_rechaza(self):
        User.objects.create_user('ana@example.com', email='ana@example.com', password='x')
        casos = (
            {'email': 'ana@example.com', 'aud': 'otra-app.apps.googleusercontent.com'},
            {'email': 'ana@example.com', 'iss': 'https://evil.example.com'},
            {'email': 'ana@example.com', 'exp': time.time() - 3600},
            {'email': 'ana@example.com', 'email_verified': False},
            {'email': ''},
        )
        for valores in casos:
            with self.subTest(valores=valores):
                c = Client()
                r, _, _ = self.volver(client=c, token_kwargs=valores)
                self.assertRedirects(r, '/login/', fetch_redirect_response=False)
                self.assertNotIn('_auth_user_id', c.session)

    def test_nonce_distinto_se_rechaza(self):
        User.objects.create_user('ana@example.com', email='ana@example.com', password='x')
        params, _ = self.iniciar()
        malo = {'id_token': id_token(email='ana@example.com', nonce='otro')}
        with mock.patch('core.google_auth.canjear_codigo', return_value=malo):
            r = self.client.get('/cuenta/google/retorno/', {'code': 'c', 'state': params['state'][0]})
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_error_al_canjear_el_codigo_no_rompe_la_pagina(self):
        params, _ = self.iniciar()
        with mock.patch('core.google_auth.canjear_codigo', side_effect=google_auth.ErrorGoogle('red')):
            r = self.client.get('/cuenta/google/retorno/', {'code': 'c', 'state': params['state'][0]}, follow=True)
        self.assertContains(r, 'No se pudo completar el inicio de sesión con Google')
        r2, _, _ = self.volver(client=Client(), respuesta={'error': 'invalid_grant'})
        self.assertRedirects(r2, '/login/', fetch_redirect_response=False)

    # ---- Cuenta nueva: completar el registro de agricultor ----

    def test_cuenta_nueva_pide_los_datos_obligatorios_y_crea_un_agricultor(self):
        r, _, _ = self.volver()
        self.assertRedirects(r, '/cuenta/google/completar/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(User.objects.filter(email='nueva@gmail.com').exists())  # aún no se crea nada
        pagina = self.client.get('/cuenta/google/completar/')
        for texto in ('nueva@gmail.com', 'value="Camila Rojas"', 'name="telefono"', 'name="comuna"',
                      'name="acepta_politicas"', 'name="csrfmiddlewaretoken"', 'class="access-visual"'):
            self.assertContains(pagina, texto)
        self.assertNotContains(pagina, 'name="password1"')
        self.assertNotContains(pagina, 'name="rut"')  # el RUT ya no se pide
        incompleto = self.client.post('/cuenta/google/completar/', {'nombre_completo': 'Camila Rojas'})
        self.assertContains(incompleto, 'class="errorlist"')
        self.assertFalse(User.objects.filter(email='nueva@gmail.com').exists())

        r = self.client.post(
            '/cuenta/google/completar/',
            {
                'nombre_completo': 'Camila Rojas',
                'telefono': '+56 9 1111 2222',
                'comuna': 'Melipilla',
                'acepta_politicas': 'on',
            },
        )
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        user = User.objects.get(email='nueva@gmail.com')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(user.username, 'nueva@gmail.com')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())  # entra con Google; puede crear una clave al recuperar
        self.assertEqual((user.first_name, user.last_name), ('Camila', 'Rojas'))
        ficha = Agricultor.objects.get(user=user)
        self.assertEqual(ficha.comuna, 'Melipilla')
        self.assertTrue(AceptacionPoliticas.objects.filter(usuario=user).exists())
        self.assertNotIn(google_auth.CLAVE_REGISTRO, self.client.session)

        # La segunda vez con Google entra directo a la misma cuenta, sin duplicarla.
        self.client.post('/logout/')
        r, _, _ = self.volver()
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(email__iexact='nueva@gmail.com').count(), 1)

    def test_completar_sin_venir_de_google_vuelve_al_login(self):
        r = self.client.get('/cuenta/google/completar/')
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        r = self.client.post('/cuenta/google/completar/', {'nombre_completo': 'X'})
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)

    def test_si_el_correo_se_registra_mientras_tanto_no_se_duplica(self):
        self.volver()
        User.objects.create_user('nueva@gmail.com', email='nueva@gmail.com', password='x')
        r = self.client.post(
            '/cuenta/google/completar/',
            {'nombre_completo': 'Camila Rojas', 'telefono': '+56 9 1111 2222', 'comuna': 'Melipilla',
             'acepta_politicas': 'on'},
        )
        self.assertContains(r, 'Este correo electrónico ya está registrado')
        self.assertEqual(User.objects.filter(email__iexact='nueva@gmail.com').count(), 1)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_cuenta_creada_con_google_puede_crear_una_clave_y_cerrar_sesion(self):
        self.volver()
        self.client.post(
            '/cuenta/google/completar/',
            {'nombre_completo': 'Camila Rojas', 'telefono': '+56 9 1111 2222', 'comuna': 'Melipilla',
             'acepta_politicas': 'on'},
        )
        r = self.client.post('/logout/')
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        # Sin contraseña utilizable el login tradicional no la deja entrar con cualquier clave.
        malo = Client().post('/login/', {'mode': 'login', 'username': 'nueva@gmail.com', 'password': ''})
        self.assertEqual(malo.status_code, 200)

    def test_id_token_se_decodifica_y_valida(self):
        with override_settings(GOOGLE_CLIENT_ID=CLIENT_ID):
            correo, nombre = google_auth.validar_id_token(id_token(nonce='n', email='X@Gmail.com'), 'n')
        self.assertEqual((correo, nombre), ('x@gmail.com', 'Camila Rojas'))
        with self.assertRaises(google_auth.ErrorGoogle):
            google_auth.validar_id_token('no-es-un-jwt', 'n')
