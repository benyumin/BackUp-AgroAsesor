"""Integración de la lista SAG de fertilizantes y bioestimulantes (agricultura orgánica) con el catálogo y las
calculadoras «Cuánto llevar» y «¿Me alcanza?».

Usa el XLSX oficial guardado en ``data/sag`` (sin llamadas al SAG) y, para los casos de reimportación, listas
armadas en memoria con la misma forma que devuelve ``leer_lista_sag``.
"""

import json
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.models import Count
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.calculos import ErrorCalculo, convertir, ejecutar_calculo
from core.catalog import catalog_payload, category_key
from core.forms import ActividadAgricolaForm
from core.models import CalculoAgricola, Cultivo, Insumo, Potrero, Predio, RegistroListaSAG, TipoInsumo
from core.sag import ListaSAG, clasificar, fecha_excel, huella, importar_lista, leer_lista_sag

User = get_user_model()
PASSWORD = 'Clave-segura-123'
PROYECTO = Path(__file__).resolve().parents[2]
XLSX = PROYECTO / 'data' / 'sag' / '39. Fertilizantes y bioestimulantes_09112026.xlsx'
STATIC = PROYECTO / 'core' / 'static'
Clasificacion = RegistroListaSAG.Clasificacion


def fila(nombre, fabricante='Fabricante SpA', sustancia='Extracto de algas', funcion='Bioestimulante', **extra):
    return {
        'numero_lista': None,
        'nombre_comercial': nombre,
        'sustancia_activa': sustancia,
        'fabricante': fabricante,
        'funcion': funcion,
        'fecha_autorizacion': None,
        'observaciones': '',
        **extra,
    }


def lista(*filas, fecha=date(2026, 11, 9)):
    return ListaSAG(fecha, list(filas), [], 0)


class LecturaDelArchivoOficialTests(TestCase):
    def test_columnas_reales_cantidad_y_fecha_de_la_lista(self):
        datos = leer_lista_sag(XLSX)
        self.assertEqual(datos.fecha_lista, date(2026, 11, 9))
        self.assertEqual(len(datos.registros), 756)
        self.assertEqual(
            datos.columnas,
            sorted(
                [
                    'numero_lista',
                    'nombre_comercial',
                    'sustancia_activa',
                    'fabricante',
                    'funcion',
                    'fecha_autorizacion',
                    'observaciones',
                ]
            ),
        )
        primero = datos.registros[0]
        self.assertEqual(primero['numero_lista'], 1)
        self.assertEqual(primero['nombre_comercial'], 'ACADIAN Liquid Concentrate (pH4)')
        self.assertEqual(primero['fabricante'], 'Acadian Seaplants Limited')
        self.assertEqual(primero['funcion'], 'Fertilizante, Bioestimulante')
        # Las 756 filas son registros distintos (Rhizo-Flo aparece dos veces pero con otra sustancia activa).
        claves = {huella(r['nombre_comercial'], r['fabricante'], r['sustancia_activa']) for r in datos.registros}
        self.assertEqual(len(claves), 756)

    def test_fechas_de_excel_y_clasificacion_de_la_funcion(self):
        self.assertEqual(fecha_excel('46335'), date(2026, 11, 9))
        self.assertEqual(fecha_excel('06-12-2024'), date(2024, 12, 6))
        self.assertIsNone(fecha_excel(''))
        self.assertIsNone(fecha_excel('no es fecha'))
        casos = {
            'Fertilizante': Clasificacion.FERTILIZANTE,
            'Ferilizante': Clasificacion.FERTILIZANTE,
            'Fertilizante, Micronutrientes': Clasificacion.FERTILIZANTE,
            'Bioestimulante': Clasificacion.BIOESTIMULANTE,
            'Bioestimulamte': Clasificacion.BIOESTIMULANTE,
            'Biestimulante': Clasificacion.BIOESTIMULANTE,
            'Fertilizante, Bioestimulante': Clasificacion.FERTILIZANTE_BIOESTIMULANTE,
            'Biofertilizante Bioestimulante': Clasificacion.BIOFERTILIZANTE,
            'Inoculante para soya': Clasificacion.BIOFERTILIZANTE,
            'Acondicionador de suelos': Clasificacion.ENMIENDA,
            'Regulador de pH': Clasificacion.OTRO,
        }
        for funcion, esperado in casos.items():
            self.assertEqual(clasificar(funcion), esperado, funcion)


class ImportacionTests(TestCase):
    def importar(self, *args):
        salida = StringIO()
        call_command('importar_lista_sag', str(XLSX), *args, stdout=salida)
        return salida.getvalue()

    def test_simulacion_no_escribe_y_aplicar_importa_sin_dosis_ni_verificacion(self):
        salida = self.importar()
        self.assertIn('Simulación', salida)
        self.assertEqual(RegistroListaSAG.objects.count(), 0)
        self.assertEqual(Insumo.objects.count(), 0)

        self.importar('--aplicar')
        self.assertEqual(RegistroListaSAG.objects.count(), 756)
        self.assertEqual(Insumo.objects.count(), 756)
        importados = Insumo.objects.filter(registro_sag__isnull=False)
        # La lista no trae dosis: nada queda con dosis ni como verificado.
        self.assertFalse(importados.filter(dosis_hectarea__isnull=False).exists())
        self.assertFalse(importados.filter(dosis_verificada=True).exists())
        self.assertFalse(importados.exclude(estado_verificacion='unverified').exists())
        self.assertEqual(
            dict(RegistroListaSAG.objects.values_list('clasificacion').annotate(n=Count('pk')).order_by()),
            {
                'fertilizante': 325,
                'fertilizante_bioestimulante': 179,
                'bioestimulante': 155,
                'biofertilizante': 56,
                'enmienda': 30,
                'otro': 11,
            },
        )
        acadian = Insumo.objects.get(nombre='ACADIAN Liquid Concentrate (pH4)')
        self.assertEqual(acadian.fabricante, 'Acadian Seaplants Limited')
        self.assertEqual(acadian.composicion_nutricional, 'Extractos de Algas')
        self.assertIn('lista-de-fertilizantes-y-bioestimulantes', acadian.fuente_url)
        self.assertIn('09-11-2026', acadian.fuente_nombre)
        self.assertEqual(acadian.registro_sag.fecha_lista, date(2026, 11, 9))
        self.assertEqual(acadian.registro_sag.archivo_fuente, XLSX.name)
        self.assertEqual(RegistroListaSAG.objects.exclude(fecha_autorizacion=None).count(), 110)

    def test_reimportar_no_duplica(self):
        self.importar('--aplicar')
        salida = self.importar('--aplicar')
        self.assertIn('Insumos nuevos: 0', salida)
        self.assertIn('sin cambios: 756', salida)
        self.assertEqual(Insumo.objects.count(), 756)

    def test_se_vincula_a_un_insumo_existente_sin_modificarlo_y_nunca_a_una_semilla(self):
        fert = TipoInsumo.objects.create(nombre='Fertilizante')
        semilla = TipoInsumo.objects.create(nombre='Semilla')
        curado = Insumo.objects.create(
            tipo=fert, nombre='Alga Plus', fabricante='Fabricante SpA', descripcion='Ficha del administrador'
        )
        Insumo.objects.create(tipo=semilla, nombre='Semilla X', fabricante='Fabricante SpA')
        resultado = importar_lista(lista(fila('Alga Plus'), fila('Semilla X')), 'lista.xlsx')
        self.assertEqual((resultado.vinculados_a_existentes, resultado.creados), (1, 1))
        curado.refresh_from_db()
        self.assertEqual(curado.descripcion, 'Ficha del administrador')
        self.assertFalse(curado.registro_sag.insumo_creado_por_importacion)
        self.assertEqual(Insumo.objects.filter(nombre='Semilla X').count(), 2)  # la semilla no se mezcla
        self.assertFalse(Insumo.objects.filter(tipo=semilla, registro_sag__isnull=False).exists())

    def test_reimportar_respeta_ediciones_y_marca_los_retirados_sin_borrarlos(self):
        importar_lista(lista(fila('Alga Plus'), fila('Raíz Activa', funcion='Fertilizante')), 'v1.xlsx')
        alga = Insumo.objects.get(nombre='Alga Plus')
        alga.descripcion = 'Editado por el administrador'
        alga.dosis_hectarea = Decimal('2')
        alga.unidad_dosis = 'L/ha'
        alga.save()
        resultado = importar_lista(
            lista(fila('Alga Plus', observaciones='Nueva observación'), fecha=date(2027, 1, 15)), 'v2.xlsx'
        )
        self.assertEqual(resultado.retirados, 1)
        alga.refresh_from_db()
        self.assertEqual(alga.descripcion, 'Editado por el administrador')
        self.assertEqual(alga.dosis_hectarea, Decimal('2'))
        self.assertIn('15-01-2027', alga.fuente_nombre)  # este campo no se había editado: se actualiza
        self.assertEqual(alga.registro_sag.observaciones, 'Nueva observación')
        retirado = RegistroListaSAG.objects.get(nombre_comercial='Raíz Activa')
        self.assertFalse(retirado.en_lista_vigente)
        self.assertTrue(Insumo.objects.filter(nombre='Raíz Activa').exists())

    def test_archivo_sin_las_columnas_esperadas_se_rechaza(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('importar_lista_sag', str(PROYECTO / 'data' / 'cultivos.csv'), stdout=StringIO())


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('agricultora', email='a@example.com', password=PASSWORD)
        cls.crop = Cultivo.objects.create(
            nombre='Trigo',
            densidad_siembra=Decimal('180'),
            unidad_siembra='kg',
            densidad_fuente='INIA',
            densidad_url='https://www.inia.cl/',
            densidad_verificada=date(2026, 10, 1),
        )
        cls.predio = Predio.objects.create(propietario=cls.user, nombre='Fundo', ubicacion='Melipilla', superficie_ha=10)
        cls.plot = Potrero.objects.create(predio=cls.predio, nombre='Norte', superficie_ha=Decimal('3.5'), cultivo=cls.crop)
        cls.fert = TipoInsumo.objects.create(nombre='Fertilizante')
        cls.semilla = TipoInsumo.objects.create(nombre='Semilla')
        cls.herbicida = TipoInsumo.objects.create(nombre='Herbicida')
        cls.urea = Insumo.objects.create(
            tipo=cls.fert,
            nombre='Urea verificada',
            dosis_hectarea=Decimal('150'),
            unidad_dosis='kg/ha',
            dosis_verificada=True,
            dosis_fuente='Ficha técnica',
            estado_verificacion='verified',
            fuente_nombre='Fabricante',
            fuente_url='https://example.com/urea',
            fecha_verificacion=date(2026, 10, 1),
        )
        cls.foliar = Insumo.objects.create(
            tipo=cls.fert,
            nombre='Foliar verificado',
            dosis_hectarea=Decimal('500'),
            unidad_dosis='mL/ha',
            dosis_verificada=True,
            dosis_fuente='Etiqueta',
            estado_verificacion='verified',
            fuente_nombre='Fabricante',
            fuente_url='https://example.com/foliar',
            fecha_verificacion=date(2026, 10, 1),
        )
        cls.semilla_trigo = Insumo.objects.create(tipo=cls.semilla, nombre='Semilla trigo', cultivo=cls.crop)
        importar_lista(
            lista(
                fila('Alga Plus', funcion='Bioestimulante'),
                fila('Humus Max', sustancia='Ácidos húmicos', funcion='Fertilizante, Bioestimulante'),
                fila('Cal Agrícola', sustancia='Carbonato de calcio', funcion='Enmienda'),
                fila('Sin Composición', sustancia='', funcion='Fertilizante'),
            ),
            'lista.xlsx',
        )
        cls.alga = Insumo.objects.get(nombre='Alga Plus')
        cls.humus = Insumo.objects.get(nombre='Humus Max')
        cls.sin_composicion = Insumo.objects.get(nombre='Sin Composición')

    def setUp(self):
        self.client.login(username='agricultora', password=PASSWORD)

    def calcular(self, **datos):
        base = {'plot_id': self.plot.pk, 'area': '2', 'crop': 'Trigo'}
        return ejecutar_calculo(self.user, {**base, **datos}).resultado

    def post(self, **datos):
        base = {'plot_id': self.plot.pk, 'area': '2', 'crop': 'Trigo'}
        return self.client.post(reverse('calculate'), json.dumps({**base, **datos}), content_type='application/json')


class CatalogoTests(Base):
    def test_productos_sag_y_anteriores_aparecen_con_contadores(self):
        response = self.client.get(reverse('productos'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['product_total'], 7)
        conteos = {key: count for key, _label, count in response.context['product_counts']}
        self.assertEqual(conteos['biostimulant'], 1)
        self.assertEqual(conteos['fertilizer'], 5)  # urea, foliar, humus, sin composición y la enmienda
        self.assertEqual(conteos['seed'], 1)
        html = response.content.decode()
        for nombre in ('Urea verificada', 'Semilla trigo', 'Alga Plus', 'Humus Max'):
            self.assertIn(nombre, html)
        self.assertIn('Lista SAG orgánica', html)
        self.assertIn('Bioestimulantes', html)

    def test_filtros_y_busqueda(self):
        def nombres(**params):
            response = self.client.get(reverse('productos'), params)
            return sorted(item.nombre for item in response.context['products_page'])

        self.assertEqual(nombres(categoria='biostimulant'), ['Alga Plus'])
        self.assertEqual(nombres(sag='no_incluidos'), ['Foliar verificado', 'Semilla trigo', 'Urea verificada'])
        self.assertEqual(len(nombres(sag='incluidos')), 4)
        self.assertEqual(nombres(funcion_sag='fertilizante_bioestimulante'), ['Humus Max'])
        self.assertEqual(nombres(q='húmicos'), ['Humus Max'])
        self.assertEqual(nombres(q='fabricante spa'), ['Alga Plus', 'Cal Agrícola', 'Humus Max', 'Sin Composición'])
        self.assertEqual(nombres(solo_calculables='1'), ['Foliar verificado', 'Urea verificada'])
        self.assertEqual(nombres(estado='verified', orden='categoria'), ['Foliar verificado', 'Urea verificada'])
        response = self.client.get(reverse('productos'), {'orden': 'verificacion'})
        self.assertEqual([p.nombre for p in response.context['products_page']][:2], ['Foliar verificado', 'Urea verificada'])

    def test_paginacion_y_consultas_constantes_con_cientos_de_productos(self):
        with CaptureQueriesContext(connection) as pocos:
            self.client.get(reverse('productos'))
        importar_lista(lista(*[fila(f'Producto {i:03d}') for i in range(60)]), 'grande.xlsx')
        with CaptureQueriesContext(connection) as muchos:
            response = self.client.get(reverse('productos'), {'pagina': 2})
        self.assertEqual(len(pocos), len(muchos))  # no hay una consulta por tarjeta
        self.assertEqual(len(response.context['products_page'].object_list), 18)
        self.assertEqual(response.context['product_count'], 67)

    def test_ficha_sag_muestra_procedencia_y_estado_de_dosis(self):
        html = self.client.get(reverse('producto_detail', args=[self.humus.pk])).content.decode()
        for texto in (
            'Lista SAG de fertilizantes y bioestimulantes',
            'Fertilizante, Bioestimulante',
            'Ácidos húmicos',
            'Fabricante SpA',
            'Sin dosis documentada',
            'Dosis no verificada',
            'no informa dosis',
            'Calcular con mi dosis',
            'herramienta=llevar&amp;tipo=insumos&amp;insumo=',
        ):
            self.assertIn(texto, html)
        self.assertNotIn('<dt>N</dt>', html)  # la lista no trae N-P-K: no se muestran filas vacías
        pdf = self.client.get(reverse('producto_pdf', args=[self.humus.pk]))
        self.assertEqual(pdf.status_code, 200)

    def test_ficha_de_producto_sin_composicion_y_ficha_anterior_intactas(self):
        html = self.client.get(reverse('producto_detail', args=[self.sin_composicion.pk])).content.decode()
        self.assertIn('No indicada', html)
        anterior = self.client.get(reverse('producto_detail', args=[self.urea.pk])).content.decode()
        self.assertIn('Calcular cantidad', anterior)
        self.assertNotIn('Lista SAG de fertilizantes', anterior)

    def test_busqueda_general_encuentra_productos_sag(self):
        response = self.client.get(reverse('busqueda'), {'q': 'Humus'})
        self.assertContains(response, 'Humus Max')


class DatosPorPantallaTests(Base):
    def test_agro_catalog_no_carga_los_importados_de_la_lista(self):
        payload = catalog_payload()
        nombres = {item['name'] for item in payload['insumos']}
        self.assertEqual(nombres, {'Urea verificada', 'Foliar verificado', 'Semilla trigo'})
        self.assertNotIn('Alga Plus', self.client.get(reverse('decisiones')).content.decode())

    def test_un_insumo_curado_vinculado_a_la_lista_si_va_en_el_catalogo_con_su_procedencia(self):
        self.urea.fabricante = 'Fabricante'
        self.urea.save()
        resultado = importar_lista(lista(fila('Urea verificada', fabricante='Fabricante')), 'lista.xlsx')
        self.assertEqual((resultado.vinculados_a_existentes, resultado.creados), (1, 0))
        urea = next(item for item in catalog_payload()['insumos'] if item['name'] == 'Urea verificada')
        self.assertIsNotNone(urea['sag'])
        self.assertTrue(urea['doseVerified'])  # la dosis verificada del administrador se conserva

    def test_buscador_de_las_calculadoras(self):
        url = reverse('productos_buscar')
        self.assertEqual(self.client.get(url, {'q': 'a'}).json()['results'], [])
        data = self.client.get(url, {'q': 'humus'}).json()
        self.assertEqual([item['name'] for item in data['results']], ['Humus Max'])
        item = data['results'][0]
        self.assertEqual(item['category'], 'fertilizer')
        self.assertIsNone(item['dose'])
        self.assertFalse(item['doseVerified'])
        self.assertEqual(item['sag']['function'], 'Fertilizante, Bioestimulante')
        self.assertEqual(self.client.get(url, {'id': self.alga.pk}).json()['results'][0]['category'], 'biostimulant')
        # Ni semillas ni productos curados (esos ya vienen en la página).
        self.assertEqual(self.client.get(url, {'q': 'semilla'}).json()['results'], [])
        self.assertEqual(self.client.get(url, {'q': 'urea'}).json()['results'], [])
        self.assertEqual(self.client.get(url, {'id': 'x'}).json()['results'], [])
        importar_lista(lista(*[fila(f'Alga {i:02d}') for i in range(30)]), 'grande.xlsx')
        grande = self.client.get(url, {'q': 'alga'}).json()
        self.assertEqual((len(grande['results']), grande['more']), (20, True))
        self.client.logout()
        self.assertEqual(self.client.get(url, {'q': 'humus'}).status_code, 302)

    def test_formulario_de_actividad_solo_ofrece_el_producto_sag_elegido(self):
        form = ActividadAgricolaForm(user=self.user)
        self.assertNotIn(self.alga, form.fields['insumo'].queryset)
        self.assertIn(self.urea, form.fields['insumo'].queryset)
        elegido = ActividadAgricolaForm(user=self.user, initial={'insumo': self.alga.pk})
        self.assertIn(self.alga, elegido.fields['insumo'].queryset)
        self.assertNotIn(self.humus, elegido.fields['insumo'].queryset)
        enviado = ActividadAgricolaForm({'insumo': str(self.humus.pk)}, user=self.user)
        self.assertIn(self.humus, enviado.fields['insumo'].queryset)


class CalculadoraDeInsumosTests(Base):
    def test_categoria_bioestimulante(self):
        self.assertEqual(category_key('Bioestimulante'), 'biostimulant')
        self.assertEqual(category_key('Enmienda'), 'fertilizer')

    def test_dosis_documentada_del_catalogo(self):
        r = self.calcular(operation='dose', calculation_type='insumos', product_id=self.urea.pk, dose='150', unit='kg/ha')
        self.assertEqual((r['amount'], r['unit'], r['origin'], r['sag_list']), (300.0, 'kg', 'documented', False))

    def test_mililitros_y_litros(self):
        r = self.calcular(operation='dose', calculation_type='insumos', product_id=self.foliar.pk, dose='500', unit='mL/ha')
        self.assertEqual((r['amount'], r['unit'], r['origin']), (1000.0, 'mL', 'documented'))
        # La misma dosis escrita en L/ha (0,5) es la dosis documentada: se compara convertida.
        r = self.calcular(operation='dose', calculation_type='insumos', product_id=self.foliar.pk, dose='0.5', unit='L/ha')
        self.assertEqual((r['amount'], r['unit'], r['origin']), (1.0, 'L', 'documented'))

    def test_gramos_y_superficie_decimal(self):
        r = self.calcular(
            operation='dose', calculation_type='insumos', product_id=self.urea.pk, dose='150000', unit='g/ha', area='1.25'
        )
        self.assertEqual((r['amount'], r['unit'], r['origin']), (187500.0, 'g', 'documented'))
        r = self.calcular(operation='dose', calculation_type='insumos', dose='2.5', unit='kg/ha', area='0.3333')
        self.assertAlmostEqual(r['amount'], 0.83325)

    def test_unidades_incompatibles_no_se_calculan(self):
        with self.assertRaisesMessage(ErrorCalculo, 'no es compatible'):
            self.calcular(operation='dose', calculation_type='insumos', product_id=self.urea.pk, dose='150', unit='L/ha')
        self.assertIsNone(convertir(Decimal('1'), 'kg/ha', 'L/ha'))
        self.assertEqual(convertir(Decimal('500'), 'g', 'kg/ha'), Decimal('0.5'))

    def test_producto_sag_sin_dosis_se_calcula_como_dato_manual(self):
        for producto in (self.alga, self.humus, self.sin_composicion):
            r = self.calcular(operation='dose', calculation_type='insumos', product_id=producto.pk, dose='3', unit='L/ha')
            self.assertEqual((r['amount'], r['unit'], r['origin']), (6.0, 'L', 'simulation'))
            self.assertTrue(r['simulation'])
            self.assertTrue(r['sag_list'])
            self.assertFalse(r['verified'])
        respuesta = self.post(operation='dose', calculation_type='insumos', product_id=self.alga.pk, unit='L/ha')
        self.assertEqual(respuesta.status_code, 400)  # sin dosis no hay valor inventado: se exige una

    def test_semillas_seleccionables_y_no_mezcladas_con_insumos(self):
        r = self.calcular(operation='dose', calculation_type='semillas', dose='180', unit='kg/ha')
        self.assertEqual((r['amount'], r['origin']), (360.0, 'documented'))
        r = self.calcular(
            operation='dose', calculation_type='semillas', product_id=self.semilla_trigo.pk, dose='200', unit='kg/ha'
        )
        self.assertEqual(r['origin'], 'simulation')
        with self.assertRaisesMessage(ErrorCalculo, 'Selecciona una semilla'):
            self.calcular(operation='dose', calculation_type='semillas', product_id=self.alga.pk, dose='2', unit='L/ha')
        with self.assertRaisesMessage(ErrorCalculo, 'propio cálculo'):
            self.calcular(
                operation='dose', calculation_type='insumos', product_id=self.semilla_trigo.pk, dose='2', unit='kg/ha'
            )


class MeAlcanzaTests(Base):
    def cobertura(self, **datos):
        return self.calcular(operation='coverage', calculation_type='coverage', **datos)

    def test_falta_producto(self):
        r = self.cobertura(product_id=self.urea.pk, dose='150', unit='kg/ha', stock='200', area='2')
        self.assertEqual((r['needed'], r['stock'], r['missingQty'], r['surplus']), (300.0, 200.0, 100.0, 0.0))
        self.assertAlmostEqual(r['coveredHa'], 4 / 3)
        self.assertAlmostEqual(r['coverableHa'], 4 / 3)
        self.assertAlmostEqual(r['missingHa'], 2 - 4 / 3)

    def test_sobra_producto_y_hectareas_que_alcanzaria(self):
        r = self.cobertura(product_id=self.urea.pk, dose='150', unit='kg/ha', stock='450', area='1.5')
        self.assertEqual((r['needed'], r['missingQty'], r['surplus'], r['coveredHa'], r['coverableHa']), (225.0, 0.0, 225.0, 1.5, 3.0))
        self.assertEqual(r['percent'], 100.0)

    def test_alcanza_justo(self):
        r = self.cobertura(product_id=self.urea.pk, dose='150', unit='kg/ha', stock='300')
        self.assertEqual((r['missingQty'], r['surplus'], r['coveredHa']), (0.0, 0.0, 2.0))

    def test_cantidad_disponible_en_otra_unidad_compatible(self):
        r = self.cobertura(product_id=self.urea.pk, dose='150', unit='kg/ha', stock='150000', stock_unit='g')
        self.assertEqual((r['stock'], r['stockEntered'], r['stockUnit'], r['missingQty']), (150.0, 150000.0, 'g', 150.0))
        r = self.cobertura(product_id=self.foliar.pk, dose='500', unit='mL/ha', stock='1.5', stock_unit='L')
        self.assertEqual((r['stock'], r['unit'], r['surplus']), (1500.0, 'mL', 500.0))

    def test_unidades_incompatibles_y_valores_invalidos(self):
        with self.assertRaisesMessage(ErrorCalculo, 'no son unidades compatibles'):
            self.cobertura(product_id=self.urea.pk, dose='150', unit='kg/ha', stock='10', stock_unit='L')
        with self.assertRaises(ErrorCalculo):
            self.cobertura(dose='150', unit='kg/ha', stock='-1')
        with self.assertRaises(ErrorCalculo):
            self.cobertura(dose='0', unit='kg/ha', stock='1')
        with self.assertRaises(ErrorCalculo):
            self.cobertura(dose='1', unit='kg/ha', stock='1', area='3.6')  # supera las 3,5 ha del potrero

    def test_producto_sag_con_dosis_manual(self):
        r = self.cobertura(product_id=self.humus.pk, dose='4', unit='L/ha', stock='5', stock_unit='L')
        self.assertEqual((r['needed'], r['missingQty'], r['origin'], r['sag_list']), (8.0, 3.0, 'simulation', True))

    def test_semilla_con_densidad_del_cultivo(self):
        r = self.cobertura(dose_basis='crop_density', dose='180', unit='kg/ha', stock='500')
        self.assertEqual((r['needed'], r['surplus'], r['origin']), (360.0, 140.0, 'documented'))
        with self.assertRaises(ErrorCalculo):
            self.cobertura(dose_basis='crop_density', product_id=self.urea.pk, dose='150', unit='kg/ha', stock='1')

    def test_por_http_se_guarda_con_la_unidad_de_lo_disponible(self):
        respuesta = self.post(
            operation='coverage', calculation_type='coverage', product_id=self.alga.pk, dose='2', unit='L/ha',
            stock='3000', stock_unit='mL',
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['missingQty'], 1.0)
        self.assertEqual(CalculoAgricola.objects.get().entrada['stock_unit'], 'mL')


class InterfazTests(Base):
    def test_decisiones_carga_el_buscador_sag_antes_de_las_calculadoras(self):
        html = self.client.get('/decisiones/?herramienta=alcanza&tipo=insumos').content.decode()
        self.assertIn(f'data-sag-search-url="{reverse("productos_buscar")}"', html)
        self.assertIn('id="calc-sag-search"', html)
        self.assertIn('data-role="sag-search"', html)
        self.assertIn('name="stock_unit"', html)
        self.assertIn('name="unit"', html)
        for script in ('js/calc-panel.js', 'js/tool-flow.js', 'js/decisiones.js'):
            self.assertLess(html.index('js/catalogo-sag.js'), html.index(script))

    def test_scripts_sin_errores_de_sintaxis(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node no está instalado')
        for nombre in ('catalogo-sag.js', 'calc-panel.js', 'decisiones.js', 'tool-flow.js', 'actividades.js'):
            resultado = subprocess.run([node, '--check', str(STATIC / 'js' / nombre)], capture_output=True, text=True)
            self.assertEqual(resultado.returncode, 0, resultado.stderr)


def filas_base(sin=()):
    """Las mismas cuatro filas que importa Base, menos las indicadas (simula una lista nueva sin ellas)."""
    filas = [
        fila('Alga Plus', funcion='Bioestimulante'),
        fila('Humus Max', sustancia='Ácidos húmicos', funcion='Fertilizante, Bioestimulante'),
        fila('Cal Agrícola', sustancia='Carbonato de calcio', funcion='Enmienda'),
        fila('Sin Composición', sustancia='', funcion='Fertilizante'),
    ]
    return [item for item in filas if item['nombre_comercial'] not in sin]


class VigenciaSAGEnCalculadorasTests(Base):
    """Un producto que deja de figurar en la lista SAG queda como registro histórico en las calculadoras."""

    def retirar_alga(self):
        resultado = importar_lista(lista(*filas_base(sin=('Alga Plus',)), fecha=date(2027, 1, 15)), 'v2.xlsx')
        self.assertEqual(resultado.retirados, 1)

    def dosis(self, producto):
        return self.post(operation='dose', calculation_type='insumos', product_id=producto.pk, dose='2', unit='L/ha').json()

    def cobertura(self, producto):
        return self.post(
            operation='coverage', calculation_type='coverage', product_id=producto.pk, dose='2', unit='L/ha',
            stock='1', stock_unit='L',
        ).json()

    def buscar(self, **params):
        return self.client.get(reverse('productos_buscar'), params).json()['results']

    # 1. Producto presente en la lista SAG
    def test_producto_en_la_lista_vigente(self):
        encontrado = self.buscar(q='alga')
        self.assertEqual([(item['name'], item['sag']['listed']) for item in encontrado], [('Alga Plus', True)])
        for resultado in (self.dosis(self.alga), self.cobertura(self.alga)):
            self.assertEqual((resultado['sag_status'], resultado['sag_list']), ('vigente', True))
            self.assertEqual(resultado['origin'], 'simulation')

    # 2. Producto que desaparece de una nueva importación
    def test_producto_que_desaparece_queda_historico_y_no_se_ofrece_al_buscar(self):
        self.retirar_alga()
        registro = RegistroListaSAG.objects.get(insumo=self.alga)
        self.assertFalse(registro.en_lista_vigente)
        self.assertEqual(registro.fecha_lista, date(2026, 11, 9))  # conserva la última lista en que figuró
        self.assertTrue(Insumo.objects.filter(pk=self.alga.pk).exists())
        self.assertEqual(self.buscar(q='alga'), [])  # el buscador de las calculadoras no lo ofrece
        self.assertEqual([item['name'] for item in self.buscar(q='humus')], ['Humus Max'])
        # Por id (enlace desde su ficha) sí llega, marcado como histórico.
        self.assertEqual([item['sag']['listed'] for item in self.buscar(id=self.alga.pk)], [False])
        # El catálogo general lo conserva con su condición histórica.
        tarjeta = self.client.get(reverse('productos'), {'q': 'Alga Plus'}).content.decode()
        self.assertIn('Ya no figura en la lista SAG', tarjeta)
        incluidos = self.client.get(reverse('productos'), {'sag': 'incluidos'}).context['products_page']
        self.assertNotIn('Alga Plus', [item.nombre for item in incluidos])
        ficha = self.client.get(reverse('producto_detail', args=[self.alga.pk])).content.decode()
        self.assertIn('No figura en la última lista importada', ficha)
        self.assertIn('Fuera de la lista SAG', ficha)

    # 3. Producto histórico seleccionado en las dos calculadoras
    def test_producto_historico_en_ambas_calculadoras(self):
        self.retirar_alga()
        dosis, cobertura = self.dosis(self.alga), self.cobertura(self.alga)
        for resultado in (dosis, cobertura):
            self.assertEqual((resultado['sag_status'], resultado['sag_list']), ('historico', False))
            self.assertEqual(resultado['origin'], 'simulation')
        self.assertEqual(dosis['amount'], 4.0)
        self.assertEqual((cobertura['needed'], cobertura['missingQty']), (4.0, 3.0))
        guardado = CalculoAgricola.objects.order_by('-pk').first()
        self.assertEqual((guardado.resultado['sag_status'], guardado.resultado['sag_list']), ('historico', False))

    # 4. Producto sin registro SAG
    def test_producto_sin_registro_sag_no_se_atribuye_a_la_lista(self):
        for resultado in (
            self.post(operation='dose', calculation_type='insumos', product_id=self.urea.pk, dose='150', unit='kg/ha').json(),
            self.post(
                operation='coverage', calculation_type='coverage', product_id=self.urea.pk, dose='150', unit='kg/ha',
                stock='10',
            ).json(),
            self.post(operation='coverage', calculation_type='coverage', dose='3', unit='kg/ha', stock='1').json(),
        ):
            self.assertEqual((resultado['sag_status'], resultado['sag_list']), ('', False))
        urea = next(item for item in catalog_payload()['insumos'] if item['name'] == 'Urea verificada')
        self.assertIsNone(urea['sag'])

    # 5. Producto que vuelve a aparecer en una importación posterior
    def test_producto_que_vuelve_a_aparecer(self):
        self.retirar_alga()
        resultado = importar_lista(lista(*filas_base(), fecha=date(2027, 3, 1)), 'v3.xlsx')
        self.assertEqual((resultado.creados, resultado.retirados), (0, 0))
        registro = RegistroListaSAG.objects.get(insumo=self.alga)
        self.assertTrue(registro.en_lista_vigente)
        self.assertEqual(registro.fecha_lista, date(2027, 3, 1))
        self.assertEqual(Insumo.objects.filter(nombre='Alga Plus').count(), 1)  # mismo insumo, sin duplicar
        self.assertEqual([item['sag']['listed'] for item in self.buscar(q='alga')], [True])
        self.assertEqual(self.dosis(self.alga)['sag_status'], 'vigente')

    # 6. Cambio entre productos con distinto estado de vigencia
    def test_cambio_entre_productos_con_distinto_estado(self):
        self.retirar_alga()
        secuencia = [(self.humus, 'vigente'), (self.alga, 'historico'), (self.urea, ''), (self.humus, 'vigente')]
        for producto, esperado in secuencia:
            es_urea = producto == self.urea
            for operacion, tipo in (('dose', 'insumos'), ('coverage', 'coverage')):
                datos = {
                    'operation': operacion,
                    'calculation_type': tipo,
                    'product_id': producto.pk,
                    'dose': '150' if es_urea else '2',
                    'unit': 'kg/ha' if es_urea else 'L/ha',
                }
                if operacion == 'coverage':
                    datos['stock'] = '1'
                resultado = self.post(**datos).json()
                self.assertEqual(resultado['sag_status'], esperado, (producto.nombre, operacion))
                self.assertEqual(resultado['sag_list'], esperado == 'vigente')

    def test_textos_de_estado_comunes_en_las_calculadoras(self):
        comun = (STATIC / 'js' / 'catalogo-sag.js').read_text(encoding='utf-8')
        for texto in (
            'lista SAG vigente',
            'registro SAG histórico',
            'Registro histórico: ya no figura en la lista SAG orgánica vigente',
            'Lista SAG orgánica vigente (sin dosis documentada)',
            'Registro SAG histórico: ya no figura en la lista vigente',
        ):
            self.assertIn(texto, comun)
        for nombre in ('calc-panel.js', 'decisiones.js', 'tool-flow.js'):
            codigo = (STATIC / 'js' / nombre).read_text(encoding='utf-8')
            self.assertIn('AgroCatalogoSAG.estado', codigo, nombre)
            self.assertNotIn("'Lista SAG orgánica (sin dosis documentada)'", codigo, nombre)  # texto anterior
        for nombre in ('calc-panel.js', 'decisiones.js'):
            self.assertIn('AgroCatalogoSAG.resultado(data.sag_status)', (STATIC / 'js' / nombre).read_text(encoding='utf-8'))


class ListaOficialConservadaTests(TestCase):
    def test_retirar_y_reincorporar_no_borra_ninguno_de_los_756(self):
        datos = leer_lista_sag(XLSX)
        importar_lista(datos, XLSX.name)
        sin_uno = ListaSAG(date(2027, 1, 15), datos.registros[1:], datos.columnas, 0)
        self.assertEqual(importar_lista(sin_uno, 'futura.xlsx').retirados, 1)
        self.assertEqual((RegistroListaSAG.objects.count(), Insumo.objects.count()), (756, 756))
        self.assertEqual(RegistroListaSAG.objects.filter(en_lista_vigente=False).count(), 1)
        importar_lista(datos, XLSX.name)
        self.assertEqual((RegistroListaSAG.objects.count(), Insumo.objects.count()), (756, 756))
        self.assertEqual(RegistroListaSAG.objects.filter(en_lista_vigente=True).count(), 756)
