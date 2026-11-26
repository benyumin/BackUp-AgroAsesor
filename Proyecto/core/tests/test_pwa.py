"""AgroAsesor como aplicación instalable (PWA): manifest, service worker, íconos, pantalla sin conexión y cabeceras.

Se comprueba lo que la instalación debe aportar (manifest válido, íconos 192/512 y maskable, service worker con
versión que cambia en cada despliegue, pantalla sin conexión, metaetiquetas de iPhone y botón de instalar) y,
sobre todo, lo que no debe tocar: rutas de autenticación, Google, recuperación de contraseña, CSRF y datos de la cuenta.
Todo con SQLite temporal: no se usa Supabase.
"""

import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from core import pwa

User = get_user_model()
PASSWORD = 'Campo-Seguro-2026!'
RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = RAIZ / 'static'
PAGINAS_PUBLICAS = ['/login/', '/login/?mode=register', '/cuenta/recuperar/', '/politicas/privacidad-y-cookies/']
PAGINAS_DE_LA_PLATAFORMA = ['/', '/predios/', '/actividades/', '/calendario/', '/calculadoras/', '/productos/']


def dimensiones_png(ruta):
    datos = Path(ruta).read_bytes()
    assert datos[:8] == b'\x89PNG\r\n\x1a\n', f'{ruta} no es PNG'
    return struct.unpack('>II', datos[16:24])


class ManifestTests(TestCase):
    def setUp(self):
        self.respuesta = self.client.get('/manifest.json')
        self.datos = json.loads(self.respuesta.content)

    def test_se_sirve_sin_sesion_y_con_el_tipo_correcto(self):
        self.assertEqual(self.respuesta.status_code, 200)
        self.assertTrue(self.respuesta['Content-Type'].startswith('application/manifest+json'))
        self.assertNotIn('Set-Cookie', self.respuesta)

    def test_solo_acepta_lectura(self):
        self.assertEqual(self.client.post('/manifest.json').status_code, 405)

    def test_identidad_y_modo_app(self):
        self.assertEqual(self.datos['name'], 'AgroAsesor')
        self.assertEqual(self.datos['short_name'], 'AgroAsesor')
        self.assertEqual(self.datos['display'], 'standalone')
        self.assertEqual(self.datos['scope'], '/')
        self.assertTrue(self.datos['start_url'].startswith('/'))
        self.assertEqual(self.datos['lang'], 'es-CL')

    def test_colores_son_los_del_tema_verde(self):
        tema = (ESTATICOS / 'css' / 'tema-figma.css').read_text(encoding='utf-8')
        self.assertIn(f'--green: {self.datos["theme_color"]};', tema)
        self.assertIn(f'--bg: {self.datos["background_color"]};', tema)

    def test_iconos_192_512_y_maskable_existen_con_su_tamano(self):
        combinaciones = {(i['sizes'], i['purpose']) for i in self.datos['icons']}
        for tam in ('192x192', '512x512'):
            self.assertIn((tam, 'any'), combinaciones)
            self.assertIn((tam, 'maskable'), combinaciones)
        for icono in self.datos['icons']:
            ruta = finders.find(icono['src'].removeprefix('/static/'))
            self.assertIsNotNone(ruta, icono['src'])
            ancho, alto = dimensiones_png(ruta)
            self.assertEqual(f'{ancho}x{alto}', icono['sizes'], icono['src'])
            self.assertEqual(icono['type'], 'image/png')

    def test_atajos_apuntan_a_rutas_reales_de_la_plataforma(self):
        self.assertGreaterEqual(len(self.datos['shortcuts']), 3)
        for atajo in self.datos['shortcuts']:
            self.assertIn(self.client.get(atajo['url']).status_code, (200, 302), atajo['url'])
            self.assertTrue(atajo['url'].startswith(self.datos['scope']))

    def test_ningun_dato_de_cuenta_en_el_manifest(self):
        usuario = User.objects.create_user('ana@example.com', email='ana@example.com', password=PASSWORD)
        self.client.force_login(usuario)
        cuerpo = self.client.get('/manifest.json').content.decode()
        self.assertNotIn('ana@example.com', cuerpo)
        self.assertNotIn('sessionid', cuerpo)


class ServiceWorkerTests(TestCase):
    def fuente(self):
        return self.client.get('/service-worker.js').content.decode()

    def test_se_sirve_en_la_raiz_sin_cache_y_con_alcance_total(self):
        respuesta = self.client.get('/service-worker.js')
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta['Content-Type'].startswith('text/javascript'))
        self.assertIn('no-cache', respuesta['Cache-Control'])
        self.assertEqual(respuesta['Service-Worker-Allowed'], '/')
        self.assertNotIn('Set-Cookie', respuesta)
        self.assertEqual(self.client.post('/service-worker.js').status_code, 405)

    def test_la_version_cambia_con_cada_despliegue(self):
        with patch.dict(os.environ, {'VERCEL_GIT_COMMIT_SHA': 'aaaaaaaaaaaa1111'}):
            uno = pwa.version_service_worker()
        with patch.dict(os.environ, {'VERCEL_GIT_COMMIT_SHA': 'bbbbbbbbbbbb2222'}):
            dos = pwa.version_service_worker()
        self.assertNotEqual(uno, dos)
        self.assertTrue(uno.startswith('aaaaaaaaaaaa-'))

    def test_la_version_viaja_en_el_archivo_para_que_el_navegador_vea_el_cambio(self):
        version = pwa.version_service_worker()
        self.assertIn(f"const VERSION = '{version}';", self.fuente())

    def test_no_intercepta_autenticacion_google_api_ni_admin(self):
        fuente = self.fuente()
        bloque = re.search(r'const SIN_INTERCEPTAR = \[(.*?)\];', fuente).group(1)
        rutas = [r.encode().decode('unicode_escape') for r in re.findall(r"'([^']+)'", bloque)]  # escapejs
        self.assertEqual(tuple(rutas), pwa.RUTAS_SIN_INTERCEPTAR)
        reales = [
            reverse('login'),
            reverse('logout'),
            reverse('enter_as_admin'),
            reverse('google_iniciar'),
            reverse('google_callback'),
            reverse('google_completar'),
            reverse('password_reset'),
            reverse('password_reset_done'),
            reverse('password_reset_complete'),
            reverse('password_reset_confirm', args=['uid', 'token']),
            reverse('api_v1', args=['calculos/']),
            reverse('predio_clima_api', args=[1]),
            reverse('admin:index'),
        ]
        for ruta in reales:
            if ruta.startswith('/api/') or ruta.startswith('/admin/'):
                self.assertTrue(any(ruta.startswith(p) for p in rutas), ruta)
            else:
                self.assertTrue(any(ruta.startswith(p) for p in rutas), f'{ruta} no está excluida')

    def test_solo_guarda_estaticos_y_la_pantalla_sin_conexion(self):
        fuente = self.fuente()
        self.assertIn("peticion.method !== 'GET'", fuente)  # POST (formularios, CSRF, login) pasa directo
        self.assertIn("credentials: 'omit'", fuente)  # nunca envía cookies a los CDN
        self.assertEqual(fuente.count('cache.put('), 2)  # estáticos propios y estáticos de CDN, nada más
        self.assertIn("respuesta.type === 'basic'", fuente)
        for prohibido in ('localStorage', 'sessionStorage', 'indexedDB', 'Authorization', 'csrf', 'document.cookie'):
            self.assertNotIn(prohibido, fuente)

    def test_no_guarda_paginas_html_ni_respuestas_de_la_api(self):
        fuente = self.fuente()
        navegar = fuente.split('async function navegar', 1)[1].split('async function respuestaSinConexion', 1)[0]
        self.assertNotIn('cache.put', navegar)
        self.assertNotIn('caches.', navegar)
        self.assertIn("url.pathname.startsWith(PREFIJO_ESTATICO)", fuente)

    def test_terceros_permitidos_son_solo_fuente_y_librerias_con_version_fija(self):
        fuente = self.fuente()
        for permitido in ('fonts.googleapis.com', 'fonts.gstatic.com', 'unpkg.com'):
            self.assertIn(permitido, fuente)
        self.assertIn('lucide@', fuente)
        self.assertIn('leaflet@', fuente)

    @skipUnless(shutil.which('node'), 'node no está instalado')
    def test_el_service_worker_es_javascript_valido(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / 'sw.js'
            ruta.write_text(self.fuente(), encoding='utf-8')
            resultado = subprocess.run(['node', '--check', str(ruta)], capture_output=True, text=True)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    @skipUnless(shutil.which('node'), 'node no está instalado')
    def test_el_script_de_instalacion_es_javascript_valido(self):
        resultado = subprocess.run(
            ['node', '--check', str(ESTATICOS / 'js' / 'pwa.js')], capture_output=True, text=True
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


class PantallaSinConexionTests(TestCase):
    def test_es_publica_autonoma_y_sin_datos_de_cuenta(self):
        usuario = User.objects.create_user('ana@example.com', email='ana@example.com', password=PASSWORD)
        self.client.force_login(usuario)
        respuesta = self.client.get('/sin-conexion/')
        html = respuesta.content.decode()
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn('ana@example.com', html)
        self.assertNotIn('csrf', html.lower())
        self.assertNotIn('<link rel="stylesheet" href="/static', html)  # autónoma: el CSS va en línea
        self.assertIn('Sin conexión', html)
        self.assertIn('id="reintentar"', html)
        self.assertIn('no-cache', respuesta['Cache-Control'])

    def test_usa_la_tipografia_y_el_verde_de_la_plataforma(self):
        html = self.client.get('/sin-conexion/').content.decode()
        self.assertIn('family=Outfit', html)
        self.assertIn('--green: #0b6b4b', html)

    def test_no_promete_que_todo_funcione_sin_internet(self):
        html = self.client.get('/sin-conexion/').content.decode().lower()
        self.assertNotIn('modo offline', html)
        self.assertNotIn('sincroniz', html)


class CabeceraPwaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_user('ana@example.com', email='ana@example.com', password=PASSWORD)

    def comprobar_cabecera(self, html, url):
        self.assertIn('<link rel="manifest" href="/manifest.json">', html, url)
        self.assertIn('<meta name="theme-color" content="#0b6b4b">', html, url)
        self.assertIn('<meta name="apple-mobile-web-app-capable" content="yes">', html, url)
        self.assertIn('<meta name="mobile-web-app-capable" content="yes">', html, url)
        self.assertIn('<meta name="apple-mobile-web-app-title" content="AgroAsesor">', html, url)
        self.assertIn('rel="apple-touch-icon"', html, url)
        self.assertIn('js/pwa.js', html, url)
        self.assertIn('data-sw="/service-worker.js"', html, url)
        self.assertEqual(html.count('rel="manifest"'), 1, url)
        self.assertEqual(html.count('js/pwa.js'), 1, url)

    def test_paginas_publicas_y_de_acceso(self):
        for url in PAGINAS_PUBLICAS:
            self.comprobar_cabecera(self.client.get(url).content.decode(), url)

    def test_paginas_de_la_plataforma(self):
        self.client.force_login(self.usuario)
        for url in PAGINAS_DE_LA_PLATAFORMA:
            respuesta = self.client.get(url)
            self.assertEqual(respuesta.status_code, 200, url)
            self.comprobar_cabecera(respuesta.content.decode(), url)

    def test_las_doce_pantallas_de_inicio_de_iphone_existen_y_coinciden_con_su_medida(self):
        html = self.client.get('/login/').content.decode()
        enlaces = re.findall(
            r'<link rel="apple-touch-startup-image"\s+href="([^"]+)"\s+media="([^"]+)"', html
        )
        self.assertEqual(len(enlaces), 12)
        for href, media in enlaces:
            ruta = finders.find(href.removeprefix('/static/'))
            self.assertIsNotNone(ruta, href)
            ancho_css, alto_css, densidad = (int(n) for n in re.search(
                r'device-width: (\d+)px\) and \(device-height: (\d+)px\) and \(-webkit-device-pixel-ratio: (\d+)', media
            ).groups())
            self.assertEqual(dimensiones_png(ruta), (ancho_css * densidad, alto_css * densidad), href)
            self.assertIn('orientation: portrait', media)

    def test_icono_de_iphone_es_180_sin_transparencia_de_esquinas(self):
        self.assertEqual(dimensiones_png(ESTATICOS / 'pwa' / 'apple-touch-icon.png'), (180, 180))

    def test_boton_de_instalar_va_oculto_en_el_menu_de_perfil_y_no_en_el_acceso(self):
        self.client.force_login(self.usuario)
        html = self.client.get('/').content.decode()
        self.assertRegex(html, r'data-pwa-install\s+hidden')
        self.assertIn('Instalar AgroAsesor', html)
        self.assertNotIn('data-pwa-install', self.client.get('/login/').content.decode())

    def test_hojas_y_script_cargan_con_version_para_que_se_actualicen(self):
        html = self.client.get('/login/').content.decode()
        self.assertRegex(html, r'css/pwa\.css\?v=\d+')
        self.assertRegex(html, r'js/pwa\.js\?v=\d+')


class AutenticacionIntactaTests(TestCase):
    """La instalación no cambia ni una ruta, token ni protección de las que ya funcionan."""

    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_user('ana@example.com', email='ana@example.com', password=PASSWORD)

    def test_rutas_de_acceso_siguen_igual(self):
        self.assertEqual(reverse('login'), '/login/')
        self.assertEqual(reverse('logout'), '/logout/')
        self.assertEqual(reverse('google_iniciar'), '/cuenta/google/')
        self.assertEqual(reverse('google_callback'), '/cuenta/google/retorno/')
        self.assertEqual(reverse('password_reset'), '/cuenta/recuperar/')
        self.assertEqual(reverse('password_reset_done'), '/cuenta/enviado/')
        self.assertEqual(reverse('password_reset_complete'), '/cuenta/restablecida/')

    def test_csrf_sigue_exigido_en_inicio_y_cierre_de_sesion(self):
        from django.test import Client

        # La vista de fallo CSRF del proyecto (core.auth.fallo_csrf) devuelve al acceso sin ejecutar la acción.
        cliente = Client(enforce_csrf_checks=True)
        cliente.post('/login/', {'email': 'ana@example.com', 'password': PASSWORD})
        self.assertNotIn('_auth_user_id', cliente.session)
        cliente.force_login(self.usuario)
        cliente.post('/logout/')
        self.assertIn('_auth_user_id', cliente.session)  # sin token CSRF el cierre de sesión no se ejecuta

    def test_iniciar_y_cerrar_sesion_siguen_funcionando(self):
        self.assertEqual(self.client.get('/').status_code, 302)  # sin sesión → acceso
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.client.post('/logout/')
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_las_paginas_de_la_cuenta_no_se_vuelven_cacheables_por_la_instalacion(self):
        self.client.force_login(self.usuario)
        for url in ('/', '/predios/'):
            respuesta = self.client.get(url)
            self.assertNotIn('public', respuesta.get('Cache-Control', ''), url)

    def test_el_script_de_instalacion_no_guarda_datos_personales(self):
        codigo = (ESTATICOS / 'js' / 'pwa.js').read_text(encoding='utf-8')
        for prohibido in ('document.cookie', 'indexedDB', 'sessionStorage', 'csrf', 'password', 'token'):
            self.assertNotIn(prohibido, codigo)
        self.assertEqual(codigo.count('localStorage.setItem'), 1)  # solo la fecha en que se cerró el aviso
