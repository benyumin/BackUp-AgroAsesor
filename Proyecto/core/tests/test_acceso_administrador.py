"""Etapa 1 del acceso administrativo: un solo login y la pantalla inicial según el rol.

El rol lo decide el servidor con ``core.auth.es_administrador`` (cuenta autenticada, activa e ``is_staff``); nada de
lo que llega en la petición (``next``, campos del formulario) cambia el destino. Se comprueban el inicio de sesión,
la sesión ya iniciada, la protección de las rutas del panel, el cierre de sesión y el menú de cada rol.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, SimpleTestCase, TestCase

from core.auth import destino_inicial, es_administrador
from core.models import Cultivo

User = get_user_model()
PASSWORD = 'Clave-segura-123'


def cuenta(**campos):
    base = {'is_authenticated': True, 'is_active': True, 'is_staff': False, 'is_superuser': False}
    base.update(campos)
    return SimpleNamespace(**base)


class CriterioDeAdministradorTests(SimpleTestCase):
    def test_solo_una_cuenta_activa_con_is_staff_es_administradora(self):
        self.assertTrue(es_administrador(cuenta(is_staff=True)))
        self.assertTrue(es_administrador(cuenta(is_staff=True, is_superuser=True)))
        self.assertFalse(es_administrador(cuenta()))
        self.assertFalse(es_administrador(cuenta(is_staff=True, is_active=False)))
        self.assertFalse(es_administrador(cuenta(is_staff=True, is_authenticated=False)))

    def test_is_superuser_por_si_solo_no_da_acceso_al_panel(self):
        self.assertFalse(es_administrador(cuenta(is_superuser=True)))

    def test_sin_cuenta_o_anonimo_no_es_administrador(self):
        self.assertFalse(es_administrador(None))
        self.assertFalse(es_administrador(AnonymousUser()))

    def test_destino_inicial_segun_el_rol(self):
        self.assertEqual(destino_inicial(cuenta(is_staff=True)), 'admin_panel')
        self.assertEqual(destino_inicial(cuenta()), 'dashboard')
        self.assertEqual(destino_inicial(AnonymousUser()), 'dashboard')


class AccesoPorRolTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            'admin@example.com', 'admin@example.com', PASSWORD, is_staff=True, is_superuser=True
        )
        self.agricultor = User.objects.create_user('ana@example.com', 'ana@example.com', PASSWORD)

    def entrar(self, identificador):
        c = Client()
        r = c.post('/login/', {'mode': 'login', 'username': identificador, 'password': PASSWORD})
        return c, r

    def con_sesion(self, usuario):
        c = Client()
        c.force_login(usuario)
        return c

    # ---- Inicio de sesión ----

    def test_administrador_inicia_sesion_y_llega_al_panel(self):
        for identificador in ('admin@example.com', 'ADMIN@EXAMPLE.COM'):
            with self.subTest(identificador=identificador):
                c, r = self.entrar(identificador)
                self.assertRedirects(r, '/panel-admin/', fetch_redirect_response=False)
                self.assertEqual(c.get('/panel-admin/').status_code, 200)

    def test_agricultor_inicia_sesion_y_llega_al_inicio(self):
        c, r = self.entrar('ana@example.com')
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.assertEqual(c.get('/').status_code, 200)

    def test_el_rol_solo_depende_de_la_cuenta_no_de_lo_que_envia_el_navegador(self):
        c = Client()
        r = c.post(
            '/login/?next=/panel-admin/',
            {
                'mode': 'login',
                'username': 'ana@example.com',
                'password': PASSWORD,
                'next': '/panel-admin/',
                'rol': 'administrador',
                'is_staff': 'on',
            },
        )
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.assertEqual(c.get('/panel-admin/').status_code, 403)
        self.assertFalse(User.objects.get(pk=self.agricultor.pk).is_staff)

    def test_next_hacia_otro_sitio_no_se_sigue(self):
        c = Client()
        r = c.post(
            '/login/?next=https://sitio-malo.example/',
            {'mode': 'login', 'username': 'admin@example.com', 'password': PASSWORD, 'next': 'https://sitio-malo.example/'},
        )
        self.assertRedirects(r, '/panel-admin/', fetch_redirect_response=False)

    def test_cuenta_inactiva_no_entra_aunque_sea_administradora(self):
        User.objects.create_user('baja@example.com', 'baja@example.com', PASSWORD, is_staff=True, is_active=False)
        c, r = self.entrar('baja@example.com')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'incorrectos')
        self.assertNotIn('_auth_user_id', c.session)

    # ---- Sesión ya iniciada, cierre de sesión y actualizar la página ----

    def test_con_la_sesion_iniciada_login_devuelve_a_cada_pantalla(self):
        for usuario, destino in ((self.admin, '/panel-admin/'), (self.agricultor, '/')):
            with self.subTest(usuario=usuario.username):
                c = self.con_sesion(usuario)
                self.assertRedirects(c.get('/login/'), destino, fetch_redirect_response=False)
                self.assertRedirects(c.get('/login/?mode=register'), destino, fetch_redirect_response=False)

    def test_cierre_de_sesion_vuelve_al_login_y_actualizar_conserva_la_sesion(self):
        for usuario, pantalla in ((self.admin, '/panel-admin/'), (self.agricultor, '/')):
            with self.subTest(usuario=usuario.username):
                c = self.con_sesion(usuario)
                self.assertEqual(c.get(pantalla).status_code, 200)
                self.assertEqual(c.get(pantalla).status_code, 200)  # actualizar el navegador no pierde la sesión
                self.assertRedirects(c.post('/logout/'), '/login/', fetch_redirect_response=False)
                r = c.get(pantalla)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r['Location'].startswith('/login/'))

    def test_fallo_de_csrf_con_sesion_lleva_a_la_pantalla_de_cada_rol(self):
        for usuario, destino in ((self.admin, '/panel-admin/'), (self.agricultor, '/')):
            with self.subTest(usuario=usuario.username):
                c = Client(enforce_csrf_checks=True)
                c.force_login(usuario)
                self.assertRedirects(c.post('/logout/'), destino, fetch_redirect_response=False)

    def test_completar_registro_de_google_con_sesion_va_a_la_pantalla_del_rol(self):
        for usuario, destino in ((self.admin, '/panel-admin/'), (self.agricultor, '/')):
            with self.subTest(usuario=usuario.username):
                c = self.con_sesion(usuario)
                self.assertRedirects(c.get('/cuenta/google/completar/'), destino, fetch_redirect_response=False)

    # ---- Protección de las rutas administrativas (en el servidor) ----

    def test_agricultor_no_puede_abrir_ninguna_ruta_del_panel(self):
        c = self.con_sesion(self.agricultor)
        antes = Cultivo.objects.count()
        self.assertEqual(c.get('/panel-admin/').status_code, 403)
        self.assertEqual(c.post('/panel-admin/cultivos-base/').status_code, 403)
        self.assertEqual(c.post('/entrar-admin/').status_code, 403)
        self.assertEqual(c.get('/mapa-prueba/').status_code, 403)
        self.assertEqual(Cultivo.objects.count(), antes)  # la acción protegida no se ejecutó

    def test_agricultor_no_entra_a_django_admin(self):
        r = self.con_sesion(self.agricultor).get('/admin/')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith('/admin/login/'))
        self.assertEqual(self.con_sesion(self.admin).get('/admin/').status_code, 200)

    def test_sin_sesion_las_rutas_del_panel_llevan_al_login(self):
        for url in ('/panel-admin/', '/mapa-prueba/'):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r['Location'].startswith('/login/'))
        r = self.client.post('/panel-admin/cultivos-base/')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith('/login/'))

    def test_administrador_si_puede_usar_las_acciones_del_panel(self):
        c = self.con_sesion(self.admin)
        self.assertRedirects(c.post('/entrar-admin/'), '/panel-admin/', fetch_redirect_response=False)
        self.assertRedirects(c.post('/panel-admin/cultivos-base/'), '/panel-admin/', fetch_redirect_response=False)
        self.assertEqual(c.get('/mapa-prueba/').status_code, 200)

    # ---- Navegación de cada rol ----

    def test_el_administrador_ve_solo_la_navegacion_administrativa(self):
        r = self.con_sesion(self.admin).get('/panel-admin/')
        etiquetas = [item['label'] for item in r.context['menu'] if 'label' in item]
        self.assertEqual(etiquetas, ['Centro de administración', 'Django Admin'])
        self.assertEqual(r.context['user']['role'], 'Administrador')
        self.assertEqual(r.context['home_url'], '/panel-admin/')  # el logotipo lleva al panel, no al inicio del agricultor
        self.assertContains(r, 'href="/panel-admin/"')

    def test_el_agricultor_conserva_su_menu_sin_entradas_de_administracion(self):
        r = self.con_sesion(self.agricultor).get('/')
        etiquetas = [item['label'] for item in r.context['menu'] if 'label' in item]
        for esperada in ('Inicio', 'Mis predios', 'Actividades', 'Calendario', 'Calculadoras', 'Catálogo agrícola', 'Reportes'):
            self.assertIn(esperada, etiquetas)
        self.assertNotIn('Centro de administración', etiquetas)
        self.assertNotIn('Django Admin', etiquetas)
        self.assertEqual(r.context['user']['role'], 'Agricultor')
        self.assertEqual(r.context['home_url'], '/')
