"""«Mis predios registrados» en /predios/: solo tarjetas con lo esencial, sin selector de vista y sin «Historial de
potreros». Nada se borra de la base de datos: solo cambia lo que se muestra."""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import Cultivo, Potrero, Predio

User = get_user_model()
CSS = Path(__file__).resolve().parent.parent / 'static' / 'css'
JS = Path(__file__).resolve().parent.parent / 'static' / 'js'


class PrediosTarjetasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.otro = User.objects.create_user('beto', email='beto@example.com', password='Campo-Seguro-2026!')
        self.palto = Cultivo.objects.create(nombre='Palto')
        self.con = Predio.objects.create(
            propietario=self.user, nombre='Fundo Verde', ubicacion='Melipilla', superficie_ha='12.5'
        )
        Potrero.objects.create(predio=self.con, nombre='Norte', superficie_ha=3, perimetro=[], cultivo=self.palto)
        Potrero.objects.create(predio=self.con, nombre='Sur', superficie_ha=2, perimetro=[])
        self.sin = Predio.objects.create(propietario=self.user, nombre='Campo Vacio', ubicacion='Paine', superficie_ha=4)
        Predio.objects.create(propietario=self.otro, nombre='Predio Ajeno', superficie_ha=9)
        self.client.force_login(self.user)

    def pagina(self):
        return self.client.get('/predios/').content.decode()

    def tarjeta(self, predio):
        html = self.pagina()
        inicio = html.index(f'data-farm-card="{predio.pk}"')
        return html[inicio : html.index('</article>', inicio)]

    def test_la_tarjeta_muestra_solo_lo_esencial(self):
        tarjeta = self.tarjeta(self.con)
        self.assertIn('Fundo Verde', tarjeta)
        self.assertRegex(tarjeta, r'12[,.]50? ha')  # la plantilla muestra 2 decimales: «12,50 ha»
        self.assertIn('2 potreros', tarjeta)
        self.assertIn('1 cultivo', tarjeta)
        self.assertIn('Siguiente paso', tarjeta)
        # Sin texto secundario: la ubicación ya está en la ficha del predio.
        self.assertNotIn('Melipilla', tarjeta)
        self.assertNotIn('map-pin', tarjeta)

    def test_sin_cultivos_no_se_inventa_ese_dato(self):
        tarjeta = self.tarjeta(self.sin)
        self.assertIn('Campo Vacio', tarjeta)
        self.assertIn('0 potreros', tarjeta)
        self.assertNotIn('data-lucide="sprout"', tarjeta)
        self.assertIn('Sin potreros', tarjeta)

    def test_la_tarjeta_conserva_seleccion_y_menu_de_acciones(self):
        tarjeta = self.tarjeta(self.con)
        pk = self.con.pk
        self.assertIn(f'data-select-farm="{pk}"', tarjeta)
        self.assertIn('aria-pressed=', tarjeta)
        self.assertIn('class="row-menu-trigger"', tarjeta)
        for destino in (
            reverse('predio_detail', args=[pk]),
            reverse('predio_edit', args=[pk]),
            reverse('potrero_create', args=[pk]),
            f"{reverse('reportes')}?predio={pk}",
        ):
            self.assertIn(f'href="{destino}', tarjeta)
        self.assertIn('data-focus-map', tarjeta)  # «Ver en mapa» sigue llevando al mapa de abajo
        self.assertIn('Ver planificación', tarjeta)

    def test_solo_aparecen_los_predios_del_usuario(self):
        self.assertNotIn('Predio Ajeno', self.pagina())

    def test_ya_no_hay_selector_de_vista_ni_historial_de_potreros(self):
        html = self.pagina()
        for ausente in (
            'Lista</button>', 'data-view-mode', 'data-view-focus', 'view-switch', 'predios-view.js',
            'HISTORIAL DE POTREROS', 'potrero-history', 'name="filtro_predio"',
        ):
            self.assertNotIn(ausente, html)

    def test_el_mapa_la_informacion_y_el_clima_siguen_en_la_pagina(self):
        html = self.pagina()
        for presente in ('id="farm-map"', 'id="selected-terrain"', 'id="weather-card"', 'id="show-all-farms"', 'id="crop-dialog"'):
            self.assertIn(presente, html)

    def test_la_vista_ya_no_calcula_el_historial(self):
        contexto = self.client.get('/predios/').context
        for clave in ('potrero_history_page', 'crop_options', 'terrain_filters'):
            self.assertNotIn(clave, contexto)

    def test_los_datos_historicos_siguen_guardados(self):
        # Quitar la sección no borra nada: los potreros siguen en la base y en la ficha del predio.
        self.assertEqual(Potrero.objects.filter(predio=self.con).count(), 2)
        self.assertContains(self.client.get(reverse('predio_detail', args=[self.con.pk])), 'Norte')


class PrediosTarjetasEstilosTests(SimpleTestCase):
    def test_grid_responsive_de_tarjetas_sin_reglas_huerfanas(self):
        predios = (CSS / 'predios.css').read_text(encoding='utf-8')
        body = (CSS / 'body.css').read_text(encoding='utf-8')
        self.assertIn('grid-template-columns: repeat(auto-fill, minmax(min(260px, 100%), 340px));', predios)
        self.assertRegex(predios, r'@media \(max-width: 600px\) \{\s*\.farm-picker-list\.entity-grid \{\s*grid-template-columns: minmax\(0, 1fr\);')
        for texto in (predios, body):
            self.assertNotIn('data-view', texto)
            self.assertNotIn('potrero-history', texto)
            self.assertIsNone(re.search(r'(?<![\w-])\.view-switch', texto))
        self.assertIn('.map-view-switch', predios)  # el interruptor Normal/Satélite del mapa no se toca


class PanelSeleccionadoCompactoTests(SimpleTestCase):
    """El panel «Información seleccionada» (lo arma predios-map.js) muestra lo esencial y pliega el resto."""

    def panel(self):
        fuente = (JS / 'predios-map.js').read_text(encoding='utf-8')
        inicio = fuente.index('function setSelectedTerrain(')
        return fuente[inicio : fuente.index('function updateMeasurements(', inicio)]

    def test_lo_esencial_va_a_la_vista_y_el_resto_queda_plegado(self):
        js = self.panel()
        for presente in ("'terrain-keys'", "'Superficie'", "'Cultivo'", "'Siembra'", "'Cosecha'", "'Planificación'",
                         "'Más datos y herramientas'", "'Más datos'", "'Historial de cultivos ('", "el('progress', 'terrain-meter')"):
            self.assertIn(presente, js)
        self.assertNotIn('.open =', js)  # ningún pliegue arranca abierto: el panel no se alarga solo
        self.assertNotIn("el('hr')", js)
        for retirado in ('Predio del potrero', 'terrain-season-history', 'drawer-area', 'drawer-place', 'Ver ficha e historial'):
            self.assertNotIn(retirado, js)

    def test_las_herramientas_y_los_enlaces_siguen_disponibles(self):
        js = self.panel()
        for enlace in ("'Planificar siembra'", "'Calcular semillas'", "'Calcular fertilizantes'", "'Abrir predio'", "'Ver ficha'"):
            self.assertIn(enlace, js)
        self.assertIn('assign.dataset.assignCrop = plot.id;', js)
        self.assertIn('plotNext.href = plot.primaryUrl || plot.seedsUrl;', js)

    def test_un_potrero_sin_cultivo_no_repite_el_boton_de_asignar(self):
        js = self.panel()
        # «Asignar cultivo» vive solo en el aviso; la acción principal del servidor únicamente se ofrece con cultivo.
        self.assertRegex(js, r"if \(plot\.crop\) \{\s*//[^\n]*\n\s*const plotNext = el\('a', 'btn-primary-agro btn-sm'")

    def test_las_fechas_vacias_no_se_repiten_como_texto_largo(self):
        js = self.panel()
        self.assertIn("'Sin fechas registradas'", js)
        self.assertIn("if (season.sowingDate) parts.push(", js)
        self.assertIn("if (season.harvestDate) parts.push(", js)

    def test_estilos_del_panel_compacto(self):
        css = (CSS / 'predios.css').read_text(encoding='utf-8')
        for regla in ('.terrain-keys {', '.terrain-key {', '.terrain-key.is-wide {', '.terrain-meter {', '.terrain-fold {', '.terrain-fold > summary {'):
            self.assertIn(regla, css)
        for huerfana in ('terrain-season-history', '.drawer-area', '.drawer-place'):
            self.assertNotIn(huerfana, css)
        self.assertIn('.terrain-fold-body', css)
