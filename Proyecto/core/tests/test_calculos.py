"""Pruebas de la lógica de la calculadora (``core/calculos.py``) y de su pantalla.

Cubren las fórmulas, la precisión decimal (0,1496 ha × 100 kg/ha = 14,96 kg, sin redondear la superficie), la
clasificación del origen de la dosis (documentada o simulación), las validaciones y que el resultado se guarde y
llegue a Reportes.
"""

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.calculos import (
    ErrorCalculo,
    cantidad_por_dosis,
    clasificar_origen,
    dosis_semilla_por_poblacion,
    ejecutar_calculo,
    formato_superficie,
    numero_positivo,
    producto_por_nutriente,
)
from core.models import CalculoAgricola, Cultivo, Potrero, Predio

User = get_user_model()


class FormulasTests(TestCase):
    def test_cantidad_por_dosis_conserva_todos_los_decimales(self):
        self.assertEqual(cantidad_por_dosis(Decimal('0.1496'), Decimal('100')), Decimal('14.96'))
        self.assertEqual(cantidad_por_dosis(Decimal('2.5'), Decimal('0.125')), Decimal('0.3125'))

    def test_dosis_por_poblacion(self):
        # Con 100 % de germinación y pureza: 250 plantas/m² × 40 g ÷ 100 = 100 kg/ha.
        self.assertEqual(dosis_semilla_por_poblacion(Decimal(250), Decimal(40), Decimal(100), Decimal(100)), 100)
        # Ejemplo del docstring: 300 × 45 ÷ (100 × 0,90 × 0,98) = 153,06 kg/ha.
        resultado = dosis_semilla_por_poblacion(Decimal(300), Decimal(45), Decimal(90), Decimal(98))
        self.assertAlmostEqual(float(resultado), 153.0612, places=4)
        # Menos germinación → más semilla para la misma población.
        menos = dosis_semilla_por_poblacion(Decimal(300), Decimal(45), Decimal(80), Decimal(98))
        self.assertGreater(menos, resultado)

    def test_producto_por_nutriente(self):
        # Urea (46 % N): 46 kg/ha de N requieren 100 kg/ha de producto.
        self.assertEqual(producto_por_nutriente(Decimal(46), Decimal(46)), 100)

    def test_formato_superficie_muestra_hasta_cuatro_decimales(self):
        self.assertEqual(formato_superficie(Decimal('0.1496')), '0,1496')
        self.assertEqual(formato_superficie(Decimal('3.2000')), '3,2')
        self.assertEqual(formato_superficie(Decimal('1234.5')), '1.234,5')
        self.assertEqual(formato_superficie(Decimal('12')), '12')

    def test_numero_positivo_rechaza_valores_invalidos(self):
        for valor in ('abc', '', None, 'NaN', 'Infinity', '-1', '0', '1e30'):
            with self.subTest(valor=valor), self.assertRaises(ErrorCalculo):
                numero_positivo(valor, 'La dosis')
        self.assertEqual(numero_positivo('0', 'El stock', permitir_cero=True), 0)
        self.assertEqual(numero_positivo('0.1496', 'La superficie'), Decimal('0.1496'))


class OrigenDeLaDosisTests(TestCase):
    referencia = {'valor': Decimal('100'), 'unidad': 'kg/ha', 'respaldada': True, 'fuente': 'INIA', 'url': 'https://x',
                  'fecha': '2026-01-10'}

    def test_sin_referencia_es_simulacion(self):
        origen, procedencia = clasificar_origen(Decimal('100'), 'kg/ha', None)
        self.assertEqual((origen, procedencia['verified']), ('simulation', False))

    def test_valor_distinto_al_del_catalogo_es_ajuste(self):
        self.assertEqual(clasificar_origen(Decimal('120'), 'kg/ha', self.referencia)[0], 'adjusted')
        self.assertEqual(clasificar_origen(Decimal('100'), 'g/ha', self.referencia)[0], 'adjusted')

    def test_valor_del_catalogo_sin_respaldo_no_es_documentado(self):
        sin_respaldo = {**self.referencia, 'respaldada': False}
        origen, procedencia = clasificar_origen(Decimal('100'), 'kg', sin_respaldo)
        self.assertEqual((origen, procedencia['verified']), ('catalog', False))

    def test_valor_del_catalogo_con_fuente_es_documentado(self):
        origen, procedencia = clasificar_origen(Decimal('100'), 'kg/ha', self.referencia)
        self.assertEqual(origen, 'documented')
        self.assertEqual((procedencia['source'], procedencia['source_date']), ('INIA', '2026-01-10'))


class CalculoBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.trigo = Cultivo.objects.create(nombre='Trigo')
        predio = Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='Melipilla', superficie_ha=5)
        self.potrero = Potrero.objects.create(
            predio=predio, nombre='Potrero chico', superficie_ha=Decimal('0.1496'), perimetro=[], cultivo=self.trigo
        )

    def datos(self, **valores):
        return {
            'operation': 'dose',
            'calculation_type': 'semillas',
            'plot_id': self.potrero.pk,
            'area': '0.1496',
            'dose': '100',
            'unit': 'kg/ha',
            'crop': 'Trigo',
            **valores,
        }


class EjecutarCalculoTests(CalculoBase):
    def test_semillas_con_dosis_manual_es_una_simulacion_precisa(self):
        calculo = ejecutar_calculo(self.user, self.datos())
        r = calculo.resultado
        self.assertEqual((r['amount'], r['unit'], r['dose'], r['area']), (14.96, 'kg', 100.0, 0.1496))
        self.assertEqual((r['origin'], r['simulation'], r['verified']), ('simulation', True, False))
        self.assertEqual(calculo.entrada['area_ha'], '0.1496')  # se guarda el valor completo, no 0.15

    def test_densidad_documentada_del_cultivo(self):
        self.trigo.densidad_siembra = Decimal('180')
        self.trigo.densidad_fuente = 'Ficha de prueba'
        self.trigo.densidad_url = 'https://example.com/ficha'
        self.trigo.densidad_verificada = '2026-03-01'
        self.trigo.save()
        r = ejecutar_calculo(self.user, self.datos(dose='180')).resultado
        self.assertEqual((r['origin'], r['verified'], r['source']), ('documented', True, 'Ficha de prueba'))
        self.assertAlmostEqual(r['amount'], 26.928)
        # La misma densidad sin fuente ya no es documentada, y un valor distinto es un ajuste.
        self.assertEqual(ejecutar_calculo(self.user, self.datos(dose='150')).resultado['origin'], 'adjusted')

    def test_fuente_declarada_se_guarda_pero_no_verifica_el_dato(self):
        calculo = ejecutar_calculo(self.user, self.datos(dose_source='  Recomendación de mi asesor  '))
        self.assertEqual(calculo.resultado['declared_source'], 'Recomendación de mi asesor')
        self.assertFalse(calculo.resultado['verified'])
        self.assertEqual(calculo.entrada['dose_source'], '  Recomendación de mi asesor  ')

    def test_validaciones(self):
        otro = User.objects.create_user('beto', email='beto@example.com', password='Campo-Seguro-2026!')
        casos = {
            'Selecciona un potrero registrado.': self.datos(plot_id=''),
            'La superficie debe ser mayor a 0.': self.datos(area='0'),
            'La dosis debe ser un número válido.': self.datos(dose='mucho'),
            'La superficie no puede superar 0,1496 ha registradas.': self.datos(area='0.15'),
            'Selecciona un cultivo activo del catálogo.': self.datos(crop='Cultivo inventado'),
            'El tipo de cálculo no está disponible.': self.datos(operation='otra'),
            'La categoría no corresponde al tipo de cálculo.': self.datos(calculation_type='coverage'),
        }
        for mensaje, datos in casos.items():
            with self.subTest(mensaje=mensaje), self.assertRaisesMessage(ErrorCalculo, mensaje):
                ejecutar_calculo(self.user, datos)
        with self.assertRaisesMessage(ErrorCalculo, 'El potrero no existe o no pertenece a tu cuenta.'):
            ejecutar_calculo(otro, self.datos())

    def test_calculo_avanzado_por_poblacion(self):
        datos = self.datos(
            operation='seed_population', plants_m2='300', thousand_seed_weight_g='45', germination_pct='90',
            purity_pct='98', source='Ficha técnica',
        )
        r = ejecutar_calculo(self.user, datos).resultado
        self.assertAlmostEqual(r['rate'], 153.0612, places=4)
        self.assertAlmostEqual(r['amount'], 153.0612245 * 0.1496, places=5)
        self.assertEqual((r['area'], r['unit'], r['dose_unit']), (0.1496, 'kg', 'kg/ha'))
        for cambio, mensaje in (
            ({'germination_pct': '101'}, 'Germinación y pureza deben ser porcentajes de hasta 100.'),
            ({'source': ''}, 'Indica la fuente de los parámetros de semilla.'),
            ({'thousand_seed_weight_g': '0'}, 'El peso de mil semillas debe ser mayor a 0.'),
        ):
            with self.subTest(cambio=cambio), self.assertRaisesMessage(ErrorCalculo, mensaje):
                ejecutar_calculo(self.user, {**datos, **cambio})


class PantallaYGuardadoTests(CalculoBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post(self, **valores):
        return self.client.post('/calculos/ejecutar/', json.dumps(self.datos(**valores)), content_type='application/json')

    def test_el_calculo_se_guarda_y_llega_a_reportes_con_la_superficie_completa(self):
        respuesta = self.post(dose_source='Mi asesor')
        self.assertEqual(respuesta.status_code, 200)
        guardado = CalculoAgricola.objects.get(pk=respuesta.json()['calculation_id'])
        self.assertEqual((guardado.potrero, guardado.operacion, guardado.resultado['amount']), (self.potrero, 'dose', 14.96))
        reporte = self.client.get('/reportes/?tipo=calculos').content.decode()
        self.assertIn('100 kg/ha × 0,1496 ha', reporte)
        self.assertNotIn('× 0,15 ha', reporte)
        self.assertIn('fuente indicada por la persona: Mi asesor', reporte)

    def test_vista_previa_no_guarda_y_los_errores_vuelven_como_json(self):
        respuesta = self.client.post(
            '/calculos/ejecutar/', json.dumps({**self.datos(), 'preview': True}), content_type='application/json'
        )
        self.assertEqual((respuesta.json()['preview'], CalculoAgricola.objects.count()), (True, 0))
        error = self.post(dose='')
        self.assertEqual((error.status_code, error.json()['error']), (400, 'La dosis debe ser un número válido.'))
        self.assertEqual(self.client.post('/calculos/ejecutar/', 'x', content_type='text/plain').status_code, 415)

    def test_pantalla_sin_dosis_inventada_y_sin_opciones_avanzadas(self):
        html = self.client.get('/decisiones/?tipo=semillas&herramienta=llevar').content.decode()
        dosis = html[html.index('id="calc-dose"') - 60 : html.index('id="calc-dose"') + 120]
        self.assertNotIn('value=', dosis)  # el campo empieza vacío: no hay una densidad por defecto
        for quitado in ('id="adv-seed"', 'id="adv-nutrient"', 'calc-advanced', 'Opciones avanzadas'):
            self.assertNotIn(quitado, html)
        self.assertIn('id="calc-dose-source-field" hidden', html)
        for script in ('js/calc-limites.js', 'js/calc-resultado.js', 'js/calc-panel.js', 'js/calc-flow.js'):
            self.assertIn(script, html)
        # Los límites y el resumen se cargan antes del motor que los usa.
        self.assertLess(html.index('js/calc-limites.js'), html.index('js/calc-panel.js'))
        self.assertLess(html.index('js/calc-resultado.js'), html.index('js/calc-panel.js'))
