"""Simplificación de la navegación y del flujo de planificación (sin quitar funciones).

- Menú agrupado, sin entradas que repiten otra pantalla.
- En la ficha del predio, un solo botón de planificación por potrero.
- El formulario de temporada dice qué se viene a hacer y nombra la fecha según el tipo de cultivo.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.forms import sowing_date_label
from core.models import Cultivo, Potrero, Predio, TemporadaCultivo

User = get_user_model()


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.palto = Cultivo.objects.create(nombre='Palto')
        self.trigo = Cultivo.objects.create(nombre='Trigo')
        self.tomate = Cultivo.objects.create(nombre='Tomate')
        self.predio = Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='Melipilla', superficie_ha=9)
        self.client.force_login(self.user)

    def potrero(self, nombre, cultivo=None, **temporada):
        potrero = Potrero.objects.create(
            predio=self.predio, nombre=nombre, superficie_ha=1, perimetro=[], cultivo=cultivo
        )
        if cultivo:
            TemporadaCultivo.objects.create(potrero=potrero, cultivo=cultivo, **temporada)
        return potrero

    def tarjeta(self, potrero):
        html = self.client.get(f'/predios/{self.predio.pk}/').content.decode()
        inicio = html.index(f'id="potrero-{potrero.pk}"')
        return html[inicio : html.index('</article>', inicio)]


class MenuAgrupadoTests(Base):
    def test_cada_pantalla_aparece_una_sola_vez_y_ninguna_funcion_se_pierde(self):
        html = self.client.get('/').content.decode()
        nav = html[html.index('aria-label="Principal"') : html.index('</nav>', html.index('aria-label="Principal"'))]
        enlaces = re.findall(r'href="([^"]+)"', nav)
        self.assertEqual(len(enlaces), len(set(enlaces)))  # sin dos entradas al mismo lugar
        self.assertNotIn('#mapa', nav)  # «Mapa» era la misma página «Mis predios»
        for etiqueta in ('>Mapa<', '>Toma de decisiones<', '>Buscar<'):
            self.assertNotIn(etiqueta, nav)
        # Todo sigue abriendo: el mapa dentro de Mis predios, el buscador arriba y el centro de decisiones.
        for url in ('/predios/', '/busqueda/', '/calculadoras/', '/decisiones/', '/calendario/', '/actividades/', '/productos/', '/reportes/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn('action="/busqueda/"', html)

    def test_calculadoras_es_un_enlace_directo_con_tres_tarjetas(self):
        for url in ('/calculadoras/', '/decisiones/', '/decisiones/?herramienta=alcanza'):
            menu = self.client.get(url).context['menu']
            item = next(i for i in menu if i.get('label') == 'Calculadoras')
            self.assertEqual(item['href'], '/calculadoras/', url)
            self.assertTrue(item['active'], url)
            self.assertNotIn('children', item, url)
        html = self.client.get('/calculadoras/').content.decode()
        self.assertNotIn('nav-group', html)  # el desplegable del menú ya no existe
        for etiqueta in ('Semillas', 'Fertilizantes', '¿Me alcanza?'):
            self.assertIn(f'<h2>{etiqueta}</h2>', html)
        for destino in (
            '/decisiones/?herramienta=llevar&amp;tipo=semillas',
            '/decisiones/?herramienta=llevar&amp;tipo=insumos',
            '/decisiones/?herramienta=alcanza',
        ):
            self.assertIn(f'href="{destino}"', html)
            self.assertEqual(self.client.get(destino.replace('&amp;', '&')).status_code, 200, destino)


class UnSoloBotonDePlanificacionTests(Base):
    def test_sin_cultivo_incompleta_y_completa_muestran_un_unico_boton(self):
        vacio = self.potrero('Vacío')
        incompleto = self.potrero('Incompleto', self.palto)
        completo = self.potrero('Completo', self.trigo, fecha_siembra='2026-11-20', fecha_cosecha='2027-03-20')
        for potrero, etiqueta, clase in (
            (vacio, 'Asignar cultivo', 'btn-primary-agro'),
            (incompleto, 'Completar planificación', 'btn-primary-agro'),
            (completo, 'Editar planificación', 'btn-secondary-agro'),
        ):
            acciones = re.search(r'<div class="plot-actions">(.*?)</div>', self.tarjeta(potrero), re.S).group(1)
            self.assertEqual(acciones.count('<a '), 1, potrero.nombre)
            self.assertRegex(acciones, rf'class="{clase} btn-sm"\s+href="[^"]+">{etiqueta}</a>')

    def test_la_temporada_vigente_no_tiene_un_segundo_acceso_y_el_historial_se_conserva(self):
        potrero = self.potrero('Lote', self.palto)
        anterior = potrero.temporadas.get()
        TemporadaCultivo.objects.create(potrero=potrero, cultivo=self.trigo, variedad='Pantera', temporada_agricola='2026-2027')
        Potrero.objects.filter(pk=potrero.pk).update(cultivo=self.trigo)  # el potrero sigue a su temporada vigente
        tarjeta = self.tarjeta(potrero)
        self.assertNotIn('Editar temporada', tarjeta)
        self.assertEqual(tarjeta.count('>Corregir</a>'), 1)  # solo la temporada anterior
        self.assertIn(f'/temporadas/{anterior.pk}/editar/', tarjeta)
        self.assertIn('Trigo · Pantera · 2026-2027', tarjeta)  # cultivo, variedad y temporada a la vista
        self.assertEqual(tarjeta.count('Vigente'), 1)
        self.assertEqual(potrero.temporadas.count(), 2)

    def test_potrero_sin_temporadas_no_muestra_un_bloque_vacio(self):
        tarjeta = self.tarjeta(self.potrero('Vacío'))
        self.assertNotIn('Temporadas de cultivo', tarjeta)
        self.assertNotIn('No hay temporadas registradas', tarjeta)

    def test_la_ficha_no_repite_el_siguiente_paso(self):
        self.potrero('Lote', self.palto)
        html = self.client.get(f'/predios/{self.predio.pk}/').content.decode()
        self.assertNotIn('class="next-step-line"', html)
        self.assertEqual(html.count('>Completar planificación</a>'), 1)


class FormularioDePlanificacionTests(Base):
    def url(self, potrero, temporada=None):
        base = f'/predios/{self.predio.pk}/potreros/{potrero.pk}/temporadas/'
        return f'{base}{temporada.pk}/editar/' if temporada else f'{base}nueva/'

    def titulo(self, url):
        return re.search(r'<h1 class="page-title">(.*?)</h1>', self.client.get(url).content.decode()).group(1)

    def test_el_titulo_dice_lo_que_se_viene_a_hacer(self):
        incompleto = self.potrero('Incompleto', self.palto)
        completo = self.potrero('Completo', self.trigo, fecha_siembra='2026-11-20', fecha_cosecha='2027-03-20')
        self.assertEqual(self.titulo(self.url(incompleto, incompleto.temporadas.get())), 'Completar planificación')
        self.assertEqual(self.titulo(self.url(completo, completo.temporadas.get())), 'Editar planificación')
        self.assertEqual(self.titulo(self.url(completo)), 'Planificar nueva temporada')
        anterior = completo.temporadas.get()
        TemporadaCultivo.objects.create(potrero=completo, cultivo=self.palto)
        self.assertEqual(self.titulo(self.url(completo, anterior)), 'Corregir temporada anterior')
        self.assertContains(self.client.get(self.url(incompleto, incompleto.temporadas.get())), 'Guardar planificación')

    def test_la_fecha_se_nombra_segun_el_tipo_de_cultivo(self):
        self.assertEqual(sowing_date_label('Palto'), 'Fecha de plantación')
        self.assertEqual(sowing_date_label('Tomate'), 'Fecha de siembra o trasplante')
        self.assertEqual(sowing_date_label('Trigo'), 'Fecha de siembra')
        self.assertEqual(sowing_date_label(''), 'Fecha de siembra')
        for cultivo, etiqueta in ((self.palto, 'Fecha de plantación'), (self.trigo, 'Fecha de siembra'),
                                  (self.tomate, 'Fecha de siembra o trasplante')):
            potrero = self.potrero(f'Lote {cultivo.nombre}', cultivo)
            html = self.client.get(self.url(potrero, potrero.temporadas.get())).content.decode()
            self.assertIn(f'<label for="id_fecha_siembra">{etiqueta}</label>', html)

    def test_guardar_sigue_funcionando_y_vuelve_al_predio(self):
        potrero = self.potrero('Lote', self.palto)
        temporada = potrero.temporadas.get()
        respuesta = self.client.post(
            self.url(potrero, temporada),
            {'cultivo': self.palto.pk, 'variedad': 'Hass', 'temporada_agricola': '2026-2027',
             'fecha_siembra': '2026-11-20', 'fecha_cosecha': '2027-04-20', 'estado': 'planned', 'observaciones': ''},
        )
        self.assertRedirects(respuesta, f'/predios/{self.predio.pk}/', fetch_redirect_response=False)
        temporada.refresh_from_db()
        self.assertEqual((temporada.variedad, str(temporada.fecha_siembra)), ('Hass', '2026-11-20'))
        acciones = re.search(r'<div class="plot-actions">(.*?)</div>', self.tarjeta(potrero), re.S).group(1)
        self.assertIn('Editar planificación', acciones)


class RegistrarPotreroSimplificadoTests(Base):
    """Registrar potrero: solo datos del potrero; la variedad y las fechas viven en la planificación."""

    def setUp(self):
        super().setUp()
        self.nuevo = f'/predios/{self.predio.pk}/potreros/nuevo/'

    def datos(self, **valores):
        return {'nombre': 'Lote', 'cultivo': '', 'superficie_ha': '1.5', 'latitud': '', 'longitud': '',
                'perimetro': '[]', **valores}

    def test_pantalla_con_los_titulos_nuevos_y_sin_campos_de_planificacion(self):
        html = self.client.get(self.nuevo).content.decode()
        self.assertIn('<h2>Información del potrero</h2>', html)
        self.assertIn('<h2>Ubicación y superficie</h2>', html)
        for fuera in ('¿Qué vas a plantar?', '¿Dónde queda?', 'Agregar más detalles', 'id_variedad',
                      'id_fecha_siembra', 'id_fecha_cosecha', 'límite verde'):
            self.assertNotIn(fuera, html)
        self.assertIn('(opcional)', html[html.index('for="id_cultivo"') : html.index('id="id_cultivo"')])
        # Ubicación exacta: sigue disponible y contraída; se conservan dibujo, herramientas, superficie y «lo que queda».
        self.assertRegex(html, r'<details class="more-details is-advanced"\s*>\s*<summary>Ubicación exacta \(avanzado\)')
        for presente in ('id="id_latitud"', 'id="id_longitud"', 'id="draw-perimeter"', 'Más herramientas',
                         'id="id_superficie_ha"', 'data-fill-available', 'class="map-legend"', 'Límite del predio'):
            self.assertIn(presente, html)
        # Al entrar, el aviso del mapa es la indicación inicial: ningún mensaje de cancelación.
        estado = re.search(r'id="map-status"[^>]*>(.*?)</p>', html, re.S).group(1)
        self.assertIn('Dibuja tu potrero dentro del límite del predio', estado)
        self.assertNotIn('cancel', html[html.index('id="map-status"') : html.index('role="toolbar"')].lower())

    def test_se_crea_sin_cultivo_con_superficie_manual(self):
        self.assertRedirects(self.client.post(self.nuevo, self.datos()), f'/predios/{self.predio.pk}/')
        lote = Potrero.objects.get(nombre='Lote')
        self.assertEqual((lote.cultivo, float(lote.superficie_ha), lote.temporadas.count()), (None, 1.5, 0))

    def test_se_crea_con_cultivo_y_dibujando_el_perimetro(self):
        perimetro = '[[-71.2160,-33.6870],[-71.2150,-33.6870],[-71.2150,-33.6860],[-71.2160,-33.6860]]'
        respuesta = self.client.post(self.nuevo, self.datos(cultivo=self.trigo.pk, superficie_ha='', perimetro=perimetro))
        self.assertRedirects(respuesta, f'/predios/{self.predio.pk}/')
        lote = Potrero.objects.get(nombre='Lote')
        self.assertEqual(len(lote.perimetro), 4)
        self.assertGreater(lote.superficie_ha, 0)  # calculada desde el contorno
        self.assertIsNotNone(lote.geojson)
        temporada = lote.temporadas.get()  # una sola, lista para planificar
        self.assertEqual((temporada.cultivo, temporada.fecha_siembra), (self.trigo, None))

    def test_planificar_despues_no_duplica_los_datos_del_cultivo(self):
        self.client.post(self.nuevo, self.datos(cultivo=self.trigo.pk))
        lote = Potrero.objects.get(nombre='Lote')
        temporada = lote.temporadas.get()
        self.client.post(
            f'/predios/{self.predio.pk}/potreros/{lote.pk}/temporadas/{temporada.pk}/editar/',
            {'cultivo': self.trigo.pk, 'variedad': 'Pantera', 'temporada_agricola': '2026-2027',
             'fecha_siembra': '2026-11-20', 'fecha_cosecha': '2027-03-20', 'estado': 'planned', 'observaciones': ''},
        )
        # Editar después el potrero (otro nombre) no crea otra temporada ni cambia la planificación.
        self.client.post(f'/predios/{self.predio.pk}/potreros/{lote.pk}/editar/', self.datos(nombre='Lote 2', cultivo=self.trigo.pk))
        lote.refresh_from_db()
        temporada = lote.temporadas.get()
        self.assertEqual((temporada.variedad, str(temporada.fecha_siembra)), ('Pantera', '2026-11-20'))
        self.assertEqual((lote.nombre, lote.variedad, str(lote.fecha_siembra)), ('Lote 2', 'Pantera', '2026-11-20'))

    def test_potrero_existente_con_datos_antiguos_no_los_pierde_al_editarlo(self):
        antiguo = Potrero.objects.create(predio=self.predio, nombre='Antiguo', superficie_ha=1, perimetro=[],
                                         cultivo=self.palto, variedad='Hass', fecha_siembra='2024-09-01')
        self.client.post(f'/predios/{self.predio.pk}/potreros/{antiguo.pk}/editar/',
                         self.datos(nombre='Antiguo', cultivo=self.palto.pk, superficie_ha='1'))
        antiguo.refresh_from_db()
        self.assertEqual((antiguo.variedad, str(antiguo.fecha_siembra)), ('Hass', '2024-09-01'))
