"""Insecticidas, herbicidas y fungicidas vigentes del SAG y registro de variedades aptas para certificación.

Usa los XLSX oficiales guardados en ``data/sag`` (sin llamadas al SAG) y, para las reimportaciones, planillas
armadas en memoria con la misma forma que devuelve ``leer_planillas``.
"""

from datetime import date
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from core.catalog import catalog_payload, ensure_base_crops
from core.models import Cultivo, Insumo, RegistroPlaguicidaSAG, TipoInsumo, VariedadSAG
from core.sag_plaguicidas import (
    PlanillaPlaguicidas,
    fecha_de_nombre,
    importar_planillas,
    leer_planillas,
    tipo_de_aptitud,
)
from core.sag_variedades import RegistroVariedades, importar_variedades, leer_registro_variedades

User = get_user_model()
PASSWORD = 'Clave-segura-123'
SAG = Path(__file__).resolve().parents[2] / 'data' / 'sag'
VIGENTES = SAG / 'Planilla general plaguicidas vigentes (26-10-26).xlsx'
RESUMEN = SAG / 'Plaguicidas Autorizados - resumen al 01-10-2025.xlsx'
VARIEDADES = SAG / 'registro_de_variedades_aptas_para_certificacion_ABRIL 2026.xlsx'


def plaguicida(numero, nombre, aptitud='Herbicida', **extra):
    return {
        'numero_sag': numero,
        'nombre_comercial': nombre,
        'titular': 'Titular S.A.',
        'sustancias_activas': 'GLIFOSATO',
        'concentracion': '48 % p/v',
        'aptitud': aptitud,
        'formulacion': 'Concentrado Soluble (SL)',
        'anio_primera_autorizacion': 2010,
        'anio_vencimiento': 2030,
        **extra,
    }


def planilla(*registros, fecha=date(2026, 10, 26)):
    return PlanillaPlaguicidas(fecha, list(registros), len(registros), [], 0)


class LecturaPlaguicidasTests(TestCase):
    def test_cruce_de_las_planillas_reales(self):
        datos = leer_planillas(VIGENTES, RESUMEN)
        self.assertEqual(datos.fecha, date(2026, 10, 26))
        self.assertEqual(datos.vigentes, 1455)
        self.assertEqual(len(datos.sin_aptitud), 70)
        self.assertEqual(datos.otras_aptitudes, 193)
        self.assertEqual(len(datos.registros), 1192)
        pirimor = next(r for r in datos.registros if r['numero_sag'] == '1004')
        self.assertEqual(pirimor['nombre_comercial'], 'PIRIMOR')
        self.assertEqual(pirimor['sustancias_activas'], 'PIRIMICARB')
        self.assertEqual(pirimor['aptitud'], 'Insecticida')
        self.assertEqual(pirimor['formulacion'], 'Granulado Dispersable (WG)')
        self.assertEqual((pirimor['anio_primera_autorizacion'], pirimor['anio_vencimiento']), (1985, 2026))

    def test_aptitud_y_fecha_del_nombre(self):
        self.assertEqual(tipo_de_aptitud('Insecticida - Acaricida'), 'Insecticida')
        self.assertEqual(tipo_de_aptitud('Bactericida - Fungicida Biológico'), 'Fungicida')
        self.assertEqual(tipo_de_aptitud('Herbicida'), 'Herbicida')
        self.assertIsNone(tipo_de_aptitud('Fitorregulador'))
        self.assertIsNone(tipo_de_aptitud('Acaricida'))
        self.assertEqual(fecha_de_nombre('Planilla general (26-10-26).xlsx'), date(2026, 10, 26))
        self.assertIsNone(fecha_de_nombre('planilla.xlsx'))


class ImportacionPlaguicidasTests(TestCase):
    def test_simulacion_no_escribe_y_aplicar_importa_sin_dosis_ni_verificacion(self):
        salida = StringIO()
        call_command('importar_plaguicidas_sag', str(VIGENTES), str(RESUMEN), stdout=salida)
        self.assertIn('Simulación', salida.getvalue())
        self.assertIn('N° SAG', salida.getvalue())  # informa los vigentes sin aptitud
        self.assertEqual(Insumo.objects.count(), 0)

        call_command('importar_plaguicidas_sag', str(VIGENTES), str(RESUMEN), '--aplicar', stdout=StringIO())
        self.assertEqual(RegistroPlaguicidaSAG.objects.count(), 1192)
        self.assertEqual(Insumo.objects.count(), 1192)
        self.assertEqual(set(Insumo.objects.values_list('tipo__nombre', flat=True)), {'Fungicida', 'Herbicida', 'Insecticida'})
        self.assertFalse(Insumo.objects.filter(dosis_hectarea__isnull=False).exists())
        self.assertFalse(Insumo.objects.exclude(estado_verificacion='unverified').exists())
        self.assertFalse(Insumo.objects.exclude(cultivos_autorizados=[]).exists())
        pirimor = Insumo.objects.get(numero_autorizacion_sag='1004')
        self.assertTrue(pirimor.es_fitosanitario)
        self.assertEqual(pirimor.ingrediente_activo, 'PIRIMICARB')
        self.assertEqual(pirimor.fabricante, 'SYNGENTA S.A.')
        self.assertIn('26-10-2026', pirimor.fuente_nombre)
        self.assertLessEqual(max(len(c) for c in Insumo.objects.values_list('concentracion', flat=True)), 100)

        # Reimportar el mismo archivo no duplica ni cambia nada.
        salida = StringIO()
        call_command('importar_plaguicidas_sag', str(VIGENTES), str(RESUMEN), '--aplicar', stdout=salida)
        self.assertEqual(Insumo.objects.count(), 1192)
        self.assertIn('sin cambios: 1192', salida.getvalue())

    def test_archivo_sin_columnas_esperadas(self):
        with self.assertRaises(CommandError):
            call_command('importar_plaguicidas_sag', str(VARIEDADES), str(RESUMEN), stdout=StringIO())

    def test_retirado_queda_no_vigente_y_vuelve_si_reaparece(self):
        importar_planillas(planilla(plaguicida('1', 'Uno'), plaguicida('2', 'Dos')), 'a.xlsx')
        resultado = importar_planillas(planilla(plaguicida('1', 'Uno')), 'b.xlsx')
        self.assertEqual(resultado.retirados, 1)
        dos = Insumo.objects.get(nombre='Dos')
        self.assertFalse(dos.vigente)
        self.assertFalse(dos.registro_plaguicida.en_planilla_vigente)

        importar_planillas(planilla(plaguicida('1', 'Uno'), plaguicida('2', 'Dos')), 'c.xlsx')
        dos.refresh_from_db()
        self.assertTrue(dos.vigente)
        self.assertTrue(dos.registro_plaguicida.en_planilla_vigente)

    def test_respeta_lo_editado_y_vincula_insumo_curado_por_numero_sag(self):
        herbicida = TipoInsumo.objects.create(nombre='Herbicida')
        curado = Insumo.objects.create(tipo=herbicida, nombre='Roundup curado', numero_autorizacion_sag='7')
        importar_planillas(planilla(plaguicida('1', 'Uno'), plaguicida('7', 'ROUNDUP')), 'a.xlsx')
        curado.refresh_from_db()
        self.assertEqual(curado.nombre, 'Roundup curado')
        self.assertFalse(curado.registro_plaguicida.insumo_creado_por_importacion)

        uno = Insumo.objects.get(nombre='Uno')
        uno.descripcion = 'Editado por el administrador'
        uno.save()
        importar_planillas(planilla(plaguicida('1', 'Uno', titular='Otro titular')), 'b.xlsx')
        uno.refresh_from_db()
        self.assertEqual(uno.fabricante, 'Otro titular')
        self.assertEqual(uno.descripcion, 'Editado por el administrador')


class CatalogoPlaguicidasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('agricultor', email='a@example.com', password=PASSWORD)
        importar_planillas(
            planilla(
                plaguicida('1', 'Mata Malezas'),
                plaguicida('2', 'Anti Hongos', aptitud='Fungicida', sustancias_activas='AZOXISTROBINA'),
            ),
            'a.xlsx',
        )

    def setUp(self):
        self.client.login(username='agricultor', password=PASSWORD)

    def test_no_van_en_el_catalogo_de_cada_pagina_pero_si_en_productos_y_busqueda(self):
        self.assertEqual(catalog_payload()['insumos'], [])
        response = self.client.get(reverse('productos'), {'categoria': 'fungicide'})
        self.assertEqual([p.nombre for p in response.context['products_page']], ['Anti Hongos'])
        response = self.client.get(reverse('busqueda'), {'q': 'azoxistrobina'})
        self.assertContains(response, 'Anti Hongos')

    def test_ficha_y_pdf_del_plaguicida_importado(self):
        producto = Insumo.objects.get(nombre='Anti Hongos')
        response = self.client.get(reverse('producto_detail', args=[producto.pk]))
        self.assertContains(response, 'AZOXISTROBINA')
        self.assertContains(response, 'Aptitud declarada por el SAG: Fungicida.')
        self.assertEqual(self.client.get(reverse('producto_pdf', args=[producto.pk])).status_code, 200)


class VariedadesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_base_crops()

    def test_registro_real_solo_cultivos_de_la_aplicacion(self):
        lectura = leer_registro_variedades(VARIEDADES)
        self.assertEqual(lectura.por_hoja, {'Variedades Comerciales': 151, 'Variedades Locales': 10})
        salida = StringIO()
        call_command('importar_variedades_sag', str(VARIEDADES), stdout=salida)
        self.assertIn('Simulación', salida.getvalue())
        self.assertEqual(VariedadSAG.objects.count(), 0)

        call_command('importar_variedades_sag', str(VARIEDADES), '--aplicar', stdout=salida)
        por_cultivo = {c.nombre: c.variedades_sag.count() for c in Cultivo.objects.all()}
        self.assertEqual(por_cultivo['Papa'], 65)  # CORAHILA está en comerciales y locales: se guarda una vez
        self.assertEqual(por_cultivo['Trigo'], 45)  # harinero + candeal
        self.assertEqual(por_cultivo['Trébol'], 2)
        self.assertEqual(VariedadSAG.objects.count(), 145)
        self.assertIn('Repetidas dentro del archivo (se importan una vez): 1', salida.getvalue())
        self.assertFalse(VariedadSAG.objects.filter(especie__in=['Triticale', 'Lupino', 'Quinoa']).exists())
        self.assertIn('Triticale (7)', salida.getvalue())

        arroz = Cultivo.objects.get(nombre='Arroz')
        self.assertIn('CUARZO INIA', catalog_payload()['varieties'][str(arroz.pk)])

    def test_variedad_que_sale_del_registro_deja_de_ofrecerse(self):
        papa = Cultivo.objects.get(nombre='Papa')

        def registro(*variedades):
            filas = [
                {'especie': 'Papa', 'nombre_cientifico': '', 'variedad': v, 'mantenedor': '', 'registro': 'comercial', 'actualizacion': 'ABRIL 2026'}
                for v in variedades
            ]
            return RegistroVariedades(filas, {})

        importar_variedades(registro('DESIREE', 'KARU'), 'a.xlsx')
        resultado = importar_variedades(registro('DESIREE'), 'b.xlsx')
        self.assertEqual(resultado.retiradas, 1)
        self.assertEqual(catalog_payload()['varieties'][str(papa.pk)], ['DESIREE'])
