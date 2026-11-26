"""Progreso de planificación compacto (seis etapas): solo en Inicio y Mis predios, con los estados reales.

Solo cambia la presentación: el cálculo sigue siendo ``journey_payload`` y las reglas de cada etapa no se tocan.
"""

from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from core.models import CalculoAgricola, Cultivo, Potrero, Predio
from core.views import journey_payload

User = get_user_model()
PASSWORD = 'Clave-segura-123'
CORE = Path(__file__).resolve().parent.parent
FEATURE = {
    'type': 'Feature',
    'properties': {},
    'geometry': {
        'type': 'Polygon',
        'coordinates': [[[-71.2, -33.7], [-71.2, -33.69], [-71.19, -33.69], [-71.19, -33.7], [-71.2, -33.7]]],
    },
}


class ProgresoCompactoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password=PASSWORD)
        self.otro = User.objects.create_user('beto', email='beto@example.com', password=PASSWORD)
        self.cultivo = Cultivo.objects.create(nombre='Cilantro')
        self.client.force_login(self.user)

    def html(self, url='/'):
        return self.client.get(url).content.decode()

    def barra(self, html, valor):
        self.assertRegex(html, rf'<progress class="journey-meter"\s+max="6"\s+value="{valor}"')

    def test_usuario_nuevo_ve_el_primer_paso_con_la_accion_de_registrar(self):
        datos = journey_payload(self.user, 'dashboard')
        self.assertEqual((datos['position'], datos['steps_total'], datos['current_label'], datos['percent']), (1, 6, 'Predio', 0))
        for url in ('/', '/predios/'):
            with self.subTest(url=url):
                html = self.html(url)
                self.assertIn('class="journey no-print"', html)
                self.assertIn('Paso 1 de 6 · Predio', html)
                self.barra(html, 0)
                self.assertIn('href="/predios/nuevo/">Registrar predio →</a>', html)

    def test_el_avance_sigue_los_estados_reales_etapa_por_etapa(self):
        farm = Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='X', superficie_ha=10)
        plot = None  # empieza con predio pero sin potreros
        for avance in range(5):
            if avance == 1:
                plot = Potrero.objects.create(predio=farm, nombre='Lumbrera', superficie_ha=4)
            elif avance == 2:
                plot.cultivo = self.cultivo
                plot.save()
            elif avance == 3:
                plot.fecha_siembra = date(2026, 10, 10)
                plot.save()
            elif avance == 4:
                CalculoAgricola.objects.create(
                    usuario=self.user, potrero=plot, operacion='dose', entrada={'area_ha': 4, 'crop': 'Cilantro'},
                    resultado={'amount': 100, 'unit': 'kg', 'dose': 25, 'dose_unit': 'kg/ha'},
                )
            datos = journey_payload(self.user, 'dashboard')
            esperado = [(2, 'Potreros', 17), (3, 'Cultivo', 33), (4, 'Siembra', 50), (5, 'Decisiones', 67), (6, 'Reporte', 83)][avance]
            with self.subTest(etapa=esperado[1]):
                self.assertEqual((datos['position'], datos['current_label'], datos['percent']), esperado)
                # El porcentaje es «etapas hechas / 6»: la etapa actual sigue pendiente y no suma.
                self.assertEqual(datos['percent'], round(100 * datos['done_count'] / datos['steps_total']))
                self.assertEqual(datos['steps'][datos['position'] - 1]['state'], 'current')
                html = self.html('/')
                self.assertIn(f'Paso {esperado[0]} de 6 · {esperado[1]}', html)
                self.barra(html, datos['done_count'])
                self.assertIn(f'{esperado[2]} %', html)

    def test_el_boton_y_el_mensaje_salen_del_sistema_actual(self):
        farm = Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='X', superficie_ha=10)
        plot = Potrero.objects.create(predio=farm, nombre='Lumbrera', superficie_ha=4, cultivo=self.cultivo)
        html = self.html('/')
        self.assertIn('Paso 4 de 6 · Siembra', html)
        self.barra(html, 3)
        self.assertIn('50 %', html)
        self.assertIn(
            f'href="/predios/{farm.pk}/potreros/{plot.pk}/temporadas/nueva/">Completar planificación →</a>', html
        )
        self.assertIn('Cilantro en «Lumbrera»: falta la fecha de siembra', html)
        self.assertIn('Cultivo seleccionado', html)
        # Las seis etapas siguen disponibles para lectores de pantalla; los seis círculos ya no se dibujan.
        for etapa in ('Predio: hecho', 'Potreros: hecho', 'Cultivo: hecho', 'Siembra: etapa actual, pendiente', 'Decisiones: pendiente', 'Reporte: pendiente'):
            self.assertIn(etapa, html)
        for viejo in ('journey-dot', 'journey-steps', 'journey-step'):
            self.assertNotIn(viejo, html)

    def test_la_accion_que_envia_un_formulario_sigue_siendo_un_formulario(self):
        farm = Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='X', superficie_ha=10)
        Predio.objects.filter(pk=farm.pk).update(geojson=FEATURE)
        html = self.html('/')
        self.assertIn('Paso 2 de 6 · Potreros', html)
        self.assertIn(f'<form method="post" action="/predios/{farm.pk}/usar-completo/" class="journey-form">', html)
        self.assertIn('Utilizar todo el predio →</button>', html)
        # Las demás opciones quedan en «Más opciones» (no se pierde ninguna acción).
        self.assertIn('Más opciones', html)
        self.assertIn(f'href="/predios/{farm.pk}/potreros/nuevo/">Dividir en potreros</a>', html)

    def test_solo_inicio_y_mis_predios_llevan_el_progreso(self):
        Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='X', superficie_ha=10)
        for url in ('/', '/predios/'):
            with self.subTest(url=url, esperado=True):
                self.assertIn('class="journey no-print"', self.html(url))
        for url in ('/actividades/', '/calendario/', '/calculadoras/', '/productos/', '/reportes/', '/decisiones/'):
            with self.subTest(url=url, esperado=False):
                self.assertNotIn('class="journey no-print"', self.html(url))

    def test_no_mezcla_el_progreso_de_otro_usuario(self):
        ajeno = Predio.objects.create(propietario=self.otro, nombre='Fundo ajeno', ubicacion='X', superficie_ha=10)
        Potrero.objects.create(
            predio=ajeno, nombre='Potrero ajeno', superficie_ha=4, cultivo=self.cultivo, fecha_siembra=date(2026, 10, 10)
        )
        datos = journey_payload(self.user, 'dashboard')
        self.assertEqual((datos['position'], datos['percent']), (1, 0))
        for url in ('/', '/predios/'):
            html = self.html(url)
            self.assertIn('Paso 1 de 6 · Predio', html)
            for ajeno_texto in ('Fundo ajeno', 'Potrero ajeno'):
                self.assertNotIn(ajeno_texto, html)


class ProgresoCompactoEstilosTests(SimpleTestCase):
    def leer(self, *partes):
        return (CORE.joinpath(*partes)).read_text(encoding='utf-8')

    def test_estilos_compactos_sin_las_reglas_de_los_seis_circulos(self):
        css = self.leer('static', 'css', 'body.css')
        for regla in ('.journey-meter {', '.journey-state {', '.journey-btn.is-primary {', '.journey-more > summary {'):
            self.assertIn(regla, css)
        for retirada in ('journey-step', 'journey-dot', 'journey-note', 'journey-next', 'journey-copy'):
            self.assertNotIn(retirada, css)
        # Móvil: rótulo corto, acción a la derecha y barra debajo.
        self.assertRegex(css, r'@media \(max-width: 600px\) \{\s*\.journey \{\s*grid-template-areas:\s*"head act"\s*"bar bar"')
        self.assertIn('.journey-short {\n    display: none;', css)
        self.assertNotIn('.journey', self.leer('static', 'css', 'dashboard.css'))  # una sola definición del componente

    def test_la_plantilla_solo_dibuja_lo_que_calcula_el_servidor(self):
        parcial = self.leer('templates', 'partials', 'journey.html')
        for dato in ('journey.position', 'journey.steps_total', 'journey.current_label', 'journey.percent', 'journey.done_count'):
            self.assertIn(dato, parcial)
        self.assertIn('<progress class="journey-meter"', parcial)
        self.assertNotIn('style=', parcial)  # sin anchos fijos en línea
        cuerpo = self.leer('templates', 'body.html')
        self.assertIn('{% if journey %}', cuerpo)
        self.assertNotIn("journey.steps.0.state != 'current'", cuerpo)
