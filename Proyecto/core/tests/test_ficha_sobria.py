"""Ficha del predio más sobria: botón «Volver» visible y sin textos repetidos (nada se pierde: solo se quita lo
que la pantalla ya dice en otro lugar)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.ayudas import AYUDAS
from core.models import Cultivo, Potrero, Predio, TemporadaCultivo

User = get_user_model()


class FichaSobriaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.palto = Cultivo.objects.create(nombre='Palto')
        self.trigo = Cultivo.objects.create(nombre='Trigo')
        self.predio = Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='Melipilla', superficie_ha=9)
        self.client.force_login(self.user)

    def pagina(self, predio=None, consulta=''):
        predio = predio or self.predio
        return self.client.get(f'/predios/{predio.pk}/{consulta}').content.decode()

    def potrero(self, nombre, cultivo=None, **temporada):
        potrero = Potrero.objects.create(
            predio=self.predio, nombre=nombre, superficie_ha=3.2, perimetro=[], cultivo=cultivo
        )
        if cultivo:
            TemporadaCultivo.objects.create(potrero=potrero, cultivo=cultivo, **temporada)
        return potrero

    def tarjeta(self, potrero):
        html = self.pagina()
        inicio = html.index(f'id="potrero-{potrero.pk}"')
        return html[inicio : html.index('</article>', inicio)]

    def test_el_boton_volver_es_visible_y_lleva_a_mis_predios(self):
        html = self.pagina()
        self.assertIn('<a class="crumbs-back" href="/predios/"><i data-lucide="arrow-left"></i> Mis predios</a>', html)

    def test_sin_potrero_las_migas_no_repiten_el_nombre_del_predio_que_ya_es_el_titulo(self):
        html = self.pagina()
        migas = html[html.index('class="crumbs"') : html.index('</nav>', html.index('class="crumbs"'))]
        self.assertNotIn('aria-current', migas)
        self.assertNotIn('›', migas)

    def test_con_potrero_seleccionado_el_camino_completo_se_conserva(self):
        potrero = self.potrero('Lote A', self.palto)
        html = self.pagina(consulta=f'?potrero={potrero.pk}')
        self.assertRegex(html, r'Mis predios</a><span aria-hidden="true">›</span><a href="/predios/\d+/">Fundo</a>.*Lote A')

    def test_no_repite_en_la_ficha_lo_que_ya_dice_la_cabecera(self):
        self.potrero('Lote A', self.palto)
        html = self.pagina()
        for repetido in ('Número de potreros', 'Superficie restante', 'Cultivos registrados', 'Vista geográfica'):
            self.assertNotIn(repetido, html)
        for presente in ('Superficie total', 'ha disponibles', 'Potreros asociados', 'Superficie', 'Perímetro'):
            self.assertIn(presente, html)

    def test_el_potrero_muestra_hectareas_y_cultivo_sin_metros_cuadrados(self):
        potrero = self.potrero('Lote A', self.palto)
        tarjeta = self.tarjeta(potrero)
        self.assertIn('3,2 ha · Palto', tarjeta)
        self.assertNotIn('m²', tarjeta)

    def test_potrero_sin_cultivo_no_escribe_un_relleno_que_el_chip_ya_dice(self):
        potrero = self.potrero('Vacío')
        tarjeta = self.tarjeta(potrero)
        self.assertNotIn('Sin cultivo asociado', tarjeta)
        self.assertIn('Sin cultivo', tarjeta)  # el chip sigue diciéndolo

    def test_la_lista_de_temporadas_solo_aparece_si_hay_historial(self):
        potrero = self.potrero('Lote', self.palto)
        sola = self.tarjeta(potrero)
        self.assertNotIn('Temporadas de cultivo', sola)
        self.assertNotIn('Siembra:', sola)
        TemporadaCultivo.objects.create(potrero=potrero, cultivo=self.trigo, temporada_agricola='2026-2027')
        Potrero.objects.filter(pk=potrero.pk).update(cultivo=self.trigo)
        con_historial = self.tarjeta(potrero)
        self.assertIn('Temporadas de cultivo', con_historial)
        self.assertEqual(con_historial.count('Vigente'), 1)
        self.assertEqual(con_historial.count('>Corregir</a>'), 1)

    def test_la_ubicacion_solo_se_muestra_si_aporta_algo(self):
        self.assertIn('class="pf-location"', self.pagina())
        self.assertIn('Melipilla', self.pagina())
        igual = Predio.objects.create(propietario=self.user, nombre='Fundo Sur', ubicacion='fundo sur', superficie_ha=2)
        self.assertNotIn('class="pf-location"', self.pagina(igual))
        sin = Predio.objects.create(propietario=self.user, nombre='Fundo Norte', ubicacion='', superficie_ha=2)
        html = self.pagina(sin)
        self.assertNotIn('class="pf-location"', html)
        self.assertNotIn('Ubicación de referencia no registrada', html)

    def test_el_historial_no_explica_como_funciona_la_base_de_datos(self):
        self.assertNotIn('persistidos en la base de datos', self.pagina())


class AyudasBrevesTests(TestCase):
    def test_cada_consejo_es_breve(self):
        for clave, ayuda in AYUDAS.items():
            with self.subTest(clave):
                self.assertLessEqual(len(ayuda['pasos']), 3)
                for paso in ayuda['pasos']:
                    self.assertLessEqual(len(paso), 100, paso)
