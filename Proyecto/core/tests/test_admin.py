"""Pruebas de RF-04: administradores solo desde cuentas existentes, sin registro público de administradores."""

import io
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core import dbutils

User = get_user_model()


def ejecutar(*args):
    salida = io.StringIO()
    call_command('administradores', *args, stdout=salida)
    return salida.getvalue()


class AdministradoresCommandTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user('ana', 'ana@example.com', 'Clave-segura-123')
        self.b = User.objects.create_user('beto', 'beto@example.com', 'Clave-segura-123')
        self.c = User.objects.create_user('carla', 'carla@example.com', 'Clave-segura-123')

    def test_listar_no_modifica(self):
        texto = ejecutar()
        self.assertIn('ana@example.com', texto)
        self.assertIn('Administradores actuales: 0', texto)
        self.assertFalse(User.objects.filter(is_staff=True).exists())

    def test_asignar_sin_aplicar_es_prueba(self):
        texto = ejecutar('--asignar', 'ana@example.com', 'beto')
        self.assertIn('MODO PRUEBA', texto)
        self.assertFalse(User.objects.filter(is_staff=True).exists())

    def test_asignar_aplicar_marca_cuentas_existentes_sin_tocar_contrasena(self):
        hash_antes = self.a.password
        ejecutar('--asignar', 'ANA@example.com', 'beto', '--aplicar')
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.c.refresh_from_db()
        self.assertTrue(self.a.is_staff and self.a.is_superuser)
        self.assertTrue(self.b.is_staff)
        self.assertFalse(self.c.is_staff)
        self.assertEqual(self.a.password, hash_antes)
        self.assertEqual(User.objects.count(), 3)

    def test_cuenta_inexistente_no_se_crea(self):
        with self.assertRaises(CommandError):
            ejecutar('--asignar', 'nadie@example.com', '--aplicar')
        self.assertEqual(User.objects.count(), 3)

    def test_correo_ambiguo_pide_usuario_exacto(self):
        User.objects.create_user('ana2', 'ana@example.com', 'Clave-segura-123')
        with self.assertRaises(CommandError):
            ejecutar('--asignar', 'ana@example.com', '--aplicar')
        self.assertEqual(ejecutar('--asignar', 'ana', '--aplicar').count('Listo'), 1)
        self.assertTrue(User.objects.get(username='ana').is_staff)
        self.assertFalse(User.objects.get(username='ana2').is_staff)

    def test_quitar_acceso(self):
        ejecutar('--asignar', 'ana', 'beto', 'carla', '--aplicar')
        ejecutar('--quitar', 'carla', '--aplicar')
        self.assertFalse(User.objects.get(username='carla').is_staff)
        self.assertEqual(User.objects.filter(is_staff=True).count(), 2)

    def test_avisa_si_hay_mas_de_tres(self):
        User.objects.create_user('dani', 'dani@example.com', 'Clave-segura-123')
        texto = ejecutar('--asignar', 'ana', 'beto', 'carla', 'dani')
        self.assertIn('la meta es 3', texto)

    def test_misma_cuenta_en_asignar_y_quitar_falla(self):
        with self.assertRaises(CommandError):
            ejecutar('--asignar', 'ana', '--quitar', 'ana')


class RegistroPublicoNoCreaAdministradoresTests(TestCase):
    def test_registro_publico_crea_solo_usuario_normal(self):
        self.client.post(
            '/login/',
            {
                'mode': 'register',
                'nombre_completo': 'Pedro Pérez',
                'email': 'pedro@example.com',
                'telefono': '+56 9 1234 5678',
                'comuna': 'Melipilla',
                'password1': 'Clave-muy-segura-2026',
                'password2': 'Clave-muy-segura-2026',
                'acepta_politicas': 'on',
                'is_staff': 'on',
                'is_superuser': 'on',
            },
        )
        usuario = User.objects.get(email='pedro@example.com')
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)


class RespaldoVerificableTests(TestCase):
    def test_respaldo_verificado_con_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            origen = Path(tmp) / 'origen.sqlite3'
            import sqlite3

            con = sqlite3.connect(origen)
            con.execute('CREATE TABLE auth_user (id INTEGER PRIMARY KEY)')
            con.execute('INSERT INTO auth_user (id) VALUES (1)')
            con.commit()
            con.close()
            carpeta = Path(tmp) / 'respaldos'
            with override_settings(
                DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(origen)}}
            ):
                destino = dbutils.respaldar(directorio=carpeta)
            self.assertTrue(destino.exists())
            suma = Path(f'{destino}.sha256')
            self.assertTrue(suma.exists())
            info = dbutils.verificar_respaldo(destino)
            self.assertTrue(info['ok'])
            self.assertEqual(info['usuarios'], 1)
            self.assertIn(info['sha256'], suma.read_text())
