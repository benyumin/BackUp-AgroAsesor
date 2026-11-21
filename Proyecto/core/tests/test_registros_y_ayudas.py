"""Eliminar, archivar y restaurar predios y potreros (core/registros.py) y consejos de ayuda (core/ayudas.py)."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import TestCase

from core.models import (
    ActividadAgricola,
    CalculoAgricola,
    Cultivo,
    Potrero,
    Predio,
    PredioHistorial,
    TemporadaCultivo,
)

User = get_user_model()
CSS = Path(__file__).resolve().parent.parent / 'static' / 'css'


def cuadrado(x, y, lado=0.001):
    return [[x, y], [x + lado, y], [x + lado, y + lado], [x, y + lado]]


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.otro = User.objects.create_user('beto', email='beto@example.com', password='Campo-Seguro-2026!')
        self.trigo = Cultivo.objects.create(nombre='Trigo')
        self.predio = Predio.objects.create(propietario=self.user, nombre='Fundo Yarek', ubicacion='Melipilla',
                                            superficie_ha=Decimal('10'))
        self.libre = Potrero.objects.create(predio=self.predio, nombre='Lote libre', superficie_ha=Decimal('2'))
        self.usado = Potrero.objects.create(predio=self.predio, nombre='Lote usado', superficie_ha=Decimal('3'),
                                            cultivo=self.trigo)
        PredioHistorial.objects.create(predio=self.predio, potrero=self.libre, usuario=self.user,
                                       accion=PredioHistorial.Accion.POTRERO_CREADO, resumen='Se creó «Lote libre».')
        self.actividad = ActividadAgricola.objects.create(
            usuario=self.user, potrero=self.usado, nombre='Sembrar', tipo=ActividadAgricola.Tipo.SIEMBRA,
            fecha_programada=date(2030, 1, 10),
        )
        self.client.force_login(self.user)

    def accion(self, tipo, pk, accion, json=False):
        extra = {'HTTP_ACCEPT': 'application/json'} if json else {}
        return self.client.post(f'/registros/{tipo}/{pk}/accion/', {'accion': accion}, **extra)


class EliminarTests(Base):
    def test_potrero_sin_registros_se_elimina_y_el_historial_se_conserva(self):
        respuesta = self.accion('potrero', self.libre.pk, 'eliminar', json=True)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['redirect'], f'/predios/{self.predio.pk}/')
        self.assertFalse(Potrero.todos.filter(pk=self.libre.pk).exists())
        # La línea de creación sigue en la base (sin enlace) y se agrega «Potrero eliminado» al predio.
        self.assertTrue(PredioHistorial.objects.filter(resumen='Se creó «Lote libre».', potrero__isnull=True).exists())
        eliminado = PredioHistorial.objects.get(accion=PredioHistorial.Accion.POTRERO_ELIMINADO)
        self.assertEqual(eliminado.predio, self.predio)
        self.assertIn('Lote libre · 2 ha · Fundo Yarek', eliminado.resumen)

    def test_potrero_con_actividades_calculos_o_temporadas_no_se_elimina(self):
        CalculoAgricola.objects.create(usuario=self.user, potrero=self.usado, operacion='dose', entrada={},
                                       resultado={})
        TemporadaCultivo.objects.create(potrero=self.usado, cultivo=self.trigo)
        info = self.client.get(f'/registros/potrero/{self.usado.pk}/eliminar/?formato=json').json()
        self.assertFalse(info['puede_eliminar'])
        self.assertEqual(info['bloqueos'],
                         ['1 actividad registrada', '1 cálculo guardado', '1 temporada de cultivo'])
        respuesta = self.accion('potrero', self.usado.pk, 'eliminar', json=True)
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn('Puedes archivarlo', respuesta.json()['error'])
        self.assertTrue(Potrero.objects.filter(pk=self.usado.pk).exists())
        self.assertTrue(ActividadAgricola.objects.filter(pk=self.actividad.pk).exists())

    def test_la_base_tampoco_borra_actividades_en_cascada(self):
        with self.assertRaises(ProtectedError):
            self.usado.delete()
        self.assertTrue(ActividadAgricola.objects.filter(pk=self.actividad.pk).exists())

    def test_predio_con_potreros_no_se_elimina_y_sin_potreros_si(self):
        respuesta = self.accion('predio', self.predio.pk, 'eliminar')
        self.assertRedirects(respuesta, f'/registros/predio/{self.predio.pk}/eliminar/', fetch_redirect_response=False)
        self.assertTrue(Predio.objects.filter(pk=self.predio.pk).exists())
        pagina = self.client.get(f'/registros/predio/{self.predio.pk}/eliminar/').content.decode()
        self.assertIn('No se puede eliminar «Fundo Yarek»', pagina)
        self.assertIn('2 potreros', pagina)
        self.assertIn('tiene 2 potreros', pagina)  # mensaje de error de la acción rechazada

        vacio = Predio.objects.create(propietario=self.user, nombre='Sin uso', ubicacion='x', superficie_ha=1)
        PredioHistorial.objects.create(predio=vacio, usuario=self.user, accion=PredioHistorial.Accion.CREADO,
                                       resumen='Predio creado: Sin uso.')
        self.assertRedirects(self.accion('predio', vacio.pk, 'eliminar'), '/predios/', fetch_redirect_response=False)
        self.assertFalse(Predio.todos.filter(pk=vacio.pk).exists())
        self.assertTrue(PredioHistorial.objects.filter(resumen='Predio creado: Sin uso.', predio__isnull=True).exists())
        self.assertTrue(PredioHistorial.objects.filter(accion=PredioHistorial.Accion.ELIMINADO).exists())

    def test_solo_el_dueno_y_solo_por_post(self):
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(f'/registros/potrero/{self.libre.pk}/eliminar/').status_code, 404)
        self.assertEqual(self.accion('potrero', self.libre.pk, 'eliminar').status_code, 404)
        self.assertEqual(self.accion('predio', self.predio.pk, 'archivar').status_code, 404)
        self.assertTrue(Potrero.objects.filter(pk=self.libre.pk).exists())
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f'/registros/potrero/{self.libre.pk}/accion/').status_code, 405)
        self.assertEqual(self.accion('potrero', self.libre.pk, 'otra').status_code, 400)
        self.assertEqual(self.client.get('/registros/tractor/1/eliminar/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.accion('potrero', self.libre.pk, 'eliminar').status_code, 302)
        self.assertTrue(Potrero.objects.filter(pk=self.libre.pk).exists())

    def test_los_menus_ofrecen_eliminar_con_confirmacion(self):
        lista = self.client.get('/predios/').content.decode()
        self.assertIn(f'href="/registros/predio/{self.predio.pk}/eliminar/"', lista)
        ficha = self.client.get(f'/predios/{self.predio.pk}/').content.decode()
        for pk in (self.libre.pk, self.usado.pk):
            self.assertIn(f'href="/registros/potrero/{pk}/eliminar/"', ficha)
        self.assertEqual(ficha.count('data-registro-eliminar'), 3)  # el predio y sus dos potreros
        self.assertIn('js/registro-acciones.js', ficha)


class ArchivarTests(Base):
    def test_archivar_oculta_el_potrero_y_sus_labores_y_restaurar_lo_devuelve(self):
        self.accion('potrero', self.usado.pk, 'archivar')
        self.assertFalse(Potrero.objects.filter(pk=self.usado.pk).exists())
        self.assertTrue(Potrero.todos.filter(pk=self.usado.pk).exists())
        self.assertTrue(ActividadAgricola.objects.filter(pk=self.actividad.pk).exists())  # se conserva
        ficha = self.client.get(f'/predios/{self.predio.pk}/').content.decode()
        self.assertNotIn(f'id="potrero-{self.usado.pk}"', ficha)  # solo queda en el mensaje y el historial
        self.assertNotIn('Sembrar', self.client.get('/actividades/').content.decode())
        granjas = self.client.get('/decisiones/').context['farm_data']
        self.assertNotIn(str(self.usado.pk), [plot['id'] for plot in granjas['plots']])
        self.assertIn('Archivados (1)', self.client.get('/predios/').content.decode())
        archivados = self.client.get('/predios/archivados/').content.decode()
        self.assertIn('Lote usado', archivados)
        self.assertEqual(self.client.get(f'/predios/{self.predio.pk}/').context['potreros'].count(), 1)

        respuesta = self.accion('potrero', self.usado.pk, 'restaurar')
        self.assertRedirects(respuesta, f'/predios/{self.predio.pk}/', fetch_redirect_response=False)
        self.assertTrue(Potrero.objects.filter(pk=self.usado.pk).exists())
        self.assertIn('Sembrar', self.client.get('/actividades/').content.decode())
        acciones = list(PredioHistorial.objects.filter(potrero=self.usado).values_list('accion', flat=True))
        self.assertIn(PredioHistorial.Accion.POTRERO_ARCHIVADO, acciones)
        self.assertIn(PredioHistorial.Accion.POTRERO_RESTAURADO, acciones)

    def test_archivar_un_predio_oculta_sus_potreros(self):
        self.accion('predio', self.predio.pk, 'archivar')
        self.assertFalse(Predio.objects.filter(pk=self.predio.pk).exists())
        self.assertFalse(Potrero.objects.filter(predio=self.predio).exists())
        self.assertEqual(self.client.get(f'/predios/{self.predio.pk}/').status_code, 404)
        self.assertIn('Fundo Yarek', self.client.get('/predios/archivados/').content.decode())
        # Un potrero de un predio archivado no se puede restaurar por separado.
        respuesta = self.accion('potrero', self.libre.pk, 'restaurar', json=True)
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn('Primero restaura el predio', respuesta.json()['error'])
        self.accion('predio', self.predio.pk, 'restaurar')
        self.assertEqual(Potrero.objects.filter(predio=self.predio).count(), 2)

    def test_restaurar_revisa_que_la_superficie_siga_libre(self):
        self.accion('potrero', self.usado.pk, 'archivar')
        Potrero.objects.create(predio=self.predio, nombre='Nuevo', superficie_ha=Decimal('7'))
        respuesta = self.accion('potrero', self.usado.pk, 'restaurar', json=True)
        self.assertEqual(respuesta.status_code, 409)
        self.assertIn('superficie del predio', respuesta.json()['error'])
        self.assertFalse(Potrero.objects.filter(pk=self.usado.pk).exists())

    def test_un_potrero_archivado_libera_su_contorno(self):
        self.predio.geojson = {'type': 'Polygon', 'coordinates': [cuadrado(-71.2, -33.6, 0.02) + [[-71.2, -33.6]]]}
        self.predio.save()
        self.libre.perimetro = cuadrado(-71.199, -33.599)
        self.libre.full_clean()
        self.libre.save()
        self.accion('potrero', self.libre.pk, 'archivar')
        encima = Potrero(predio=self.predio, nombre='Encima', superficie_ha=Decimal('1'),
                         perimetro=cuadrado(-71.199, -33.599))
        encima.full_clean()  # no choca con el archivado


class AyudasTests(Base):
    def test_el_consejo_llega_plegado_y_la_pagina_carga_ayudas_js(self):
        inicio = self.client.get('/').content.decode()
        self.assertRegex(inicio, r'<details class="ayuda no-print" data-ayuda="bienvenida">')
        self.assertIn('Bienvenido a AgroAsesor: así se trabaja', inicio)
        self.assertIn('Primeros pasos en AgroAsesor', inicio)  # la línea que queda cuando está plegado
        self.assertIn('js/ayudas.js', inicio)

    def test_cada_seccion_tiene_su_consejo(self):
        paginas = {
            '/predios/': 'predios', f'/predios/{self.predio.pk}/': 'potreros', '/decisiones/': 'calculadora',
            '/actividades/': 'actividades', '/calendario/': 'calendario', '/reportes/': 'reportes',
        }
        for url, clave in paginas.items():
            with self.subTest(url):
                html = self.client.get(url).content.decode()
                self.assertIn(f'data-ayuda="{clave}"', html)
                self.assertIn('js/ayudas.js', html)

    def test_el_estado_de_los_consejos_ya_no_se_guarda_en_el_servidor(self):
        self.assertEqual(self.client.post('/ayuda/bienvenida/ocultar/').status_code, 404)
        self.assertEqual(self.client.post('/ayuda/mostrar/').status_code, 404)

    def test_perfil_permite_volver_a_mostrarlos_desde_el_navegador(self):
        perfil = self.client.get('/cuenta/perfil/').content.decode()
        self.assertIn('data-ayudas-estado', perfil)
        self.assertIn('data-ayudas-mostrar', perfil)


class TipografiaTests(TestCase):
    def test_un_solo_juego_de_variables_y_pesos_hasta_600(self):
        tema = (CSS / 'tema-figma.css').read_text(encoding='utf-8')
        self.assertIn('--font-sans: Outfit, "Segoe UI", sans-serif;', tema)
        for peso in ('--fw-light: 300', '--fw-regular: 400', '--fw-medium: 500', '--fw-semibold: 600'):
            self.assertIn(peso, tema)
        for hoja in sorted(CSS.glob('*.css')):
            texto = hoja.read_text(encoding='utf-8')
            with self.subTest(hoja.name):
                # Los pesos se escriben con las variables; nunca 700/800/900 ni «bold».
                self.assertIsNone(re.search(r'font-weight:\s*(\d+|bold|bolder)\b', texto))
                for familia in re.findall(r'font-family\s*:\s*([^;}]+)', texto):
                    self.assertTrue(familia.strip().startswith(('var(--', 'inherit')), familia)
