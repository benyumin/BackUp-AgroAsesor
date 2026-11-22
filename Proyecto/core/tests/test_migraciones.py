"""Prueba de la migración 0022 (limpieza de la base de datos, etapa 1).

Se vuelve a la migración 0021, se crean datos como estaban entonces, se aplica la 0022 y se revisa que:
  * el nombre de la ficha pase a la cuenta solo cuando la cuenta no tenía nombre propio;
  * desaparezcan las tablas y columnas que ya no se usan;
  * lo que se conserva (teléfono, comuna y superficies del historial) quede intacto.

Usa TransactionTestCase porque cambia el esquema de la base. Para llegar a la 0021 se revierten las migraciones más
nuevas que ella: si algún día se agrega una que no se pueda revertir, esta prueba se adapta o se retira.
"""
from unittest import skipIf

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

ANTES = [('core', '0021_remove_agricultor_rut')]
DESPUES = [('core', '0022_limpieza_base_de_datos')]


def _tablas():
    return set(connection.introspection.table_names())


def _columnas(tabla):
    with connection.cursor() as cursor:
        return {columna.name for columna in connection.introspection.get_table_description(cursor, tabla)}


@skipIf(
    connection.vendor == 'sqlite',
    'Revertir migraciones necesita PostgreSQL: SQLite no lo admite con las claves foráneas activas.',
)
class Migracion0022Tests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ANTES)
        self.apps_antes = ejecutor.loader.project_state(ANTES).apps

    def tearDown(self):
        # Deja la base en el esquema más nuevo para que las demás pruebas no se vean afectadas.
        ejecutor = MigrationExecutor(connection)
        ejecutor.loader.build_graph()
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())
        super().tearDown()

    def aplicar_0022(self):
        ejecutor = MigrationExecutor(connection)
        ejecutor.loader.build_graph()
        ejecutor.migrate(DESPUES)
        return ejecutor.loader.project_state(DESPUES).apps

    def test_el_nombre_de_la_ficha_pasa_a_la_cuenta_solo_si_la_cuenta_no_tiene_nombre(self):
        Usuario = self.apps_antes.get_model('auth', 'User')
        Agricultor = self.apps_antes.get_model('core', 'Agricultor')
        self.assertIn('nombre', _columnas(Agricultor._meta.db_table))
        casos = [
            # (usuario, first_name, last_name, nombre de la ficha, first_name final, last_name final)
            ('sin_nombre_cuatro_palabras', '', '', 'María José Pérez Soto', 'María José', 'Pérez Soto'),
            ('sin_nombre_tres_palabras', '', '', 'Luis Pérez Soto', 'Luis', 'Pérez Soto'),
            ('sin_nombre_dos_palabras', '', '', 'Ana Pérez', 'Ana', 'Pérez'),
            ('sin_nombre_una_palabra', '', '', 'Ana', 'Ana', ''),
            ('con_nombre_propio', 'Carlos', 'Díaz', 'Otro Nombre Distinto', 'Carlos', 'Díaz'),
            ('con_solo_nombre', 'Carla', '', 'Carla Muñoz', 'Carla', ''),
            ('ficha_vacia', '', '', '', '', ''),
            ('ficha_solo_espacios', '', '', '   ', '', ''),
        ]
        for usuario, nombre, apellidos, ficha, _, _ in casos:
            cuenta = Usuario.objects.create(username=usuario, first_name=nombre, last_name=apellidos)
            Agricultor.objects.create(user=cuenta, nombre=ficha, telefono='+56911111111', comuna='Melipilla')

        apps_despues = self.aplicar_0022()

        Usuario = apps_despues.get_model('auth', 'User')
        Agricultor = apps_despues.get_model('core', 'Agricultor')
        for usuario, _, _, _, nombre_final, apellidos_final in casos:
            cuenta = Usuario.objects.get(username=usuario)
            self.assertEqual((cuenta.first_name, cuenta.last_name), (nombre_final, apellidos_final), usuario)
        self.assertNotIn('nombre', _columnas(Agricultor._meta.db_table))
        self.assertEqual(Agricultor.objects.count(), len(casos))
        self.assertEqual(set(Agricultor.objects.values_list('telefono', 'comuna')), {('+56911111111', 'Melipilla')})

    def test_borra_las_tablas_y_columnas_que_ya_no_se_usan(self):
        apps = self.apps_antes
        Problema = apps.get_model('core', 'ProblemaAgricola')
        Insumo = apps.get_model('core', 'Insumo')
        tablas_que_se_van = {
            apps.get_model('core', 'AyudaOculta')._meta.db_table,
            apps.get_model('core', 'CargaCatalogo')._meta.db_table,
            Problema._meta.db_table,
            Insumo._meta.get_field('problemas').remote_field.through._meta.db_table,
        }
        tablas_que_se_van.update(campo.remote_field.through._meta.db_table for campo in Problema._meta.many_to_many)
        historial = apps.get_model('core', 'PredioHistorial')._meta.db_table
        columnas_que_se_van = {'geometria_anterior', 'geometria_nueva'}
        columnas_que_se_quedan = {'superficie_anterior_ha', 'superficie_nueva_ha'}
        # Antes de aplicar la 0022 todo eso existe.
        self.assertLessEqual(tablas_que_se_van, _tablas())
        self.assertLessEqual(columnas_que_se_van | columnas_que_se_quedan, _columnas(historial))

        self.aplicar_0022()

        self.assertEqual(tablas_que_se_van & _tablas(), set())
        columnas = _columnas(historial)
        self.assertEqual(columnas_que_se_van & columnas, set())
        self.assertLessEqual(columnas_que_se_quedan, columnas)
