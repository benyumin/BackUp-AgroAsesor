"""Pruebas de las herramientas de la base de datos única (estado y respaldo), cuentas y registro."""

import sqlite3
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from core import dbutils


class EstadoYRespaldoTests(TestCase):
    def test_estado_bd_informa_conteos(self):
        salida = StringIO()
        call_command('estado_bd', stdout=salida)
        texto = salida.getvalue()
        self.assertIn('BASE DE DATOS EN USO', texto)
        self.assertIn('Usuarios', texto)

    def test_ruta_oficial_solo_es_la_de_docker(self):
        self.assertFalse(dbutils.es_base_oficial())

    def test_respaldo_crea_copia_y_poda(self):
        with tempfile.TemporaryDirectory() as carpeta:
            base = Path(carpeta) / 'viva.sqlite3'
            con = sqlite3.connect(str(base))
            con.execute('CREATE TABLE t(x)')
            con.execute('INSERT INTO t VALUES (1)')
            con.commit()
            con.close()
            from unittest import mock

            with mock.patch.object(dbutils, 'ruta_bd', return_value=base):
                primero = dbutils.respaldar(directorio=Path(carpeta) / 'bk', mantener=1)
                self.assertTrue(primero.exists())
                copia = sqlite3.connect(str(primero))
                try:
                    self.assertEqual(copia.execute('SELECT x FROM t').fetchone()[0], 1)
                finally:
                    copia.close()  # en Windows un archivo abierto no se puede borrar al cerrar la carpeta temporal


class CicloDeCuentaTests(TestCase):
    """Registrarse, salir, volver más tarde y entrar directo al panel (por usuario o por correo)."""

    DATOS = {
        'mode': 'register',
        'nombre_completo': 'Rosa Campo',
        'email': 'Rosa@Correo.cl',
        'telefono': '+56 9 1111 2222',
        'comuna': 'Melipilla',
        'password1': 'Cultivo-Seguro-2026',
        'password2': 'Cultivo-Seguro-2026',
        'acepta_politicas': 'on',
    }

    def test_registro_salida_y_regreso_con_el_correo(self):
        r = self.client.post('/login/', self.DATOS)
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.client.post('/logout/')
        for identificador in ('rosa@correo.cl', 'ROSA@CORREO.CL', 'Rosa@Correo.cl'):
            with self.subTest(identificador=identificador):
                r = self.client.post(
                    '/login/', {'mode': 'login', 'username': identificador, 'password': 'Cultivo-Seguro-2026'}
                )
                self.assertRedirects(r, '/', fetch_redirect_response=False)
                self.assertEqual(self.client.get('/').status_code, 200)  # entra directo al panel
                self.client.post('/logout/')

    def test_contrasena_incorrecta_no_entra(self):
        self.client.post('/login/', self.DATOS)
        self.client.post('/logout/')
        r = self.client.post('/login/', {'mode': 'login', 'username': 'rosa@correo.cl', 'password': 'otra-clave-123'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'incorrectos')

    def test_correo_compartido_por_cuentas_antiguas_no_bloquea_el_acceso(self):
        U = get_user_model()
        U.objects.create_user('vieja_a', email='mismo@correo.cl', password='clave-de-a-2026')
        U.objects.create_user('vieja_b', email='mismo@correo.cl', password='clave-de-b-2026')
        for clave, usuario in (('clave-de-a-2026', 'vieja_a'), ('clave-de-b-2026', 'vieja_b')):
            with self.subTest(usuario=usuario):
                r = self.client.post('/login/', {'mode': 'login', 'username': 'mismo@correo.cl', 'password': clave})
                self.assertRedirects(r, '/', fetch_redirect_response=False)
                self.assertEqual(int(self.client.session['_auth_user_id']), U.objects.get(username=usuario).pk)
                self.client.post('/logout/')
        r = self.client.post('/login/', {'mode': 'login', 'username': 'mismo@correo.cl', 'password': 'mala-clave'})
        self.assertContains(r, 'incorrectos')

    def test_cuenta_desactivada_no_entra(self):
        get_user_model().objects.create_user(
            'baja', email='baja@correo.cl', password='clave-baja-2026', is_active=False
        )
        r = self.client.post('/login/', {'mode': 'login', 'username': 'baja', 'password': 'clave-baja-2026'})
        self.assertContains(r, 'incorrectos')


class RegistroAgricultorTests(TestCase):
    """RF-01: nombre completo, correo, contraseña, teléfono y comuna (validados)."""

    CLAVE = 'Campo-Seguro-2026!'

    def datos(self, **extra):
        base = {
            'mode': 'register',
            'nombre_completo': 'Ana María Pérez Soto',
            'email': 'ana@correo.cl',
            'telefono': '9 8765 4321',
            'comuna': 'san josé de maipo',
            'password1': self.CLAVE,
            'password2': self.CLAVE,
            'acepta_politicas': 'on',
        }
        base.update(extra)
        return base

    def test_registro_guarda_la_ficha_completa_y_nunca_crea_administradores(self):
        r = self.client.post('/login/', self.datos(is_staff='on', is_superuser='on'))
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        usuario = get_user_model().objects.get(email='ana@correo.cl')
        self.assertFalse(usuario.is_staff or usuario.is_superuser)
        self.assertEqual((usuario.first_name, usuario.last_name), ('Ana María', 'Pérez Soto'))
        ficha = usuario.agricultor
        self.assertEqual(usuario.get_full_name(), 'Ana María Pérez Soto')
        self.assertEqual(ficha.telefono, '+56987654321')
        self.assertEqual(ficha.comuna, 'San José de Maipo')

    def test_campos_obligatorios_y_caracteres_invalidos(self):
        casos = {
            'nombre_completo': ['', 'Madonna', 'Ana 123 Pérez', 'Ana <b>Pérez</b>'],
            'telefono': ['', '12345', 'abc', '+1 555 123 4567', '+56 9 1234 56789'],
            'comuna': ['', 'Mel1pilla', '<script>'],
        }
        for campo, valores in casos.items():
            for valor in valores:
                with self.subTest(campo=campo, valor=valor):
                    r = self.client.post('/login/', self.datos(**{campo: valor}))
                    self.assertEqual(r.status_code, 200)
                    self.assertFalse(get_user_model().objects.filter(email='ana@correo.cl').exists())

    def test_validador_de_telefono(self):
        from core.validators import normalizar_telefono

        for bueno in ('+56 9 1234 5678', '912345678', '56912345678', '(22) 123 4567', '09 1234 5678'):
            self.assertRegex(normalizar_telefono(bueno), r'^\+56\d{9}$')

    def test_cuenta_antigua_sin_ficha_ve_aviso_y_puede_completar_su_perfil(self):
        antigua = get_user_model().objects.create_user('vieja@correo.cl', email='vieja@correo.cl', password=self.CLAVE)
        self.client.force_login(antigua)
        self.assertContains(self.client.get('/'), 'Completa tu perfil')
        r = self.client.post(
            '/cuenta/perfil/',
            {
                'nombre_completo': 'Rosa Vieja',
                'telefono': '+56 2 2123 4567',
                'comuna': 'Melipilla',
            },
        )
        self.assertRedirects(r, '/cuenta/perfil/', fetch_redirect_response=False)
        self.assertEqual(antigua.agricultor.telefono, '+56221234567')
        self.assertNotContains(self.client.get('/'), 'Completa tu perfil')

    def test_perfil_exige_sesion_y_ya_no_tiene_campo_rut(self):
        self.assertEqual(self.client.get('/cuenta/perfil/').status_code, 302)
        otra = get_user_model().objects.create_user('b@correo.cl', email='b@correo.cl', password=self.CLAVE)
        self.client.force_login(otra)
        self.assertNotContains(self.client.get('/cuenta/perfil/'), 'name="rut"')

    def test_el_nombre_vive_solo_en_la_cuenta_y_el_perfil_lo_lee_y_lo_edita_ahi(self):
        from core.forms import PerfilForm

        self.client.post('/login/', self.datos())
        usuario = get_user_model().objects.get(email='ana@correo.cl')
        self.assertNotIn('nombre', [campo.name for campo in usuario.agricultor._meta.get_fields()])
        self.assertEqual(str(usuario.agricultor), 'Ana María Pérez Soto')
        self.assertEqual(PerfilForm(user=usuario).initial['nombre_completo'], 'Ana María Pérez Soto')
        r = self.client.post(
            '/cuenta/perfil/',
            {'nombre_completo': 'Rosa Vieja Díaz', 'telefono': '+56 9 8765 4321', 'comuna': 'Melipilla'},
        )
        self.assertRedirects(r, '/cuenta/perfil/', fetch_redirect_response=False)
        usuario.refresh_from_db()
        self.assertEqual((usuario.first_name, usuario.last_name), ('Rosa', 'Vieja Díaz'))
        self.assertEqual(usuario.get_full_name(), 'Rosa Vieja Díaz')
        self.assertEqual(PerfilForm(user=usuario).initial['nombre_completo'], 'Rosa Vieja Díaz')


class UnicaBaseConfiguradaTests(TestCase):
    """Django y Docker apuntan siempre a /data/db.sqlite3 (volumen persistente) y no hay otra base."""

    def _leer(self, nombre):
        from django.conf import settings

        return (Path(settings.BASE_DIR) / nombre).read_text(encoding='utf-8')

    def test_django_tiene_una_sola_base_y_sin_enrutadores(self):
        from django.conf import settings

        self.assertEqual(list(settings.DATABASES), ['default'])
        self.assertEqual(getattr(settings, 'DATABASE_ROUTERS', []), [])

    def test_docker_y_django_usan_la_misma_ruta_oficial(self):
        compose, dockerfile = self._leer('docker-compose.yml'), self._leer('Dockerfile')
        self.assertIn('DJANGO_DB_PATH: /data/db.sqlite3', compose)
        self.assertIn('DJANGO_DB_PATH=/data/db.sqlite3', dockerfile)
        self.assertIn('agroasesor_data:/data', compose)
        self.assertEqual(dbutils.RUTA_OFICIAL_DOCKER, '/data/db.sqlite3')

    def test_la_imagen_no_lleva_bases_ni_respaldos_dentro(self):
        ignorados = self._leer('.dockerignore')
        for patron in ('*.sqlite3', 'backups/'):
            self.assertIn(patron, ignorados)
