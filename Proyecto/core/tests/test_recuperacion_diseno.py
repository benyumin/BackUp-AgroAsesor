"""Rediseño de la recuperación de contraseña: mismas pantallas y rutas, con el lenguaje visual del inicio de sesión.

Se comprueba lo que el rediseño no debe romper (rutas, CSRF, campos, ojo de contraseña, validaciones, enlace del correo
y su versión de texto plano) y lo nuevo (ícono por pantalla, recuadro de ayuda, enlace inválido con salida, correo HTML).
Todo con SQLite temporal y correo en memoria: no se envía nada ni se toca Supabase.
"""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail
from django.template import loader
from django.test import SimpleTestCase, TestCase, override_settings

User = get_user_model()
PASSWORD = 'Campo-Seguro-2026!'
NUEVA = 'Otra-Clave-Segura-2027!'
LOCMEM = {'default': {'BACKEND': 'django.core.mail.backends.locmem.EmailBackend'}}
REMITENTE = 'AgroAsesor <no-responder@agroasesor.example>'
RAIZ = Path(__file__).resolve().parent.parent
CSS = RAIZ / 'static' / 'css' / 'login.css'
PANTALLAS = {
    'recuperar': '/cuenta/recuperar/',
    'enviado': '/cuenta/enviado/',
    'restablecida': '/cuenta/restablecida/',
    'invalido': '/cuenta/restablecer/xx/token-falso/',
}


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('ana@example.com', email='ana@example.com', password=PASSWORD)

    def enlace_del_correo(self):
        self.client.post('/cuenta/recuperar/', {'email': self.user.email})
        return re.search(r'https?://[^/\s]+(/cuenta/restablecer/\S+)', mail.outbox[0].body).group(1)

    def pantalla_nueva_contrasena(self):
        paso = self.client.get(self.enlace_del_correo())  # Django guarda el token en la sesión y redirige
        self.assertEqual(paso.status_code, 302)
        return paso.url, self.client.get(paso.url)


class MismoLenguajeVisualQueElLoginTests(Base):
    def test_las_cinco_pantallas_usan_el_mismo_esqueleto_hojas_y_script_que_el_login(self):
        with override_settings(MAILERS=LOCMEM):
            _, nueva = self.pantalla_nueva_contrasena()
        paginas = {nombre: self.client.get(url) for nombre, url in PANTALLAS.items()}
        paginas['nueva'] = nueva
        login = self.client.get('/login/').content.decode()
        for nombre, respuesta in paginas.items():
            with self.subTest(nombre):
                self.assertEqual(respuesta.status_code, 200)
                html = respuesta.content.decode()
                for marca in ('access-body', 'access-shell', 'access-visual', 'access-panel', 'AgroAsesor',
                              'Gestión agrícola inteligente', 'css/tema-figma.css', 'css/login.css', 'js/acceso.js'):
                    self.assertIn(marca, html)
                    self.assertIn(marca, login) if marca.startswith(('css/', 'js/', 'access-')) else None
                # misma versión de la hoja de estilos que el login (así el navegador no mezcla copias)
                self.assertEqual(re.findall(r'login\.css\?v=\d+', html), re.findall(r'login\.css\?v=\d+', login))

    def test_cada_pantalla_tiene_su_icono_y_ninguna_inventa_elementos_nuevos(self):
        with override_settings(MAILERS=LOCMEM):
            _, nueva = self.pantalla_nueva_contrasena()
        paginas = {nombre: self.client.get(url) for nombre, url in PANTALLAS.items()}
        paginas['nueva'] = nueva
        for nombre, respuesta in paginas.items():
            with self.subTest(nombre):
                html = respuesta.content.decode()
                self.assertEqual(html.count('class="access-status'), 1, nombre)
                self.assertNotIn('recovery-', html)  # nada del borrador: pasos «01 02 03», «✦», etc.
                self.assertNotIn('✦', html)
        self.assertIn('access-status--alerta', paginas['invalido'].content.decode())
        self.assertNotIn('access-status--alerta', paginas['recuperar'].content.decode())

    def test_inicio_de_sesion_y_registro_no_cambiaron(self):
        login = self.client.get('/login/').content.decode()
        for texto in ('<h1>Iniciar Sesión</h1>', 'data-eye="password"', '¿Olvidaste tu contraseña?',
                      'href="/cuenta/recuperar/"', 'name="username"', 'csrfmiddlewaretoken'):
            self.assertIn(texto, login)
        self.assertNotIn('access-status', login)
        self.assertNotIn('access-tip', login)
        registro = self.client.get('/login/?mode=register').content.decode()
        for texto in ('<h1>Crear Cuenta</h1>', 'data-eye="id_password1"', 'data-eye="id_password2"', 'Registrarse'):
            self.assertIn(texto, registro)


class PantallasTests(Base):
    def test_recuperar_conserva_ruta_campo_csrf_y_validacion(self):
        r = self.client.get('/cuenta/recuperar/')
        self.assertContains(r, '<h1>Recuperar Contraseña</h1>')
        self.assertContains(r, 'name="csrfmiddlewaretoken"')
        self.assertContains(r, 'name="email"')
        self.assertContains(r, 'Enviar instrucciones')
        self.assertContains(r, 'href="/login/"')
        malo = self.client.post('/cuenta/recuperar/', {'email': 'no-es-correo'})
        self.assertEqual(malo.status_code, 200)
        self.assertContains(malo, 'errorlist')
        self.assertContains(malo, 'access-shell')  # el error no rompe el diseño

    @override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE)
    def test_solicitud_recibida_tiene_recuadro_de_ayuda_y_mensaje_neutro(self):
        existente = self.client.post('/cuenta/recuperar/', {'email': self.user.email}, follow=True)
        inexistente = self.client.post('/cuenta/recuperar/', {'email': 'nadie@example.com'}, follow=True)
        for r in (existente, inexistente):
            self.assertEqual(r.redirect_chain[-1][0], '/cuenta/enviado/')
            self.assertContains(r, 'Si existe una cuenta activa con ese correo')
            self.assertContains(r, 'class="access-tip"')
            self.assertContains(r, 'carpeta de spam o correo no deseado')
            self.assertContains(r, 'href="/cuenta/recuperar/"')  # «Intentar de nuevo»
            self.assertContains(r, 'href="/login/"')
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE)
    def test_nueva_contrasena_tiene_dos_campos_con_ojo_que_apuntan_a_inputs_reales(self):
        _, r = self.pantalla_nueva_contrasena()
        html = r.content.decode()
        self.assertIn('<h1>Restablecer Contraseña</h1>', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        for campo in ('id_new_password1', 'id_new_password2'):
            self.assertIn(f'data-eye="{campo}"', html)  # el botón ojo apunta al input
            self.assertRegex(html, rf'<input[^>]+id="{campo}"')
            self.assertRegex(html, rf'<input[^>]+id="{campo}"[^>]*type="password"|<input[^>]+type="password"[^>]+id="{campo}"')
        self.assertEqual(html.count('aria-label="Mostrar contraseña"'), 2)
        self.assertIn('Guardar nueva contraseña', html)

    @override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE)
    def test_errores_de_validacion_se_ven_dentro_del_mismo_diseno(self):
        destino, _ = self.pantalla_nueva_contrasena()
        distintas = self.client.post(destino, {'new_password1': NUEVA, 'new_password2': 'Otra-Distinta-9999'})
        self.assertEqual(distintas.status_code, 200)
        self.assertContains(distintas, 'errorlist')
        self.assertContains(distintas, 'access-shell')
        self.assertContains(distintas, 'data-eye="id_new_password1"')
        corta = self.client.post(destino, {'new_password1': '123', 'new_password2': '123'})
        self.assertContains(corta, 'errorlist')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))  # ninguna de las dos cambió la clave

    def test_enlace_invalido_no_deja_la_pagina_vacia_y_ofrece_pedir_otro(self):
        r = self.client.get(PANTALLAS['invalido'])
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn('<title>Enlace no disponible · AgroAsesor</title>', html)
        self.assertIn('<h1>Enlace No Disponible</h1>', html)
        self.assertIn('El enlace es inválido o venció.', html)
        self.assertIn('href="/cuenta/recuperar/">Solicitar un nuevo enlace</a>', html)
        self.assertIn('href="/login/"', html)
        self.assertNotIn('<form', html)

    @override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE)
    def test_enlace_ya_usado_cae_en_la_pantalla_de_enlace_no_disponible(self):
        url = self.enlace_del_correo()
        paso = self.client.get(url)
        hecho = self.client.post(paso.url, {'new_password1': NUEVA, 'new_password2': NUEVA}, follow=True)
        self.assertEqual(hecho.redirect_chain[-1][0], '/cuenta/restablecida/')
        self.assertContains(hecho, '<h1>Contraseña Actualizada</h1>')
        self.assertContains(hecho, 'href="/login/"')
        self.assertContains(self.client.get(url), 'Solicitar un nuevo enlace')


@override_settings(MAILERS=LOCMEM, DEFAULT_FROM_EMAIL=REMITENTE, SITE_URL='https://agroasesor.example')
class CorreoHtmlTests(Base):
    def enviar(self):
        self.client.post('/cuenta/recuperar/', {'email': self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        return mail.outbox[0]

    def test_el_correo_es_multipart_con_texto_plano_y_html(self):
        mensaje = self.enviar()
        self.assertEqual(mensaje.subject, 'Restablecer contraseña de AgroAsesor')
        self.assertEqual(mensaje.to, [self.user.email])
        self.assertEqual(mensaje.from_email, REMITENTE)
        self.assertIn('Solicitaste restablecer tu contraseña de AgroAsesor.', mensaje.body)  # texto plano intacto
        self.assertNotIn('<', mensaje.body)
        self.assertEqual([tipo for _, tipo in [(a.content, a.mimetype) for a in mensaje.alternatives]], ['text/html'])

    def test_el_html_lleva_el_mismo_enlace_seguro_de_django_que_el_texto(self):
        mensaje = self.enviar()
        html = mensaje.alternatives[0].content
        enlace_texto = re.search(r'https://agroasesor\.example/cuenta/restablecer/\S+', mensaje.body).group(0)
        self.assertTrue(enlace_texto.endswith('/'))
        self.assertEqual(html.count(enlace_texto), 3)  # botón, enlace alternativo y su texto visible
        self.assertIn(f'href="{enlace_texto}"', html)
        # el token del enlace es válido de verdad: abrirlo lleva al formulario de nueva contraseña
        paso = self.client.get(re.sub(r'^https://agroasesor\.example', '', enlace_texto))
        self.assertEqual(paso.status_code, 302)
        self.assertContains(self.client.get(paso.url), 'name="new_password2"')

    def test_el_html_usa_la_plantilla_propia_y_no_la_del_admin_de_django(self):
        plantilla = loader.get_template('registration/agro_reset_email.html')
        self.assertTrue(Path(plantilla.origin.name).is_relative_to(RAIZ / 'templates'))
        html = self.enviar().alternatives[0].content
        self.assertIn('Restablecer contraseña', html)
        self.assertIn('AgroAsesor', html)
        self.assertIn('Si no solicitaste este cambio', html)
        self.assertNotIn('Ha recibido este correo electrónico', html)  # texto de la plantilla del admin

    def test_el_html_es_https_con_los_colores_del_tema_y_sin_adjuntos(self):
        mensaje = self.enviar()
        html = mensaje.alternatives[0].content
        self.assertNotRegex(html, r'(?i)(src|href)="http://')
        self.assertIn('https://agroasesor.example/static/img/email-logo.png', html)
        for color in ('#0b6b4b', '#0f1b2d', '#64748b', '#e6eaee', '#e6f7ef'):
            self.assertIn(color, html)
        self.assertEqual(mensaje.attachments, [])  # el logo se enlaza, no se adjunta
        self.assertIn('width="600"', html)
        self.assertIn('viewport', html)

    def test_el_nombre_de_la_persona_se_escapa(self):
        User.objects.filter(pk=self.user.pk).update(first_name='<script>alert(1)</script>')
        html = self.enviar().alternatives[0].content
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_sin_nombre_el_saludo_sigue_siendo_natural(self):
        html = self.enviar().alternatives[0].content
        self.assertRegex(html, r'Hola\. Recibimos una solicitud')

    def test_el_logo_del_correo_existe_y_es_pequeno(self):
        logo = RAIZ / 'static' / 'img' / 'email-logo.png'
        datos = logo.read_bytes()
        self.assertEqual(datos[:8], b'\x89PNG\r\n\x1a\n')
        self.assertLess(len(datos), 20_000)


class HojaDeEstilosTests(SimpleTestCase):
    def test_los_estilos_nuevos_reutilizan_variables_del_tema(self):
        css = CSS.read_text(encoding='utf-8').replace('\r\n', '\n')
        for selector in ('.access-tip', '.access-status--alerta'):
            self.assertIn(selector, css)
        bloque = css[css.index('.access-status--alerta'):css.index('/* Política de privacidad */')]
        self.assertNotRegex(bloque, r'font-weight:\s*(\d+|bold)\b')  # solo var(--fw-*)
        self.assertNotRegex(bloque, r'font-family')
        self.assertIn('var(--acc-green-soft)', bloque)
        self.assertIn('var(--acc-danger)', bloque)

    def test_la_version_del_css_en_la_plantilla_es_la_actual(self):
        cabecera = (RAIZ / 'templates' / 'partials' / 'access_head.html').read_text(encoding='utf-8')
        self.assertIn("css/login.css' %}?v=10", cabecera)
