"""Limpieza de la interfaz: sin pie repetido, sin datos técnicos del mapa y sin el recuadro «Próxima acción».

Solo cambia lo que se muestra: el GeoJSON, los vértices guardados y las funciones del mapa siguen intactos.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Cultivo, Potrero, Predio

CORE = Path(__file__).resolve().parent.parent
PIE = 'La información es orientativa y no reemplaza la asesoría'
GEOJSON = {
    'type': 'Polygon',
    'coordinates': [[[-71.22, -33.69], [-71.21, -33.69], [-71.21, -33.68], [-71.22, -33.68], [-71.22, -33.69]]],
}
SECCIONES = (
    '/',
    '/predios/',
    '/productos/',
    '/decisiones/',
    '/actividades/',
    '/reportes/',
    '/calendario/',
    '/busqueda/',
)


class LimpiezaInterfazTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.client.force_login(self.user)

    def con_predio(self):
        predio = Predio.objects.create(
            propietario=self.user, nombre='Fundo', ubicacion='Melipilla', superficie_ha=100, geojson=GEOJSON
        )
        cultivo = Cultivo.objects.create(nombre='Palto de prueba')
        potrero = Potrero.objects.create(predio=predio, nombre='Lote 1', superficie_ha=1.3, perimetro=[], cultivo=cultivo)
        return predio, potrero

    def test_el_pie_repetido_ya_no_aparece_en_ninguna_seccion(self):
        self.con_predio()
        for url in SECCIONES:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotContains(r, PIE, msg_prefix=url)
            self.assertNotContains(r, 'app-footer', msg_prefix=url)
        self.assertNotIn('app-footer', (CORE / 'static' / 'css' / 'body.css').read_text(encoding='utf-8'))

    def test_el_inicio_no_muestra_el_texto_decorativo_ni_la_guia_de_primer_predio_si_ya_hay_predio(self):
        vacio = self.client.get('/')
        self.assertNotContains(vacio, 'Cultivando mejores decisiones')
        self.assertContains(vacio, 'Empieza registrando tu primer predio')
        self.con_predio()
        con_predio = self.client.get('/')
        self.assertNotContains(con_predio, 'Cultivando mejores decisiones')
        self.assertNotContains(con_predio, 'Empieza registrando tu primer predio')

    def test_la_ficha_del_predio_no_muestra_datos_tecnicos_pero_conserva_el_mapa_y_el_geojson(self):
        predio, _ = self.con_predio()
        r = self.client.get(f'/predios/{predio.pk}/')
        for texto in ('Estado GeoJSON', 'GeoJSON guardado', 'Coordenadas de los vértices', 'pf-geo-bar'):
            self.assertNotContains(r, texto)
        self.assertContains(r, 'id="predio-detail-map"')
        self.assertContains(r, 'id="predio-detail-geometry"')  # la geometría sigue llegando al mapa
        predio.refresh_from_db()
        self.assertEqual(predio.geojson, GEOJSON)  # y sigue guardada tal cual
        sin_mapa = Predio.objects.create(propietario=self.user, nombre='Antiguo', ubicacion='X', superficie_ha=2)
        self.assertNotContains(self.client.get(f'/predios/{sin_mapa.pk}/'), 'Estado GeoJSON')

    def test_el_panel_de_mis_predios_no_genera_proxima_accion_y_conserva_las_herramientas(self):
        js = (CORE / 'static' / 'js' / 'predios-map.js').read_text(encoding='utf-8')
        for retirado in ('Próxima acción', 'nextAction', 'Definir planificación', 'drawer-next'):
            self.assertNotIn(retirado, js)
        for vigente in ("'Planificar siembra'", "'Calcular semillas'", "'Calcular fertilizantes'", "'Abrir predio'"):
            self.assertIn(vigente, js)
        self.assertIn('assign.dataset.assignCrop = plot.id;', js)  # sin cultivo: botón «Asignar cultivo» en el aviso
        self.assertNotIn('drawer-next', (CORE / 'static' / 'css' / 'predios.css').read_text(encoding='utf-8'))

    def test_mis_predios_sin_predios_muestra_solo_la_invitacion_a_crear_el_primero(self):
        html = self.client.get('/predios/').content.decode()
        self.assertEqual(html.count('Registrar primer predio'), 1)
        for ausente in ('id="farm-map"', 'id="selected-terrain"', 'id="potrero-history"', 'id="show-all-farms"'):
            self.assertNotIn(ausente, html)

    def test_mis_predios_con_predios_muestra_listado_mapa_y_panel(self):
        self.con_predio()
        html = self.client.get('/predios/').content.decode()
        for presente in ('id="farm-map"', 'id="selected-terrain"', 'id="potrero-history"', 'Fundo', 'Lote 1'):
            self.assertIn(presente, html)
        self.assertNotIn('Registrar primer predio', html)
