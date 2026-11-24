"""Fase A1: ``render_page`` calcula los predios (``agro-farms``) y el catálogo (``agro-catalog``) solo donde se leen.

Las pantallas que los usan (plantilla o JavaScript) reciben exactamente lo mismo que antes. El resto recibe la misma
estructura vacía, con las mismas claves, y ya no ejecuta esas consultas en cada clic.
"""

import json
import re
from datetime import date
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.catalog import catalog_payload, empty_catalog_payload
from core.models import (
    ActividadAgricola,
    Cultivo,
    Insumo,
    Potrero,
    Predio,
    ReferenciaAgronomica,
    TemporadaCultivo,
    TipoInsumo,
    Zona,
)
from core.views import base
from core.views.contexto_predios import empty_farm_payload, farm_payload

User = get_user_model()
CORE = Path(__file__).resolve().parent.parent
TEMPLATES = CORE / 'templates'
JS = CORE / 'static' / 'js'


def json_tag(response, tag_id):
    match = re.search(r'<script id="%s" type="application/json">(.*?)</script>' % tag_id, response.content.decode(), re.S)
    return json.loads(match.group(1)) if match else None


class DatosPorPantallaBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('agricultor', email='a@example.com', password='Clave-segura-123')
        self.crop = Cultivo.objects.create(nombre='Maíz prueba', rendimiento_ton_ha=10, densidad_siembra=25)
        tipo = TipoInsumo.objects.create(nombre='Semilla')
        self.insumo = Insumo.objects.create(nombre='Semilla de maíz', tipo=tipo, cultivo=self.crop)
        self.farm = Predio.objects.create(propietario=self.user, nombre='Campo', ubicacion='X', superficie_ha=10)
        self.plot = Potrero.objects.create(predio=self.farm, nombre='Lote A', superficie_ha=4, cultivo=self.crop)
        self.season = TemporadaCultivo.objects.create(
            potrero=self.plot, cultivo=self.crop, temporada_agricola='2026-2027', fecha_siembra=date(2026, 9, 1)
        )
        self.activity = ActividadAgricola.objects.create(
            usuario=self.user, potrero=self.plot, temporada=self.season, nombre='Riego',
            tipo=ActividadAgricola.Tipo.choices[0][0], fecha_programada=date(2026, 12, 1),
        )
        self.client.force_login(self.user)
        p, pl = self.farm.pk, self.plot.pk
        # Pantallas que leen agro-farms / agro-catalog (plantilla o JS) y deben recibir los datos completos.
        self.farm_pages = ['/', '/predios/', f'/predios/{p}/potreros/nuevo/', f'/predios/{p}/potreros/{pl}/editar/',
                           '/decisiones/', '/busqueda/?q=riego']
        self.catalog_pages = ['/predios/', '/decisiones/', '/calendario/', '/actividades/nueva/',
                              f'/actividades/{self.activity.pk}/editar/', '/busqueda/?q=riego']
        # Pantallas que no leen ninguno de los dos.
        self.plain_pages = [
            f'/predios/{p}/', f'/predios/{p}/potreros/{pl}/temporadas/{self.season.pk}/editar/', '/predios/archivados/',
            '/actividades/', f'/actividades/{self.activity.pk}/', '/calculadoras/', '/productos/',
            f'/productos/{self.insumo.pk}/', '/reportes/', '/cuenta/perfil/',
        ]


class EstructuraVaciaTests(DatosPorPantallaBase):
    def test_predios_vacios_tienen_la_misma_forma_que_una_cuenta_sin_predios(self):
        newcomer = User.objects.create_user('nuevo', email='n@example.com', password='Clave-segura-123')
        self.assertEqual(empty_farm_payload(), farm_payload(newcomer))

    def test_la_estructura_vacia_no_consulta_la_base_de_datos(self):
        with CaptureQueriesContext(connection) as queries:
            empty_farm_payload()
            empty_catalog_payload()
        self.assertEqual(len(queries), 0)


class PantallasConDatosTests(DatosPorPantallaBase):
    def test_las_pantallas_que_leen_agro_farms_reciben_los_predios_completos(self):
        expected = farm_payload(self.user)
        for url in self.farm_pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['farm_data'], expected)
                self.assertEqual(json_tag(response, 'agro-farms')['plots'][0]['id'], str(self.plot.pk))

    def test_las_pantallas_que_leen_agro_catalog_reciben_el_catalogo_completo(self):
        expected = catalog_payload()
        for url in self.catalog_pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['catalog'], expected)
                self.assertEqual(json_tag(response, 'agro-catalog')['cultivos'][0]['id'], str(self.crop.pk))

    def test_el_mapa_recibe_la_geometria_de_los_potreros(self):
        self.plot.geojson = {'type': 'Polygon', 'coordinates': [[[-71, -33], [-71, -33.1], [-71.1, -33.1], [-71, -33]]]}
        self.plot.save()
        for url in ('/predios/', f'/predios/{self.farm.pk}/potreros/{self.plot.pk}/editar/', '/decisiones/'):
            with self.subTest(url=url):
                self.assertEqual(json_tag(self.client.get(url), 'agro-farms')['plots'][0]['geojson'], self.plot.geojson)

    def test_el_formulario_del_predio_conserva_sus_potreros_sin_geometria(self):
        for url in ('/predios/nuevo/', f'/predios/{self.farm.pk}/editar/'):
            with self.subTest(url=url):
                plots = self.client.get(url).context['farm_data']['plots']
                self.assertEqual([plot['id'] for plot in plots], [str(self.plot.pk)])
                self.assertIsNone(plots[0]['geojson'])



class CatalogoVacioTests(TestCase):
    """Sin cultivos, insumos, zonas ni referencias (cuenta con un predio sin potreros)."""

    def setUp(self):
        for model in (ReferenciaAgronomica, Insumo, Cultivo):
            model.objects.all().delete()
        user = User.objects.create_user('agricultor', email='a@example.com', password='Clave-segura-123')
        Predio.objects.create(propietario=user, nombre='Campo', ubicacion='X', superficie_ha=10)
        self.client.force_login(user)

    def test_catalogo_vacio_tiene_la_misma_forma_que_un_catalogo_sin_datos(self):
        Predio.objects.all().delete()
        Zona.objects.all().delete()
        self.assertEqual(empty_catalog_payload(), catalog_payload())

    def test_un_catalogo_vacio_sigue_vacio_en_las_pantallas_que_lo_leen(self):
        for url in ('/predios/', '/decisiones/'):
            with self.subTest(url=url):
                catalog = self.client.get(url).context['catalog']
                self.assertEqual(catalog['cultivos'], [])
                self.assertEqual(catalog['semillas'], [])
        self.assertEqual(Cultivo.objects.count(), 0)


class PantallasSinDatosTests(DatosPorPantallaBase):
    def test_las_demas_pantallas_no_calculan_predios_ni_catalogo(self):
        with mock.patch.object(base, 'farm_payload') as farms, mock.patch.object(base, 'catalog_payload') as catalog:
            for url in self.plain_pages:
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
        farms.assert_not_called()
        catalog.assert_not_called()

    def test_calendario_y_actividades_no_calculan_los_predios(self):
        with mock.patch.object(base, 'farm_payload') as farms:
            for url in ('/calendario/', '/actividades/nueva/', f'/actividades/{self.activity.pk}/editar/'):
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
        farms.assert_not_called()

    def test_inicio_y_formularios_de_terreno_no_calculan_el_catalogo(self):
        p = self.farm.pk
        urls = ('/', '/predios/nuevo/', f'/predios/{p}/editar/', f'/predios/{p}/potreros/nuevo/',
                f'/predios/{p}/potreros/{self.plot.pk}/editar/')
        with mock.patch.object(base, 'catalog_payload') as catalog:
            for url in urls:
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
        catalog.assert_not_called()

    def test_las_etiquetas_siguen_en_la_pagina_con_la_estructura_vacia(self):
        for url in self.plain_pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(json_tag(response, 'agro-farms'), empty_farm_payload())
                self.assertEqual(json_tag(response, 'agro-catalog'), empty_catalog_payload())

    def test_calculadoras_no_consulta_tablas_del_catalogo_ni_de_temporadas(self):
        with CaptureQueriesContext(connection) as queries:
            self.client.get('/calculadoras/')
        sql = ' '.join(query['sql'] for query in queries)
        for table in ('core_cultivo', 'core_insumo', 'core_zona', 'core_referenciaagronomica', 'core_temporadacultivo'):
            self.assertNotIn(table, sql)


class ListaDePantallasTests(TestCase):
    """Si una plantilla nueva empieza a leer agro-farms / agro-catalog, debe quedar en la lista de ``render_page``."""

    @staticmethod
    def readers(tag_id):
        pattern = re.compile(r"""(readJson|getElementById)\(\s*['"]%s['"]""" % tag_id)
        return {path.name for path in JS.glob('*.js') if pattern.search(path.read_text(encoding='utf-8'))}

    @staticmethod
    def templates_loading(scripts):
        found = set()
        for path in TEMPLATES.rglob('*.html'):
            text = path.read_text(encoding='utf-8')
            if any(f'js/{name}' in text for name in scripts):
                found.add(path.relative_to(TEMPLATES).as_posix())
        return found

    @staticmethod
    def templates_using(variable):
        pattern = re.compile(r'\b%s\.' % variable)
        return {
            path.relative_to(TEMPLATES).as_posix()
            for path in TEMPLATES.rglob('*.html')
            if path.name != 'body.html' and pattern.search(path.read_text(encoding='utf-8'))
        }

    def test_toda_plantilla_que_lee_agro_farms_esta_en_la_lista(self):
        readers = self.readers('agro-farms')
        self.assertIn('predios-map.js', readers)
        needed = self.templates_loading(readers) | self.templates_using('farm_data')
        self.assertLessEqual(needed, base.FARM_DATA_TEMPLATES)

    def test_toda_plantilla_que_lee_agro_catalog_esta_en_la_lista(self):
        readers = self.readers('agro-catalog')
        self.assertIn('actividades.js', readers)
        # predios-map.js lee el catálogo solo para el diálogo de cultivo, que existe únicamente en predios.html.
        needed = self.templates_loading(readers - {'predios-map.js'}) | self.templates_using('catalog')
        self.assertLessEqual(needed, base.CATALOG_TEMPLATES)
        self.assertIn('predios.html', base.CATALOG_TEMPLATES)
        self.assertEqual(
            {path.relative_to(TEMPLATES).as_posix() for path in TEMPLATES.rglob('*.html')
             if 'id="crop-dialog"' in path.read_text(encoding='utf-8')},
            {'predios.html'},
        )

    def test_las_plantillas_de_la_lista_existen(self):
        for name in base.FARM_DATA_TEMPLATES | base.CATALOG_TEMPLATES:
            with self.subTest(template=name):
                self.assertTrue((TEMPLATES / name).is_file())
