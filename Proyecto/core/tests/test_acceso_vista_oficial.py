"""Módulo de acceso (login, registro, recuperar y restablecer contraseña) según el diseño de referencia.

Verifican la composición (panel agrícola + formulario), los textos de cada pantalla, que los parámetros de los
formularios (nombres de campos, token CSRF, rutas y comportamiento) siguen intactos, las validaciones y que el CSS
usa la identidad de AgroAsesor (verde y Outfit) en todas las pantallas.
"""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

User = get_user_model()
PASSWORD = 'Campo-Seguro-2026!'
MAIL = {'default': {'BACKEND': 'django.core.mail.backends.locmem.EmailBackend'}}
CSS = Path(__file__).resolve().parent.parent / 'static' / 'css' / 'login.css'
HEADLINE_LOGIN = 'Optimiza tus decisiones agrícolas con datos reales de tu campo.'
HEADLINE_REGISTRO = 'Únete a la red de agricultores líderes de la provincia de Melipilla.'
HEADLINE_RECUPERAR = 'Protegemos la seguridad de tus datos y la administración de tus cultivos.'
HEADLINE_RESTABLECER = 'Establece un acceso seguro y mantén el control de tus campos.'
PANTALLAS = (
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


def registro(**valores):
    datos = {
        'mode': 'register',
        'nombre_completo': 'Rosa Pérez',
        'email': 'rosa@example.com',
        'telefono': '+56 9 8765 4321',
        'comuna': 'Melipilla',
        'password1': PASSWORD,
        'password2': PASSWORD,
        'acepta_politicas': 'on',
    }
    datos.update(valores)
    return datos


class LoginVistaOficialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('agricultora', email='ana@example.com', password=PASSWORD)

    def test_login_muestra_los_textos_y_el_orden_del_diseno(self):
        r = self.client.get('/login/')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        orden = (
            'class="access-visual"',
            '<strong>AgroAsesor</strong>',
            'Gestión agrícola inteligente',
            HEADLINE_LOGIN,
            '<span>Melipilla, Chile</span>',
            '<span>Módulo de Acceso v2.1</span>',
            '<h1>Iniciar Sesión</h1>',
            'Ingresa tus credenciales profesionales para acceder a la plataforma agrícola.',
            '<span>Correo electrónico</span>',
            '<span>Contraseña</span>',
            'data-eye="password"',
            '¿Olvidaste tu contraseña?',
            'Ingresar</button>',
            '¿No tienes cuenta?',
            'Regístrate aquí',
        )
        posiciones = [html.index(texto) for texto in orden]
        self.assertEqual(posiciones, sorted(posiciones))
        self.assertContains(r, 'placeholder="ejemplo@agroasesor.cl"')

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
        self.assertIn('href="/cuenta/recuperar/"', html)
        self.assertIn('href="?mode=register"', html)
        self.assertIn('js/acceso.js', html)  # ojo para mostrar u ocultar la contraseña

    def test_login_con_correo_y_con_usuario_redirige_al_inicio(self):
        for identificador in ('ana@example.com', 'agricultora'):
            c = Client()
            r = c.post('/login/', {'mode': 'login', 'username': identificador, 'password': PASSWORD})
            self.assertRedirects(r, '/', fetch_redirect_response=False)
            self.assertEqual(int(c.session['_auth_user_id']), self.user.pk)
            self.assertEqual(c.get('/').status_code, 200)

    def test_login_incorrecto_muestra_el_error_y_conserva_lo_escrito(self):
        r = self.client.post('/login/', {'mode': 'login', 'username': 'ana@example.com', 'password': 'mala'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Usuario, correo electrónico o contraseña incorrectos.')
        self.assertContains(r, 'role="alert"')
        self.assertContains(r, 'value="ana@example.com"')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_cerrar_sesion_vuelve_al_login(self):
        self.client.force_login(self.user)
        r = self.client.post('/logout/')
        self.assertRedirects(r, '/login/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get('/').status_code, 302)  # sin sesión ya no entra al panel

    def test_usuario_con_sesion_que_abre_el_login_va_al_inicio(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get('/login/'), '/', fetch_redirect_response=False)


class RegistroVistaOficialTests(TestCase):
    def test_registro_muestra_el_diseno_con_todos_sus_campos_en_orden(self):
        r = self.client.get('/login/?mode=register')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        orden = (
            HEADLINE_REGISTRO,
            '<h1>Crear Cuenta</h1>',
            'Formulario exclusivo para registro de agricultores y productores locales.',
            'name="nombre_completo"',
            'name="telefono"',
            'name="email"',
            'name="comuna"',
            'name="password1"',
            'name="password2"',
            'name="acepta_politicas"',
            'Registrarse</button>',
            '¿Ya tienes cuenta?',
        )
        posiciones = [html.index(texto) for texto in orden]
        self.assertEqual(posiciones, sorted(posiciones))
        self.assertContains(r, '<input type="hidden" name="mode" value="register">', html=True)
        self.assertContains(r, 'name="csrfmiddlewaretoken"')
        self.assertContains(r, f'href="{reverse("politica_privacidad")}"')
        self.assertContains(r, 'data-eye="id_password1"')
        self.assertContains(r, 'data-eye="id_password2"')
        self.assertContains(r, 'href="?mode=login"')

    def test_registro_crea_un_agricultor_y_entra_al_inicio(self):
        r = self.client.post('/login/', registro())
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        user = User.objects.get(email='rosa@example.com')
        self.assertFalse(user.is_staff)  # el registro público nunca crea administradores
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.agricultor.comuna, 'Melipilla')
        self.assertTrue(user.aceptaciones_politicas.exists())

    def test_registro_ya_no_pide_ni_guarda_rut(self):
        """El RUT se eliminó del registro y de la base: ni se muestra ni se guarda aunque alguien lo envíe."""
        pagina = self.client.get('/login/?mode=register')
        self.assertNotContains(pagina, 'name="rut"')
        self.assertNotContains(pagina, 'RUT (opcional)')
        r = self.client.post('/login/', registro(rut='12.345.678-5'))  # un campo desconocido se ignora
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        ficha = User.objects.get(email='rosa@example.com').agricultor
        self.assertNotIn('rut', [campo.name for campo in ficha._meta.get_fields()])

    def test_contrasenas_distintas_o_debiles_se_rechazan(self):
        distinta = self.client.post('/login/', registro(password2='Otra-Clave-2026!'))
        self.assertContains(distinta, 'Las contraseñas no coinciden.')
        debil = self.client.post('/login/', registro(password1='12345678', password2='12345678'))
        self.assertContains(debil, 'class="errorlist"')
        self.assertFalse(User.objects.filter(email='rosa@example.com').exists())

    def test_sin_aceptar_la_politica_no_se_crea_la_cuenta(self):
        r = self.client.post('/login/', registro(acepta_politicas=''))
        self.assertContains(r, 'Debes leer y aceptar la Política de privacidad')
        self.assertFalse(User.objects.filter(email='rosa@example.com').exists())


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
            HEADLINE_RECUPERAR,
            'Módulo de Acceso v2.1',
            'access-shell--recuperar',
        ):
            self.assertContains(r, texto)
        self.assertContains(r, 'href="/login/"')
        self.assertContains(r, '<path d="m12 19-7-7 7-7" />')  # flecha ← del enlace «Volver»

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
    def test_flujo_completo_recuperar_y_restablecer(self):
        r = self.client.post('/cuenta/recuperar/', {'email': self.user.email}, follow=True)
        self.assertEqual(r.redirect_chain[-1][0], '/cuenta/enviado/')
        for texto in (
            '<h1>Solicitud Recibida</h1>',
            'class="access-status"',
            'te enviamos las instrucciones para restablecer tu contraseña. Revisa tu bandeja de entrada y la carpeta de spam.',
            '<a class="submit" href="/login/">Volver al inicio de sesión</a>',
            HEADLINE_RECUPERAR,
        ):
            self.assertContains(r, texto)
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r'http://testserver(/cuenta/restablecer/\S+)', mail.outbox[0].body).group(1)

        paso = self.client.get(url)  # Django guarda el token en sesión y redirige a /set-password/
        self.assertEqual(paso.status_code, 302)
        formulario = self.client.get(paso.url)
        for texto in (
            '<h1>Restablecer Contraseña</h1>',
            'Por favor, escribe una nueva contraseña que sea segura y difícil de adivinar',
            HEADLINE_RESTABLECER,
            '<span>Nueva contraseña</span>',
            '<span>Confirmar nueva contraseña</span>',
            'name="new_password1"',
            'name="new_password2"',
            'data-eye="id_new_password1"',
            'data-eye="id_new_password2"',
            'Guardar nueva contraseña</button>',
            'class="field-help"',
            'Volver al inicio de sesión',
            'name="csrfmiddlewaretoken"',
        ):
            self.assertContains(formulario, texto)

        nueva = 'Otra-Clave-Segura-2027!'
        hecho = self.client.post(paso.url, {'new_password1': nueva, 'new_password2': nueva}, follow=True)
        self.assertEqual(hecho.redirect_chain[-1][0], '/cuenta/restablecida/')
        for texto in ('<h1>Contraseña Actualizada</h1>', 'class="access-status"', 'Tu nueva contraseña quedó guardada.'):
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
        self.assertContains(r, 'css/login.css?v=9')


class ComposicionComunTests(TestCase):
    def test_todas_las_pantallas_comparten_panel_tipografia_y_estilos(self):
        for url in PANTALLAS:
            html = self.client.get(url).content.decode()
            self.assertIn('<body class="access-body">', html, url)
            self.assertIn('class="access-shell', html, url)
            self.assertIn('<section class="access-visual" aria-hidden="true">', html, url)
            self.assertIn('class="access-logo"', html, url)
            self.assertIn('Gestión agrícola inteligente', html, url)
            self.assertIn('<span>Módulo de Acceso v2.1</span>', html, url)
            self.assertIn('class="access-panel"', html, url)
            self.assertIn('css/tema-figma.css', html, url)
            self.assertIn('css/login.css?v=9', html, url)
            self.assertLess(html.index('css/tema-figma.css'), html.index('css/login.css'), url)
            self.assertRegex(html, r'(?s)<link href="[^"]*(outfit\.css|family=Outfit)[^"]*"')
            self.assertNotRegex(html, r'family=Inter|inter\.css', url)  # una sola tipografía

    def test_rutas_actuales_siguen_resolviendo(self):
        for nombre, url in (
            ('login', '/login/'),
            ('logout', '/logout/'),
            ('password_reset', '/cuenta/recuperar/'),
            ('password_reset_done', '/cuenta/enviado/'),
            ('password_reset_complete', '/cuenta/restablecida/'),
            ('politica_privacidad', '/politicas/privacidad-y-cookies/'),
        ):
            self.assertEqual(reverse(nombre), url)


class DisenoAccesoCssTests(TestCase):
    """El CSS reproduce la composición de referencia con la identidad verde de AgroAsesor."""

    def test_paleta_y_tipografia_salen_de_las_variables_globales(self):
        raiz = css_rule(':root')
        for variable, valor in (
            ('--acc-green', 'var(--green, #0b6b4b)'),
            ('--acc-ink', 'var(--ink, #0f1b2d)'),
            ('--acc-muted', 'var(--muted, #64748b)'),
            ('--acc-line', 'var(--line, #e6eaee)'),
            ('--acc-bg', 'var(--bg, #f6f8f7)'),
        ):
            self.assertIn(f'{variable}: {valor};', raiz)
        self.assertIn('--acc-font: var(--font-sans)', raiz)  # Outfit, definida en tema-figma.css
        self.assertIn('font-family: var(--acc-font);', css_rule('.access-body'))
        css = css_text().casefold()
        for naranjo in ('orange', '#c26217', '#a85413', '194, 98, 23'):
            self.assertNotIn(naranjo, css)

    def test_tarjeta_en_dos_columnas_41_59_sobre_fondo_claro(self):
        self.assertIn('background: var(--acc-bg);', css_rule('.access-body'))
        shell = css_rule('.access-shell')
        self.assertIn('minmax(0, 41fr) minmax(0, 59fr)', shell)
        self.assertIn('margin: auto;', shell)
        self.assertIn('border-radius: 16px;', shell)
        self.assertIn('box-shadow', shell)
        self.assertIn('background: #fff;', css_rule('.access-panel'))

    def test_panel_izquierdo_con_foto_agricola_y_filtro_oscuro(self):
        visual = css_rule('.access-visual')
        self.assertIn('url("../img/login-campo.png")', visual)
        self.assertIn('linear-gradient(180deg, rgba(20, 30, 20, 0.62)', visual)
        self.assertIn('justify-content: space-between;', visual)
        self.assertTrue(finders.find('img/login-campo.png'))
        self.assertIn('background: var(--acc-green);', css_rule('.access-logo'))

    def test_controles_en_verde(self):
        self.assertIn('background: var(--acc-green);', css_rule('.submit'))
        self.assertIn('color: var(--acc-green);', css_rule('.forgot'))
        self.assertIn('color: var(--acc-green);', css_rule('.switch a'))
        self.assertIn('border-color: var(--acc-green);', css_rule('.field input:focus,\n.field select:focus'))
        self.assertIn('height: 44px;', css_rule('.field input,\n.field select'))
        self.assertIn('height: 48px;', css_rule('.submit'))
        self.assertIn('font-size: 34px;', css_rule('.access-panel h1'))

    def test_responsive_para_notebook_tablet_y_movil(self):
        css = css_text()
        self.assertIn('@media (max-width: 1200px)', css)
        tablet = css[css.index('@media (max-width: 960px)') : css.index('@media (max-width: 560px)')]
        self.assertIn('grid-template-columns: minmax(0, 1fr);', tablet)  # el panel agrícola pasa arriba
        self.assertIn('min-height: 0;', tablet)
        movil = css[css.index('@media (max-width: 560px)') :]
        self.assertIn('grid-template-columns: 1fr;', movil)  # campos dobles apilados
        self.assertIn('display: none;', movil)  # en el teléfono se ocultan frase y pie del panel
