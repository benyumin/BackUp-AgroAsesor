"""La ficha del predio (``/predios/<pk>/`` y ``?nuevo=1``) ya no muestra la pestaña ni los accesos al Historial.

La bitácora (``PredioHistorial``) se sigue guardando y los demás enlaces con ``#predio-history`` abren la ficha.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Potrero, Predio, PredioHistorial

User = get_user_model()
CORE = Path(__file__).resolve().parent.parent


class FichaSinHistorialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('agricultor', email='a@example.com', password='Clave-segura-123')
        self.farm = Predio.objects.create(propietario=self.user, nombre='Campo', ubicacion='X', superficie_ha=10)
        self.plot = Potrero.objects.create(predio=self.farm, nombre='Lote A', superficie_ha=4)
        PredioHistorial.objects.create(
            predio=self.farm, usuario=self.user, accion=PredioHistorial.Accion.CREADO, resumen='Predio creado.'
        )
        self.client.force_login(self.user)
        self.urls = [f'/predios/{self.farm.pk}/', f'/predios/{self.farm.pk}/?nuevo=1',
                     f'/predios/{self.farm.pk}/?potrero={self.plot.pk}']

    def test_la_ficha_solo_tiene_la_pestana_informacion(self):
        for url in self.urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn('id="tab-information"', html)
                self.assertIn('id="predio-information"', html)
                self.assertIn('Lote A', html)
                for texto in ('id="tab-history"', 'id="predio-history"', 'data-open-history', 'Ver historial',
                              'Historial del predio', 'class="history-list"'):
                    self.assertNotIn(texto, html)

    def test_la_bitacora_se_sigue_guardando(self):
        self.assertTrue(PredioHistorial.objects.filter(predio=self.farm, accion=PredioHistorial.Accion.CREADO).exists())
        self.assertEqual(self.client.get(self.urls[0]).context['historial'].count(), 1)

    def test_enlaces_antiguos_al_historial_no_ocultan_la_informacion(self):
        source = (CORE / 'static' / 'js' / 'predio-detail.js').read_text(encoding='utf-8')
        self.assertIn("window.location.hash === '#predio-history' && historyTab", source)

    def test_una_sola_pestana_ocupa_todo_el_ancho(self):
        css = (CORE / 'static' / 'css' / 'predio-ficha.css').read_text(encoding='utf-8')
        rule = css[css.index('.pf .detail-tabs {'):]
        rule = rule[:rule.index('}')]
        self.assertIn('grid-auto-columns: 1fr', rule)
        self.assertNotIn('1fr 1fr', rule)
