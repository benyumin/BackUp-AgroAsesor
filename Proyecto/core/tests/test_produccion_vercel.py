"""Configuración de producción (Vercel) leída desde variables de entorno.

Cada caso carga ``AgroAsesor.settings`` en un proceso aparte con SOLO las variables indicadas (así no depende del
``.env`` de quien ejecute las pruebas ni del entorno de la máquina). No se conecta a ninguna base de datos ni servicio:
los valores de clave son ficticios.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

RAIZ = Path(__file__).resolve().parent.parent.parent  # carpeta que contiene manage.py
VARIABLES_DEL_PROYECTO = (
    'VERCEL', 'DJANGO_CONFIAR_PROXY_HTTPS', 'SITE_URL', 'EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USE_TLS',
    'EMAIL_USE_SSL', 'EMAIL_TIMEOUT', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'DEFAULT_FROM_EMAIL', 'CODESPACES',
    'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI', 'OPEN_METEO_API_KEY', 'CLIMA_CACHE_SEGUNDOS',
    'CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS', 'CLIMA_PRECISION_COORDENADAS', 'CLIMA_TIMEOUT_SEGUNDOS',
    'CLIMA_ESPERA_LIMITE_SEGUNDOS', 'CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS', 'CLIMA_ESPERA_CONCURRENTE_SEGUNDOS',
    'CLIMA_CACHE_COMPARTIDA',
)
BASE = {
    'DJANGO_DEBUG': 'False',
    'DJANGO_SECRET_KEY': 'clave-ficticia-solo-para-pruebas-0123456789',
    'DJANGO_ALLOWED_HOSTS': 'localhost',
    'DJANGO_CSRF_TRUSTED_ORIGINS': '',
    'DATABASE_URL': 'sqlite:////tmp/agroasesor-prueba-settings.sqlite3',
}
SCRIPT = (
    'import json, os\n'
    'os.environ["DJANGO_SETTINGS_MODULE"] = "AgroAsesor.settings"\n'
    'from django.conf import settings\n'
    'print(json.dumps({n: getattr(settings, n, None) for n in json.loads(os.environ["NOMBRES_A_LEER"])}))\n'
)


def entorno(**variables):
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES_DEL_PROYECTO}
    env.update(dict.fromkeys(VARIABLES_DEL_PROYECTO, ''))  # vacías: el .env local no las rellena (load_dotenv no pisa)
    env.update(BASE)
    env.update(variables)
    return env


def cargar(nombres, **variables):
    """Valores de esos ajustes con esas variables de entorno. Devuelve (ajustes, texto de stderr)."""
    env = entorno(NOMBRES_A_LEER=json.dumps(list(nombres)), **variables)
    proceso = subprocess.run([sys.executable, '-c', SCRIPT], cwd=RAIZ, env=env, capture_output=True, text=True, timeout=60)
    if proceso.returncode:
        raise AssertionError(proceso.stderr[-1500:])
    return json.loads(proceso.stdout.strip().splitlines()[-1]), proceso.stderr


class ProxyHttpsTests(SimpleTestCase):
    def test_fuera_de_vercel_no_se_confia_en_x_forwarded_proto(self):
        self.assertIsNone(cargar(['SECURE_PROXY_SSL_HEADER'])[0]['SECURE_PROXY_SSL_HEADER'])

    def test_en_vercel_django_sabe_que_la_peticion_llego_por_https(self):
        ajustes, _ = cargar(['SECURE_PROXY_SSL_HEADER', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE'], VERCEL='1')
        self.assertEqual(ajustes['SECURE_PROXY_SSL_HEADER'], ['HTTP_X_FORWARDED_PROTO', 'https'])
        self.assertTrue(ajustes['SESSION_COOKIE_SECURE'] and ajustes['CSRF_COOKIE_SECURE'])

    def test_se_puede_pedir_expresamente_para_otro_proxy(self):
        ajustes, _ = cargar(['SECURE_PROXY_SSL_HEADER'], DJANGO_CONFIAR_PROXY_HTTPS='true')
        self.assertEqual(ajustes['SECURE_PROXY_SSL_HEADER'], ['HTTP_X_FORWARDED_PROTO', 'https'])


class SitioPublicoTests(SimpleTestCase):
    NOMBRES = ['SITE_URL', 'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS']

    def test_site_url_se_normaliza_y_se_acepta_como_host_y_origen(self):
        ajustes, _ = cargar(self.NOMBRES, SITE_URL=' https://agroasesor.vercel.app/ ')
        self.assertEqual(ajustes['SITE_URL'], 'https://agroasesor.vercel.app')
        self.assertIn('agroasesor.vercel.app', ajustes['ALLOWED_HOSTS'])
        self.assertIn('https://agroasesor.vercel.app', ajustes['CSRF_TRUSTED_ORIGINS'])
        self.assertIn('localhost', ajustes['ALLOWED_HOSTS'])  # lo que ya estaba configurado se conserva

    def test_un_valor_mal_escrito_se_ignora_con_un_aviso_y_no_tumba_el_sitio(self):
        for malo in ('agroasesor.vercel.app', 'ftp://agroasesor.example', 'https://agroasesor.example/ruta', 'https://'):
            with self.subTest(malo):
                ajustes, aviso = cargar(self.NOMBRES, SITE_URL=malo)
                self.assertEqual(ajustes['SITE_URL'], '')
                self.assertNotIn('agroasesor.example', ajustes['ALLOWED_HOSTS'])
                self.assertIn('SITE_URL', aviso)

    def test_sin_site_url_no_cambia_nada(self):
        ajustes, aviso = cargar(self.NOMBRES)
        self.assertEqual((ajustes['SITE_URL'], ajustes['ALLOWED_HOSTS'], ajustes['CSRF_TRUSTED_ORIGINS']), ('', ['localhost'], []))
        self.assertNotIn('SITE_URL', aviso)


class CorreoTests(SimpleTestCase):
    def mailer(self, **variables):
        ajustes, _ = cargar(['MAILERS', 'DEFAULT_FROM_EMAIL'], **variables)
        return ajustes['MAILERS']['default'], ajustes['DEFAULT_FROM_EMAIL']

    def test_sin_servidor_los_correos_solo_se_escriben_en_el_log_desarrollo(self):
        config, _ = self.mailer()
        self.assertTrue(config['BACKEND'].endswith('console.EmailBackend'))
        self.assertEqual(config['OPTIONS'], {})

    def test_con_host_se_usa_smtp_sin_tener_que_nombrar_el_backend(self):
        config, remitente = self.mailer(
            EMAIL_HOST='smtp.proveedor.example', EMAIL_HOST_USER='usuario-falso', EMAIL_HOST_PASSWORD='clave-falsa',
            DEFAULT_FROM_EMAIL='AgroAsesor <no-responder@agroasesor.example>',
        )
        self.assertTrue(config['BACKEND'].endswith('smtp.EmailBackend'))
        self.assertEqual(
            config['OPTIONS'],
            {'host': 'smtp.proveedor.example', 'port': 587, 'use_tls': True, 'use_ssl': False,
             'username': 'usuario-falso', 'password': 'clave-falsa', 'timeout': 10},
        )
        self.assertEqual(remitente, 'AgroAsesor <no-responder@agroasesor.example>')

    def test_puerto_465_usa_ssl_implicito_y_no_starttls(self):
        opciones = self.mailer(EMAIL_HOST='smtp.proveedor.example', EMAIL_PORT='465')[0]['OPTIONS']
        self.assertEqual((opciones['port'], opciones['use_ssl'], opciones['use_tls']), (465, True, False))

    def test_ssl_y_tls_nunca_quedan_activos_a_la_vez(self):
        opciones = self.mailer(EMAIL_HOST='h.example', EMAIL_PORT='587', EMAIL_USE_SSL='true', EMAIL_USE_TLS='true')[0]['OPTIONS']
        self.assertTrue(opciones['use_ssl'])
        self.assertFalse(opciones['use_tls'])  # Django lanzaría ValueError con ambos

    def test_se_puede_desactivar_tls_para_un_relay_interno(self):
        opciones = self.mailer(EMAIL_HOST='h.example', EMAIL_PORT='25', EMAIL_USE_TLS='false')[0]['OPTIONS']
        self.assertEqual((opciones['use_tls'], opciones['use_ssl']), (False, False))

    def test_las_credenciales_pegadas_con_espacios_o_saltos_de_linea_se_recortan(self):
        opciones = self.mailer(EMAIL_HOST=' h.example ', EMAIL_HOST_USER=' u ', EMAIL_HOST_PASSWORD='  k-falsa\n')[0]['OPTIONS']
        self.assertEqual((opciones['host'], opciones['username'], opciones['password']), ('h.example', 'u', 'k-falsa'))

    def test_valores_invalidos_vuelven_al_valor_seguro(self):
        opciones = self.mailer(EMAIL_HOST='h.example', EMAIL_PORT='abc', EMAIL_TIMEOUT='9999')[0]['OPTIONS']
        self.assertEqual((opciones['port'], opciones['timeout']), (587, 30))

    def test_el_backend_explicito_manda_sobre_la_deteccion(self):
        config, _ = self.mailer(EMAIL_HOST='h.example', EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
        self.assertTrue(config['BACKEND'].endswith('console.EmailBackend'))
        self.assertEqual(config['OPTIONS'], {})


class ClimaTests(SimpleTestCase):
    NOMBRES = [
        'CLIMA_CACHE_SEGUNDOS', 'CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS', 'CLIMA_PRECISION_COORDENADAS',
        'CLIMA_TIMEOUT_SEGUNDOS', 'CLIMA_ESPERA_LIMITE_SEGUNDOS', 'CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS',
        'CLIMA_ESPERA_CONCURRENTE_SEGUNDOS', 'CLIMA_CACHE_COMPARTIDA', 'CLIMA_API_KEY',
    ]

    def test_valores_por_defecto(self):
        ajustes, _ = cargar(self.NOMBRES)
        self.assertEqual(ajustes, {
            'CLIMA_CACHE_SEGUNDOS': 900, 'CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS': 21600, 'CLIMA_PRECISION_COORDENADAS': 2,
            'CLIMA_TIMEOUT_SEGUNDOS': 6, 'CLIMA_ESPERA_LIMITE_SEGUNDOS': 120, 'CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS': 1800,
            'CLIMA_ESPERA_CONCURRENTE_SEGUNDOS': 3, 'CLIMA_CACHE_COMPARTIDA': True, 'CLIMA_API_KEY': '',
        })

    def test_todo_es_configurable_por_variables_de_entorno(self):
        ajustes, _ = cargar(
            self.NOMBRES, CLIMA_CACHE_SEGUNDOS='1800', CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS='7200',
            CLIMA_PRECISION_COORDENADAS='3', CLIMA_TIMEOUT_SEGUNDOS='8', CLIMA_ESPERA_LIMITE_SEGUNDOS='300',
            CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS='900', CLIMA_ESPERA_CONCURRENTE_SEGUNDOS='1',
            CLIMA_CACHE_COMPARTIDA='false', OPEN_METEO_API_KEY=' clave-ficticia ',
        )
        self.assertEqual(ajustes['CLIMA_CACHE_SEGUNDOS'], 1800)
        self.assertEqual(ajustes['CLIMA_PRECISION_COORDENADAS'], 3)
        self.assertEqual(ajustes['CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS'], 900)
        self.assertFalse(ajustes['CLIMA_CACHE_COMPARTIDA'])
        self.assertEqual(ajustes['CLIMA_API_KEY'], 'clave-ficticia')

    def test_valores_absurdos_se_acotan_o_vuelven_al_defecto(self):
        ajustes, _ = cargar(
            self.NOMBRES, CLIMA_PRECISION_COORDENADAS='9', CLIMA_TIMEOUT_SEGUNDOS='mucho', CLIMA_CACHE_SEGUNDOS='-5',
            CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS='99999999',
        )
        self.assertEqual(ajustes['CLIMA_PRECISION_COORDENADAS'], 4)
        self.assertEqual(ajustes['CLIMA_TIMEOUT_SEGUNDOS'], 6)
        self.assertEqual(ajustes['CLIMA_CACHE_SEGUNDOS'], 0)
        self.assertEqual(ajustes['CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS'], 7200)


class ArranqueTests(SimpleTestCase):
    def test_manage_py_check_pasa_con_la_configuracion_de_vercel_smtp_y_google(self):
        env = entorno(
            VERCEL='1', SITE_URL='https://agroasesor.vercel.app', EMAIL_HOST='smtp.proveedor.example',
            EMAIL_PORT='465', EMAIL_HOST_USER='usuario-falso', EMAIL_HOST_PASSWORD='clave-falsa',
            DEFAULT_FROM_EMAIL='AgroAsesor <no-responder@agroasesor.example>',
            GOOGLE_CLIENT_ID='id-falso.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET='secreto-falso',
        )
        proceso = subprocess.run(
            [sys.executable, 'manage.py', 'check'], cwd=RAIZ, env=env, capture_output=True, text=True, timeout=90
        )
        self.assertEqual(proceso.returncode, 0, proceso.stderr[-1500:])
        self.assertIn('no issues', proceso.stdout)
