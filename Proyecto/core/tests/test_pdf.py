"""Informes PDF generados en el servidor (``core/pdf``).

Comprueban que cada descarga sea un PDF real (``application/pdf``, empieza con ``%PDF``), que solo el dueño pueda
descargar sus cálculos y que el contenido use los datos guardados (superficie con 4 decimales, resultado,
procedencia del dato). Para leer el texto se usa ``pypdf`` si está instalado; si no, esas comprobaciones se omiten.
"""

import json
import re
import unittest
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import CalculoAgricola, Cultivo, Insumo, Potrero, Predio, ReferenciaAgronomica, TipoInsumo, Zona
from core.pdf.base import numero

try:
    from pypdf import PdfReader
except ImportError:  # pypdf no es una dependencia del proyecto
    PdfReader = None

User = get_user_model()


def texto_pdf(contenido):
    lector = PdfReader(BytesIO(contenido))
    return ' '.join(pagina.extract_text() for pagina in lector.pages), len(lector.pages)


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'ana', email='ana@example.com', password='Campo-Seguro-2026!', first_name='Ana', last_name='Pérez'
        )
        self.otro = User.objects.create_user('beto', email='beto@example.com', password='Campo-Seguro-2026!')
        self.trigo = Cultivo.objects.create(nombre='Trigo')
        self.predio = Predio.objects.create(propietario=self.user, nombre='Fundo Yarek', ubicacion='Melipilla',
                                            superficie_ha=9)
        self.potrero = Potrero.objects.create(predio=self.predio, nombre='Potrero chico',
                                              superficie_ha=Decimal('0.1496'), perimetro=[], cultivo=self.trigo)
        self.client.force_login(self.user)

    def calcular(self, **datos):
        cuerpo = {'operation': 'dose', 'calculation_type': 'semillas', 'plot_id': self.potrero.pk, 'area': '0.1496',
                  'dose': '100', 'unit': 'kg/ha', 'crop': 'Trigo', **datos}
        respuesta = self.client.post('/calculos/ejecutar/', json.dumps(cuerpo), content_type='application/json')
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()['calculation_id']

    def assertPdf(self, respuesta, nombre=None):
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(respuesta.content.startswith(b'%PDF-'))
        self.assertIn(b'%%EOF', respuesta.content[-1024:])
        self.assertIn('attachment;', respuesta['Content-Disposition'])
        if nombre:
            self.assertIn(nombre, respuesta['Content-Disposition'])
        # La tipografía de la plataforma va incrustada en el documento.
        self.assertRegex(respuesta.content, rb'/BaseFont /[A-Z]{6}\+Outfit')


class InformeCalculoTests(Base):
    def test_es_un_pdf_real_y_solo_lo_descarga_el_dueno(self):
        pk = self.calcular()
        self.assertPdf(self.client.get(f'/calculos/{pk}/pdf/'), f'agroasesor-calculo-semillas-{pk}.pdf')
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(f'/calculos/{pk}/pdf/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(f'/calculos/{pk}/pdf/').status_code, 302)

    @unittest.skipIf(PdfReader is None, 'pypdf no está instalado')
    def test_informe_de_semillas_con_dosis_manual(self):
        pk = self.calcular(dose_source='Mi asesor')
        texto, paginas = texto_pdf(self.client.get(f'/calculos/{pk}/pdf/').content)
        self.assertEqual(paginas, 1)
        for esperado in (
            'AgroAsesor', 'Informe de cálculo de semillas', 'Generado el', 'Información del terreno',
            'Fundo Yarek', 'Potrero chico', 'Trigo', '0,1496 ha', 'Parámetros del cálculo', 'Dosis por hectárea',
            'kg/ha', 'Sin fuente técnica verificada', 'Mi asesor', 'Resultado principal', 'SEMILLA NECESARIA',
            '14,96', 'Detalle de la operación', '0,1496 ha × 100 kg/ha = 14,96 kg', 'Observaciones técnicas',
            'Simulación con datos ingresados por la persona', 'Limitaciones del cálculo', 'Página 1 de 1',
            'no reemplazan la evaluación de un profesional agrónomo',
        ):
            self.assertIn(esperado, texto)
        self.assertNotIn('0,15 ha', texto)
        self.assertNotIn('ana@example.com', texto)  # el correo no se imprime

    @unittest.skipIf(PdfReader is None, 'pypdf no está instalado')
    def test_dosis_documentada_se_presenta_como_respaldada(self):
        self.trigo.densidad_siembra = Decimal('180')
        self.trigo.densidad_fuente = 'Ficha de prueba'
        self.trigo.densidad_url = 'https://example.com/ficha'
        self.trigo.densidad_verificada = '2026-03-01'
        self.trigo.save()
        texto, _ = texto_pdf(self.client.get(f'/calculos/{self.calcular(dose="180")}/pdf/').content)
        self.assertIn('Cálculo respaldado por información verificada', texto)
        self.assertIn('Ficha de prueba', texto)
        self.assertIn('verificada el 01/03/2026', texto)
        self.assertNotIn('Simulación', texto)

    def test_todas_las_operaciones_generan_su_informe(self):
        tipo = TipoInsumo.objects.create(nombre='Fertilizante')
        urea = Insumo.objects.create(
            nombre='Urea', tipo=tipo, vigente=True, estado_verificacion='verified', fuente_nombre='Ficha',
            fuente_url='https://example.com', fecha_verificacion='2026-01-01', composicion_nutricional='N 46 %',
            nitrogeno_pct=46, unidad_dosis='kg/ha',
        )
        casos = {
            'coverage': {'operation': 'coverage', 'calculation_type': 'coverage', 'stock': '10'},
            'seed_population': {'operation': 'seed_population', 'plants_m2': '300', 'thousand_seed_weight_g': '45',
                                'germination_pct': '90', 'purity_pct': '98', 'source': 'Ficha'},
            'fertilizer_nutrition': {'operation': 'fertilizer_nutrition', 'calculation_type': 'insumos',
                                     'product_id': urea.pk, 'nutrient': 'N', 'need_per_ha': '46',
                                     'need_source': 'Análisis de suelo'},
            'insumos': {'calculation_type': 'insumos'},
        }
        for nombre, datos in casos.items():
            with self.subTest(nombre):
                self.assertPdf(self.client.get(f'/calculos/{self.calcular(**datos)}/pdf/'))
        if PdfReader:
            ultimo = CalculoAgricola.objects.filter(operacion='coverage').get()
            texto, _ = texto_pdf(self.client.get(f'/calculos/{ultimo.pk}/pdf/').content)
            self.assertIn('Informe de disponibilidad de insumo', texto)
            self.assertIn('Superficie cubierta', texto)

    def test_la_pantalla_pide_el_pdf_al_servidor(self):
        html = self.client.get('/decisiones/?tipo=semillas&herramienta=llevar').content.decode()
        self.assertIn('data-pdf-url="/calculos/0/pdf/"', html)
        self.assertNotIn('jspdf', html)
        self.assertNotIn('agro-pdf.js', html)


class OtrosInformesTests(Base):
    def test_reportes_en_pdf_para_cada_tipo(self):
        self.calcular()
        for tipo in ('resumen', 'cuaderno', 'cultivos', 'calculos'):
            with self.subTest(tipo):
                self.assertPdf(self.client.get(f'/reportes/?tipo={tipo}&formato=pdf'), f'agroasesor-{tipo}-')
        html = self.client.get('/reportes/?tipo=calculos').content.decode()
        self.assertIn('formato=pdf', html)
        self.assertNotIn('window.print()', html)
        if PdfReader:
            texto, _ = texto_pdf(self.client.get('/reportes/?tipo=calculos&formato=pdf').content)
            for esperado in ('Cálculos guardados', 'Filtros aplicados', 'Potrero chico', '× 0,1496 ha'):
                self.assertIn(esperado, texto)

    def test_calendario_en_pdf(self):
        zona = Zona.objects.create(nombre='Melipilla')
        referencia = ReferenciaAgronomica.objects.create(
            cultivo=self.trigo, zona=zona, siembra_inicio_mes=5, siembra_fin_mes=7, cosecha_inicio_mes=12,
            cosecha_fin_mes=1,
        )
        self.assertPdf(self.client.get(f'/calendario/referencias/{referencia.pk}/pdf/?anio=2026'),
                       'agroasesor-calendario-trigo-2026.pdf')
        if PdfReader:
            texto, _ = texto_pdf(self.client.get(f'/calendario/referencias/{referencia.pk}/pdf/?anio=2026').content)
            self.assertIn('Calendario de siembra · Trigo', texto)
            self.assertIn('sin fuente verificada', texto)
        html = self.client.get('/calendario/').content.decode()
        self.assertIn('data-pdf-url="/calendario/referencias/0/pdf/"', html)
        referencia.estado = ReferenciaAgronomica.Estado.RETIRADA
        referencia.save(update_fields=['estado'])
        self.assertEqual(self.client.get(f'/calendario/referencias/{referencia.pk}/pdf/').status_code, 404)

    def test_ficha_de_producto_en_pdf(self):
        producto = Insumo.objects.create(nombre='Semilla de trigo', tipo=TipoInsumo.objects.create(nombre='Semilla'),
                                         vigente=True)
        self.assertPdf(self.client.get(f'/productos/{producto.pk}/pdf/'), 'agroasesor-ficha-semilla-de-trigo.pdf')
        html = self.client.get(f'/productos/{producto.pk}/').content.decode()
        self.assertIn(f'/productos/{producto.pk}/pdf/', html)
        self.assertNotIn('window.print()', html)


class FormatoTests(TestCase):
    def test_numeros_en_formato_chileno_sin_ceros_sobrantes(self):
        self.assertEqual(numero(Decimal('0.1496'), 4), '0,1496')
        self.assertEqual(numero(14.96, 2), '14,96')
        self.assertEqual(numero(1234.5, 2), '1.234,5')
        self.assertEqual(numero(100, 3), '100')
        self.assertEqual(numero(66.84, 0), '67')
        self.assertEqual(numero(None), '—')
        self.assertTrue(re.fullmatch(r'\d+', numero('7', 0)))
