"""Endurecimiento de seguridad: DEBUG seguro en Vercel, HTTPS estricto y límite de intentos.

Los ajustes se cargan en un proceso aparte (como en ``test_produccion_vercel``) para no depender del ``.env`` local.
Los límites se prueban contra la base de pruebas (tabla de caché compartida); no se conecta a ningún servicio.
"""

import json
import subprocess
import sys
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core import limites
from core.models import CacheCompartida
from core.tests.test_produccion_vercel import RAIZ, SCRIPT, entorno


def cargar_sin(nombres, quitar=(), **variables):
    env = entorno(NOMBRES_A_LEER=json.dumps(list(nombres)), **variables)
    for nombre in quitar:
        env.pop(nombre, None)
    proceso = subprocess.run([sys.executable, '-c', SCRIPT], cwd=RAIZ, env=env, capture_output=True, text=True, timeout=60)
    if proceso.returncode:
        raise AssertionError(proceso.stderr[-1500:])
    return json.loads(proceso.stdout.strip().splitlines()[-1])


class DebugPorDefectoTests(SimpleTestCase):
    def test_en_vercel_sin_variable_debug_queda_apagado(self):
        ajustes = cargar_sin(['DEBUG', 'SESSION_COOKIE_SECURE'], quitar=['DJANGO_DEBUG'], VERCEL='1')
        self.assertFalse(ajustes['DEBUG'])
        self.assertTrue(ajustes['SESSION_COOKIE_SECURE'])

    def test_en_vercel_variable_vacia_tambien_lo_apaga(self):
        self.assertFalse(cargar_sin(['DEBUG'], VERCEL='1', DJANGO_DEBUG='')['DEBUG'])

    def test_en_local_sin_variable_sigue_encendido(self):
        self.assertTrue(cargar_sin(['DEBUG'], quitar=['DJANGO_DEBUG'])['DEBUG'])

    def test_en_vercel_sin_clave_secreta_no_arranca(self):
        with self.assertRaises(AssertionError) as error:
            cargar_sin(['DEBUG'], quitar=['DJANGO_DEBUG', 'DJANGO_SECRET_KEY'], VERCEL='1')
        self.assertIn('DJANGO_SECRET_KEY', str(error.exception))


class HttpsEstrictoTests(SimpleTestCase):
    NOMBRES = ['SECURE_HSTS_SECONDS', 'SECURE_SSL_REDIRECT', 'SECURE_HSTS_INCLUDE_SUBDOMAINS']

    def test_en_vercel_hsts_y_redireccion_a_https(self):
        ajustes = cargar_sin(self.NOMBRES, VERCEL='1')
        self.assertEqual(ajustes['SECURE_HSTS_SECONDS'], 30 * 24 * 3600)
        self.assertTrue(ajustes['SECURE_SSL_REDIRECT'])
        self.assertFalse(ajustes['SECURE_HSTS_INCLUDE_SUBDOMAINS'])

    def test_sin_proxy_de_confianza_no_redirige(self):
        ajustes = cargar_sin(self.NOMBRES)
        self.assertFalse(ajustes['SECURE_SSL_REDIRECT'])

    def test_en_desarrollo_no_hay_hsts(self):
        ajustes = cargar_sin(self.NOMBRES, DJANGO_DEBUG='True')
        self.assertIn(ajustes['SECURE_HSTS_SECONDS'], (0, None))
        self.assertFalse(ajustes['SECURE_SSL_REDIRECT'])


LIMITES_PEQUENOS = {
    'LIMITE_INTENTOS_ACTIVO': True,
    'LIMITE_LOGIN_CUENTA': (3, 900),
    'LIMITE_LOGIN_IP': (5, 900),
    'LIMITE_REGISTRO_IP': (2, 3600),
    'LIMITE_RECUPERAR_IP': (3, 3600),
    'LIMITE_RECUPERAR_CORREO': (1, 3600),
}


@override_settings(**LIMITES_PEQUENOS)
class LimiteLoginTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ana', 'ana@example.com', 'clave-segura-123')

    def _entrar(self, usuario='ana', clave='mala', **extra):
        return self.client.post(reverse('login'), {'username': usuario, 'password': clave}, **extra)

    def test_bloquea_la_cuenta_tras_varios_fallos_aunque_la_clave_sea_correcta(self):
        for _ in range(3):
            self._entrar()
        respuesta = self._entrar(clave='clave-segura-123')
        self.assertEqual(respuesta.status_code, 429)
        self.assertContains(respuesta, 'Demasiados intentos', status_code=429)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_el_bloqueo_vence_con_la_ventana(self):
        for _ in range(3):
            self._entrar()
        with mock.patch('core.limites.time.time', return_value=10**10):
            respuesta = self._entrar(clave='clave-segura-123')
        self.assertEqual(respuesta.status_code, 302)

    def test_un_acceso_correcto_reinicia_el_contador_de_la_cuenta(self):
        self._entrar()
        self._entrar()
        self.assertEqual(self._entrar(clave='clave-segura-123').status_code, 302)
        self.client.logout()
        self._entrar()
        self._entrar()
        self.assertEqual(self._entrar(clave='clave-segura-123').status_code, 302)

    def test_el_correo_y_el_usuario_sin_mayusculas_comparten_contador(self):
        self._entrar('ANA')
        self._entrar('Ana')
        self._entrar('ana')
        self.assertEqual(self._entrar('ana', 'clave-segura-123').status_code, 429)

    def test_limite_por_ip_con_cuentas_distintas(self):
        for i in range(5):
            self._entrar(f'nadie{i}')
        self.assertEqual(self._entrar(clave='clave-segura-123').status_code, 429)

    def test_otra_ip_no_queda_bloqueada_por_ip(self):
        for i in range(5):
            self._entrar(f'nadie{i}', REMOTE_ADDR='10.0.0.1')
        self.assertEqual(self._entrar(clave='clave-segura-123', REMOTE_ADDR='10.0.0.2').status_code, 302)

    def test_admin_de_django_tambien_queda_limitado(self):
        for _ in range(3):
            self.client.post(reverse('admin:login'), {'username': 'ana', 'password': 'mala'})
        self.assertEqual(self._entrar(clave='clave-segura-123').status_code, 429)

    def test_en_la_base_no_quedan_correos_ni_ip(self):
        self._entrar('ana@example.com', REMOTE_ADDR='203.0.113.9')
        claves = ' '.join(CacheCompartida.objects.values_list('clave', flat=True))
        cargas = ' '.join(CacheCompartida.objects.values_list('carga', flat=True))
        for dato in ('ana@example.com', 'ana', '203.0.113.9'):
            self.assertNotIn(dato, claves.replace('lim-', ''))
            self.assertNotIn(dato, cargas)

    @override_settings(LIMITE_INTENTOS_ACTIVO=False)
    def test_se_puede_desactivar(self):
        for _ in range(5):
            self._entrar()
        self.assertEqual(self._entrar(clave='clave-segura-123').status_code, 302)

    def test_si_la_base_falla_no_se_impide_el_acceso(self):
        from django.db import DatabaseError

        with mock.patch('core.cache_compartida.leer', side_effect=DatabaseError):
            with mock.patch('core.cache_compartida.guardar', side_effect=DatabaseError):
                self.assertEqual(self._entrar(clave='clave-segura-123').status_code, 302)


class IpClienteTests(SimpleTestCase):
    def _request(self, **meta):
        return mock.Mock(META={'REMOTE_ADDR': '10.1.1.1', **meta})

    def test_sin_proxy_de_confianza_ignora_x_forwarded_for(self):
        with override_settings(SECURE_PROXY_SSL_HEADER=None):
            self.assertEqual(limites.ip_cliente(self._request(HTTP_X_FORWARDED_FOR='1.2.3.4')), '10.1.1.1')

    def test_con_proxy_de_confianza_usa_la_primera_ip(self):
        with override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https')):
            self.assertEqual(limites.ip_cliente(self._request(HTTP_X_FORWARDED_FOR='1.2.3.4, 10.0.0.1')), '1.2.3.4')


@override_settings(**LIMITES_PEQUENOS)
class LimiteRegistroTests(TestCase):
    def _registrar(self, n, **extra):
        return self.client.post(
            reverse('login'),
            {
                'mode': 'register',
                'nombre_completo': f'Persona Prueba{"x" * n}',
                'username': f'persona{n}',
                'email': f'persona{n}@example.com',
                'password1': 'Clave-Muy-Segura-2026',
                'password2': 'Clave-Muy-Segura-2026',
                'telefono': '+56912345678',
                'comuna': 'Melipilla',
                'acepta_politicas': 'on',
            },
            **extra,
        )

    def test_formularios_con_errores_no_gastan_intentos(self):
        for _ in range(4):
            self.client.post(reverse('login'), {'mode': 'register', 'username': 'x'})
        respuesta = self.client.post(reverse('login'), {'mode': 'register', 'username': 'x'})
        self.assertNotEqual(respuesta.status_code, 429)

    def test_bloquea_tras_crear_varias_cuentas_desde_la_misma_ip(self):
        with mock.patch('core.views.cuentas.RegistroForm') as formulario:
            formulario.return_value.is_valid.return_value = True
            formulario.return_value.save.side_effect = lambda: get_user_model().objects.create_user(
                f'u{get_user_model().objects.count()}'
            )
            for i in range(2):
                self.assertEqual(self._registrar(i).status_code, 302)
                self.client.logout()
            respuesta = self._registrar(9)
        self.assertEqual(respuesta.status_code, 429)
        self.assertContains(respuesta, 'Demasiados intentos', status_code=429)


@override_settings(**LIMITES_PEQUENOS)
class LimiteRecuperacionTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user('ana', 'ana@example.com', 'clave-segura-123')

    def _pedir(self, correo='ana@example.com'):
        return self.client.post(reverse('password_reset'), {'email': correo})

    def test_un_correo_no_recibe_mas_mensajes_que_el_limite_y_la_respuesta_es_igual(self):
        primera = self._pedir()
        segunda = self._pedir()
        self.assertEqual(primera.status_code, 302)
        self.assertEqual(segunda.status_code, 302)
        self.assertEqual(primera['Location'], segunda['Location'])
        self.assertEqual(len(mail.outbox), 1)

    def test_bloquea_por_ip(self):
        for i in range(3):
            self._pedir(f'otro{i}@example.com')
        respuesta = self._pedir()
        self.assertEqual(respuesta.status_code, 429)
        self.assertContains(respuesta, 'Demasiados intentos', status_code=429)
        self.assertEqual(len(mail.outbox), 0)
