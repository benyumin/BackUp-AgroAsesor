"""«Completar planificación» sin el error de PostgreSQL y un historial del predio sin coordenadas guardadas.

PostgreSQL rechaza ``SELECT … LEFT JOIN cultivo … FOR UPDATE`` («FOR UPDATE cannot be applied to the nullable side
of an outer join»). SQLite no emite FOR UPDATE, así que aquí se comprueba CÓMO se piden los bloqueos: ninguno al
abrir el formulario (GET) y, al guardar, solo sobre la fila propia (``of=('self',)``).
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.test import TestCase

from core.models import Cultivo, Potrero, Predio, PredioHistorial, TemporadaCultivo

User = get_user_model()
POLIGONO = {
    'type': 'Polygon',
    'coordinates': [
        [[-71.260527, -33.710116], [-71.258317, -33.710068], [-71.258306, -33.710633], [-71.260462, -33.710651],
         [-71.260527, -33.710116]]
    ],
}


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.palto = Cultivo.objects.create(nombre='Palto prueba')
        self.predio = Predio.objects.create(
            propietario=self.user, nombre='Fundo Yarek', ubicacion='Melipilla', superficie_ha=9, geojson=POLIGONO
        )
        self.potrero = Potrero.objects.create(
            predio=self.predio, nombre='Terreno Construccion', superficie_ha=1.31, perimetro=[], cultivo=self.palto
        )
        self.temporada = TemporadaCultivo.objects.create(potrero=self.potrero, cultivo=self.palto)
        self.client.force_login(self.user)
        self.editar = f'/predios/{self.predio.pk}/potreros/{self.potrero.pk}/temporadas/{self.temporada.pk}/editar/'
        self.nueva = f'/predios/{self.predio.pk}/potreros/{self.potrero.pk}/temporadas/nueva/'

    def datos(self, **valores):
        return {
            'cultivo': self.palto.pk,
            'variedad': 'Hass',
            'temporada_agricola': '2026-2027',
            'fecha_siembra': '2026-11-20',
            'fecha_cosecha': '2027-04-20',
            'estado': 'planned',
            'observaciones': '',
            **valores,
        }


class BloqueosTemporadaTests(Base):
    def bloqueos(self, llamada):
        """Argumentos de cada select_for_update() pedido durante ``llamada``."""
        original = QuerySet.select_for_update
        vistos = []

        def espia(queryset, *args, **kwargs):
            vistos.append(kwargs)
            return original(queryset, *args, **kwargs)

        with mock.patch.object(QuerySet, 'select_for_update', espia):
            respuesta = llamada()
        return respuesta, vistos

    def test_abrir_el_formulario_no_bloquea_ninguna_fila(self):
        for url in (self.editar, self.nueva):
            respuesta, vistos = self.bloqueos(lambda: self.client.get(url))
            self.assertEqual(respuesta.status_code, 200, url)
            self.assertEqual(vistos, [], url)
        self.assertContains(self.client.get(self.editar), '<h1 class="page-title">Completar planificación</h1>')
        self.assertContains(self.client.get(self.editar), 'Guardar planificación')

    def test_completar_planificacion_del_predio_abre_el_formulario(self):
        ficha = self.client.get(f'/predios/{self.predio.pk}/')
        self.assertContains(ficha, f'href="{self.editar}">Completar planificación</a>')
        self.assertEqual(self.client.get(self.editar).status_code, 200)

    def test_al_guardar_solo_se_bloquea_la_fila_propia(self):
        respuesta, vistos = self.bloqueos(lambda: self.client.post(self.editar, self.datos()))
        self.assertRedirects(respuesta, f'/predios/{self.predio.pk}/', fetch_redirect_response=False)
        self.assertGreaterEqual(len(vistos), 3)  # predio, potrero y temporada (más el guardado de la temporada)
        for argumentos in vistos:
            self.assertEqual(argumentos.get('of'), ('self',))  # nunca alcanza el LEFT JOIN de «cultivo»

    def test_guardar_conserva_el_cultivo_y_el_potrero_y_no_pierde_registros(self):
        otro = Potrero.objects.create(predio=self.predio, nombre='Otro', superficie_ha=1, perimetro=[])
        self.client.post(self.editar, self.datos())
        self.temporada.refresh_from_db()
        self.assertEqual(str(self.temporada.fecha_siembra), '2026-11-20')
        self.assertEqual(str(self.temporada.fecha_cosecha), '2027-04-20')
        self.assertEqual((self.temporada.potrero_id, self.temporada.cultivo_id), (self.potrero.pk, self.palto.pk))
        self.potrero.refresh_from_db()
        self.assertEqual(self.potrero.cultivo_id, self.palto.pk)
        self.assertEqual(TemporadaCultivo.objects.count(), 1)
        self.assertEqual(Potrero.objects.count(), 2)
        self.assertFalse(otro.temporadas.exists())  # la temporada no se mezcla con otro potrero
        self.predio.refresh_from_db()
        self.assertEqual(self.predio.geojson, POLIGONO)

    def test_potrero_sin_cultivo_tambien_abre_el_formulario(self):
        # El caso que fallaba en PostgreSQL: la relación «cultivo» es opcional.
        vacio = Potrero.objects.create(predio=self.predio, nombre='Sin cultivo', superficie_ha=1, perimetro=[])
        url = f'/predios/{self.predio.pk}/potreros/{vacio.pk}/temporadas/nueva/'
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_asignar_cultivo_tambien_bloquea_solo_la_fila_propia(self):
        from core import seasons
        import inspect

        fuente = inspect.getsource(seasons)
        self.assertNotIn("select_for_update().select_related('predio', 'cultivo')", fuente)
        self.assertEqual(fuente.count("select_for_update(of=('self',)).select_related('predio', 'cultivo')"), 2)


class HistorialSinCoordenadasTests(Base):
    def test_el_historial_muestra_el_resumen_y_la_superficie_sin_tablas_de_coordenadas(self):
        PredioHistorial.objects.create(
            predio=self.predio,
            usuario=self.user,
            accion=PredioHistorial.Accion.PERIMETRO,
            resumen='Perímetro actualizado; superficie: 1 ha → 2 ha.',
            superficie_anterior_ha=1,
            superficie_nueva_ha=2,
        )
        response = self.client.get(f'/predios/{self.predio.pk}/')
        # La ficha ya no tiene pestaña Historial: el registro se conserva en la base, sin coordenadas en la página.
        self.assertEqual(response.context['historial'].first().resumen, 'Perímetro actualizado; superficie: 1 ha → 2 ha.')
        html = response.content.decode()
        self.assertNotIn('id="predio-history"', html)
        for texto in ('Ver coordenadas registradas', 'Coordenadas anteriores'):
            self.assertNotIn(texto, html)
        self.predio.refresh_from_db()
        self.assertEqual(self.predio.geojson, POLIGONO)  # el mapa del predio no cambia

    def test_la_bitacora_ya_no_guarda_geometrias(self):
        nombres = {campo.name for campo in PredioHistorial._meta.get_fields()}
        self.assertNotIn('geometria_anterior', nombres)
        self.assertNotIn('geometria_nueva', nombres)
