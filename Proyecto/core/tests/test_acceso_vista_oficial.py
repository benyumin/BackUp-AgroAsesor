"""Acceso de AgroAsesor (login, registro, recuperar contraseña y política): tarjeta centrada con la identidad verde.

Verifican el orden y los textos de la pantalla, que los parámetros del formulario (nombres de campos, token CSRF,
rutas y comportamiento) siguen intactos, que «Recordarme» funciona de verdad, que el botón de Google solo aparece
si django-allauth está configurado y que el CSS usa la paleta global (sin los naranjos anteriores).
"""

import re
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

User = get_user_model()
PASSWORD = 'Campo-Seguro-2026!'
MAIL = {'default': {'BACKEND': 'django.core.mail.backends.locmem.EmailBackend'}}
CSS = Path(__file__).resolve().parent.parent / 'static' / 'css' / 'login.css'
PAGINAS_ACCESO = (
    '/login/',
    '/login/?mode=register',
    '/cuenta/recuperar/',
    '/cuenta/enviado/',
    '/cuenta/restablecida/',
    '/cuenta/restablecer/xx/token-falso/',  # enlace inválido
)


def css_text():
    return CSS.read_text(encoding='utf-8').replace('\r\n', '\n')


def css_rule(selector):
    """Cuerpo de la regla CSS cuyo selector es exactamente ``selector`` (primera aparición)."""
    coincidencia = re.search(r'(?m)^' + re.escape(selector) + r' \{\n(.*?)\n\}', css_text(), re.S)
    assert coincidencia, f'no existe la regla {selector}'
    return coincidencia.group(1)


class LoginVistaOficialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('agricultora', email='ana@example.com', password=PASSWORD)

    def test_login_muestra_los_elementos_en_el_orden_pedido(self):
        r = self.client.get('/login/')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        orden = (
            'class="access-logo"',
            '<strong>AgroAsesor</strong>',
            '>Ingresar</a>',
            '>Crear cuenta</a>',
            '¡Bienvenido de nuevo!</h1>',
            'Ingresa a tu cuenta para gestionar tus predios y actividades agrícolas.',
            '<span>Correo electrónico</span>',
            '<span>Contraseña</span>',
            'data-eye="password"',
            'Recordarme',
            '¿Olvidaste tu contraseña?',
            '<button class="submit" type="submit">',
            '¿No tienes cuenta?',
        )
        posiciones = [html.index(texto) for texto in orden]
        self.assertEqual(posiciones, sorted(posiciones))
        self.assertContains(r, 'placeholder="ejemplo@agroasesor.cl"')
        self.assertRegex(html, r'<button class="submit" type="submit">\s*Iniciar sesión\s*</button>')

    def test_pestanas_cambian_entre_ingresar_y_crear_cuenta(self):
        login = self.client.get('/login/').content.decode()
        registro = self.client.get('/login/?mode=register').content.decode()
        self.assertRegex(login, r'class="access-tab is-active"\s+href="\?mode=login"\s+aria-current="page"')
        self.assertRegex(registro, r'class="access-tab is-active"\s+href="\?mode=register"\s+aria-current="page"')
        for html in (login, registro):
            self.assertIn('class="access-tabs"', html)

    def test_login_conserva_los_parametros_del_formulario(self):
        html = self.client.get('/login/').content.decode()
        self.assertIn('<form method="post" class="access-form">', html)
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn('<input type="hidden" name="mode" value="login">', html)
        usuario = re.search(r'<input[^>]*id="username"[^>]*>', html, re.S).group(0)
        for atributo in ('name="username"', 'type="text"', 'required', 'autocomplete="username"'):
            self.assertIn(atributo, usuario)  # sigue aceptando usuario O correo (type="text", no "email")
        clave = re.search(r'<input[^>]*id="password"[^>]*>', html, re.S).group(0)
        for atributo in ('name="password"', 'type="password"', 'required', 'autocomplete="current-password"'):
            self.assertIn(atributo, clave)
        recordar = re.search(r'<input[^>]*id="recordarme"[^>]*>', html, re.S).group(0)
        for atributo in ('name="recordarme"', 'type="checkbox"', 'checked'):
            self.assertIn(atributo, recordar)
        self.assertIn('href="/cuenta/recuperar/"', html)
        self.assertIn('href="?mode=register"', html)

    def test_login_sigue_funcionando_con_correo_y_con_usuario(self):
        for identificador in ('ana@example.com', 'agricultora'):
            c = Client()
            r = c.post(
                '/login/', {'mode': 'login', 'username': identificador, 'password': PASSWORD, 'recordarme': '1'}
            )
            self.assertRedirects(r, '/', fetch_redirect_response=False)
            self.assertEqual(int(c.session['_auth_user_id']), self.user.pk)

    def test_recordarme_mantiene_la_sesion_y_sin_marcar_termina_al_cerrar_el_navegador(self):
        recordado = Client()
        recordado.post('/login/', {'mode': 'login', 'username': 'ana@example.com', 'password': PASSWORD, 'recordarme': '1'})
        self.assertFalse(recordado.session.get_expire_at_browser_close())
        temporal = Client()
        temporal.post('/login/', {'mode': 'login', 'username': 'ana@example.com', 'password': PASSWORD})
        self.assertIn('_auth_user_id', temporal.session)
        self.assertTrue(temporal.session.get_expire_at_browser_close())

    def test_login_incorrecto_muestra_el_error_y_conserva_lo_escrito(self):
        r = self.client.post('/login/', {'mode': 'login', 'username': 'ana@example.com', 'password': 'mala'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Usuario, correo electrónico o contraseña incorrectos.')
        self.assertContains(r, 'role="alert"')
        self.assertContains(r, 'value="ana@example.com"')
        self.assertNotIn('_auth_user_id', self.client.session)
        recordar = re.search(r'<input[^>]*id="recordarme"[^>]*>', r.content.decode(), re.S).group(0)
        self.assertNotIn('checked', recordar)  # se respeta que lo dejó sin marcar

    def test_registro_usa_la_misma_vista_con_todos_sus_campos(self):
        r = self.client.get('/login/?mode=register')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Crea tu cuenta</h1>')
        self.assertContains(r, 'access-shell--registro')
        self.assertContains(r, 'class="access-logo"')
        for campo in (
            'nombre_completo',
            'email',
            'telefono',
            'comuna',
            'rut',
            'password1',
            'password2',
            'acepta_politicas',
        ):
            self.assertContains(r, f'name="{campo}"')
        self.assertContains(r, '<input type="hidden" name="mode" value="register">', html=True)
        self.assertContains(r, 'name="csrfmiddlewaretoken"')
        self.assertContains(r, f'href="{reverse("politica_privacidad")}"')
        self.assertContains(r, 'data-eye="id_password1"')
        self.assertContains(r, 'Inicia sesión')
        self.assertContains(r, 'href="?mode=login"')
        self.assertNotContains(r, 'name="recordarme"')

    def test_registro_sigue_creando_la_cuenta(self):
        datos = {
            'mode': 'register',
            'nombre_completo': 'Rosa Pérez',
            'email': 'rosa@example.com',
            'telefono': '+56 9 8765 4321',
            'comuna': 'Melipilla',
            'rut': '',
            'password1': PASSWORD,
            'password2': PASSWORD,
            'acepta_politicas': 'on',
        }
        r = self.client.post('/login/', datos)
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(email='rosa@example.com').exists())

    def test_registro_invalido_muestra_los_errores_y_no_crea_la_cuenta(self):
        datos = {
            'mode': 'register',
            'nombre_completo': 'Rosa Pérez',
            'email': 'no-es-correo',
            'telefono': '+56 9 8765 4321',
            'comuna': 'Melipilla',
            'password1': PASSWORD,
            'password2': 'distinta',
        }
        r = self.client.post('/login/', datos)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="errorlist"')
        self.assertContains(r, 'Crea tu cuenta</h1>')
        self.assertFalse(User.objects.filter(email='no-es-correo').exists())

    def test_sin_allauth_no_hay_boton_de_google(self):
        # django-allauth no está instalado: no se muestra un botón que no funcione.
        for url in ('/login/', '/login/?mode=register'):
            r = self.client.get(url)
            self.assertNotContains(r, 'Continuar con Google', msg_prefix=url)
            self.assertNotContains(r, 'O continúa con', msg_prefix=url)

    def test_con_google_configurado_el_boton_envia_por_post_con_csrf(self):
        with mock.patch('core.views.google_login_url', return_value='/accounts/google/login/'):
            r = self.client.get('/login/')
        html = r.content.decode()
        self.assertIn('O continúa con', html)
        formulario = re.search(r'<form method="post" action="/accounts/google/login/"[^>]*>(.*?)</form>', html, re.S)
        self.assertIsNotNone(formulario)
        self.assertIn('name="csrfmiddlewaretoken"', formulario.group(1))
        self.assertIn('Continuar con Google', formulario.group(1))
        self.assertIn('fill="#4285F4"', formulario.group(1))  # logotipo oficial de Google (4 colores)
        self.assertLess(html.index('<button class="submit"'), html.index('O continúa con'))
        self.assertLess(html.index('Continuar con Google'), html.index('¿No tienes cuenta?'))

    def test_la_ruta_de_google_solo_existe_con_allauth(self):
        from core.views import google_login_url

        self.assertEqual(google_login_url(), '')

    def test_todas_las_pantallas_de_acceso_comparten_la_tarjeta_y_los_estilos(self):
        for url in PAGINAS_ACCESO:
            html = self.client.get(url).content.decode()
            self.assertIn('<body class="access-body">', html, url)
            self.assertIn('class="access-shell', html, url)
            self.assertIn('class="access-panel"', html, url)
            self.assertIn('class="access-logo"', html, url)
            self.assertIn('css/tema-figma.css', html, url)  # variables globales de color de AgroAsesor
            self.assertIn('css/login.css?v=6', html, url)
            self.assertLess(html.index('css/tema-figma.css'), html.index('css/login.css'), url)
            self.assertRegex(html, r'(?s)<link href="[^"]*(outfit\.css|family=Outfit)[^"]*"')
            self.assertNotIn('access-visual', html, url)  # sin el panel de foto anterior
            self.assertIn('AgroAsesor · Melipilla, Chile', html, url)


class RecuperarContrasenaVistaOficialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('farmer@example.com', email='farmer@example.com', password=PASSWORD)

    def test_recuperar_muestra_los_textos_del_diseno(self):
        r = self.client.get('/cuenta/recuperar/')
        self.assertEqual(r.status_code, 200)
        for texto in (
            '<h1>Recuperar Contraseña</h1>',
            'Ingresa tu correo electrónico y te enviaremos las instrucciones para restablecer tu contraseña.',
            '<span>Correo electrónico</span>',
            'placeholder="ejemplo@agroasesor.cl"',
            'Enviar instrucciones</button>',
            'Volver al inicio de sesión',
            'Melipilla, Chile',
            'access-shell--recuperar',
        ):
            self.assertContains(r, texto)
        self.assertContains(r, 'href="/login/"')
        self.assertContains(r, '<path d="m12 19-7-7 7-7" />')  # flecha ← del enlace «Volver»
        self.assertNotContains(r, 'Módulo de Acceso')

    def test_recuperar_conserva_el_campo_y_el_token(self):
        html = self.client.get('/cuenta/recuperar/').content.decode()
        self.assertIn('name="csrfmiddlewaretoken"', html)
        correo = re.search(r'<input[^>]*name="email"[^>]*>', html, re.S).group(0)
        for atributo in ('type="email"', 'autocomplete="email"', 'maxlength="254"', 'required'):
            self.assertIn(atributo, correo)

    def test_correo_invalido_vuelve_con_error_y_conserva_lo_escrito(self):
        r = self.client.post('/cuenta/recuperar/', {'email': 'no-es-correo'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="errorlist"')
        self.assertContains(r, 'value="no-es-correo"')

    @override_settings(MAILERS=MAIL)
    def test_flujo_completo_con_la_vista_nueva(self):
        r = self.client.post('/cuenta/recuperar/', {'email': self.user.email}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], '/cuenta/enviado/')
        for texto in (
            '<h1>Solicitud Recibida</h1>',
            'class="access-status"',
            'te enviamos las instrucciones para restablecer tu contraseña. Revisa tu bandeja de entrada y la carpeta de spam.',
            '<a class="submit" href="/login/">Volver al inicio de sesión</a>',
        ):
            self.assertContains(r, texto)
        self.assertNotContains(r, 'consola del servidor')
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r'http://testserver(/cuenta/restablecer/\S+)', mail.outbox[0].body).group(1)

        paso = self.client.get(url)  # Django guarda el token en sesión y redirige a /set-password/
        self.assertEqual(paso.status_code, 302)
        formulario = self.client.get(paso.url)
        for texto in (
            '<h1>Restablecer Contraseña</h1>',
            'Elige una contraseña nueva y segura para tu cuenta.',
            'name="new_password1"',
            'name="new_password2"',
            'Guardar contraseña</button>',
            'class="field-help"',
            'Volver al inicio de sesión',
        ):
            self.assertContains(formulario, texto)

        nueva = 'Otra-Clave-Segura-2027!'
        hecho = self.client.post(paso.url, {'new_password1': nueva, 'new_password2': nueva}, follow=True)
        self.assertEqual(hecho.redirect_chain[-1][0], '/cuenta/restablecida/')
        for texto in (
            '<h1>Contraseña Actualizada</h1>',
            'class="access-status"',
            'Tu nueva contraseña quedó guardada.',
            'Iniciar sesión',
        ):
            self.assertContains(hecho, texto)
        self.assertContains(hecho, 'class="submit" href="/login/"')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(nueva))
        entra = Client().post('/login/', {'mode': 'login', 'username': self.user.email, 'password': nueva})
        self.assertRedirects(entra, '/', fetch_redirect_response=False)

    def test_enlace_invalido_muestra_enlace_no_disponible(self):
        r = self.client.get('/cuenta/restablecer/xx/token-falso/')
        self.assertContains(r, '<h1>Enlace No Disponible</h1>')
        self.assertContains(r, 'El enlace es inválido o venció.')
        self.assertContains(r, 'Volver al inicio de sesión')
        self.assertNotContains(r, '<form')

    def test_contrasenas_distintas_muestran_el_error_en_la_pantalla_de_restablecer(self):
        with override_settings(MAILERS=MAIL):
            self.client.post('/cuenta/recuperar/', {'email': self.user.email})
        url = re.search(r'http://testserver(/cuenta/restablecer/\S+)', mail.outbox[0].body).group(1)
        paso = self.client.get(url)
        r = self.client.post(paso.url, {'new_password1': 'Otra-Clave-Segura-2027!', 'new_password2': 'distinta'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="errorlist"')
        self.assertContains(r, '<h1>Restablecer Contraseña</h1>')

    def test_politica_de_privacidad_sigue_usando_la_tarjeta_simple(self):
        r = self.client.get(reverse('politica_privacidad'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'recovery-shell')
        self.assertContains(r, 'policy-content')
        self.assertContains(r, 'css/login.css?v=6')
        self.assertContains(r, 'class="access-logo"')


class DisenoAccesoCssTests(TestCase):
    """El CSS del acceso usa la identidad verde de AgroAsesor y una tarjeta centrada y adaptable."""

    def test_paleta_sale_de_las_variables_globales_y_no_quedan_naranjos(self):
        raiz = css_rule(':root')
        for variable, valor in (
            ('--acc-green', 'var(--green, #0b6b4b)'),
            ('--acc-ink', 'var(--ink, #0f1b2d)'),
            ('--acc-muted', 'var(--muted, #64748b)'),
            ('--acc-line', 'var(--line, #e6eaee)'),
            ('--acc-bg', 'var(--bg, #f6f8f7)'),
        ):
            self.assertIn(f'{variable}: {valor};', raiz)
        self.assertIn('Outfit', raiz)
        css = css_text().casefold()
        for naranjo in ('orange', '#c26217', '#a85413', '194, 98, 23'):
            self.assertNotIn(naranjo, css)
        self.assertNotIn('login-campo', css)  # sin foto de fondo

    def test_tarjeta_centrada_blanca_con_bordes_redondeados_y_sombra(self):
        cuerpo = css_rule('.access-body')
        self.assertIn('display: flex;', cuerpo)
        self.assertIn('min-height: 100vh;', cuerpo)
        self.assertIn('margin: auto;', css_rule('.access-shell'))
        self.assertIn('width: min(100%, 460px);', css_rule('.access-shell'))
        self.assertIn('width: min(100%, 600px);', css_rule('.access-shell--registro'))
        panel = css_rule('.access-panel')
        self.assertIn('background: #fff;', panel)
        self.assertIn('border-radius: 24px;', panel)
        self.assertIn('box-shadow', panel)

    def test_botones_y_campos_en_verde(self):
        self.assertIn('background: var(--acc-green);', css_rule('.submit'))
        self.assertIn('background: var(--acc-green);', css_rule('.access-logo'))
        self.assertIn('color: var(--acc-green);', css_rule('.access-tab.is-active'))
        self.assertIn('border-color: var(--acc-green);', css_rule('.field input:focus,\n.field select:focus'))
        self.assertIn('accent-color: var(--acc-green);', css_rule('.remember input'))
        self.assertIn('color: var(--acc-green);', css_rule('.forgot'))
        self.assertIn('background: #fff;', css_rule('.access-google'))

    def test_movil_apila_los_campos_dobles(self):
        css = css_text()
        movil = css[css.index('@media (max-width: 560px)'):]
        self.assertIn('grid-template-columns: 1fr;', movil)
        self.assertIn('padding: 28px 20px 24px;', movil)
