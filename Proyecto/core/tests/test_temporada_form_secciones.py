"""«Completar planificación»: tres grupos de campos en una tarjeta centrada (solo presentación).

El formulario, las validaciones y el guardado siguen siendo los de siempre (``TemporadaCultivoForm`` y la vista
``temporada_form``): estas pruebas comprueban que los siete campos están, cada uno en su grupo, que se cargan los
valores guardados, que los errores se ven y que los botones conservan sus rutas.
"""

from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from core.models import Cultivo, Potrero, Predio, TemporadaCultivo

User = get_user_model()
CORE = Path(__file__).resolve().parent.parent
CAMPOS = ('cultivo', 'variedad', 'temporada_agricola', 'fecha_siembra', 'fecha_cosecha', 'estado', 'observaciones')
GRUPOS = (
    ('Información del cultivo', ('cultivo', 'variedad')),
    ('Temporada y fechas', ('temporada_agricola', 'fecha_siembra', 'fecha_cosecha')),
    ('Estado y observaciones', ('estado', 'observaciones')),
)


class TemporadaFormSeccionesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        self.palto = Cultivo.objects.create(nombre='Palto prueba')
        self.predio = Predio.objects.create(propietario=self.user, nombre='Fundo', ubicacion='Melipilla', superficie_ha=9)
        self.potrero = Potrero.objects.create(
            predio=self.predio, nombre='Lumbrera', superficie_ha=1.5, perimetro=[], cultivo=self.palto
        )
        self.temporada = TemporadaCultivo.objects.create(
            potrero=self.potrero,
            cultivo=self.palto,
            variedad='Hass',
            temporada_agricola='2026-2027',
            fecha_siembra=date(2026, 11, 20),
            fecha_cosecha=date(2027, 4, 20),
            estado='planned',
            observaciones='Regar cada tres días.',
        )
        self.client.force_login(self.user)
        base = f'/predios/{self.predio.pk}/potreros/{self.potrero.pk}/temporadas/'
        self.editar = f'{base}{self.temporada.pk}/editar/'
        self.volver = f'/predios/{self.predio.pk}/#potrero-{self.potrero.pk}'

    def datos(self, **valores):
        return {
            'cultivo': self.palto.pk,
            'variedad': 'Hass',
            'temporada_agricola': '2026-2027',
            'fecha_siembra': '2026-11-20',
            'fecha_cosecha': '2027-04-20',
            'estado': 'planned',
            'observaciones': 'Regar cada tres días.',
            **valores,
        }

    def pagina(self, url=None):
        return self.client.get(url or self.editar).content.decode()

    def test_los_siete_campos_estan_una_vez_y_cada_uno_en_su_grupo(self):
        html = self.pagina()
        bloques = html.split('<section class="season-group"')[1:]
        self.assertEqual(len(bloques), 3)
        for bloque, (titulo, campos) in zip(bloques, GRUPOS):
            self.assertIn(titulo, bloque)
            for campo in campos:
                self.assertEqual(bloque.count(f'id="id_{campo}"'), 1, (titulo, campo))
        for campo in CAMPOS:
            self.assertEqual(html.count(f'id="id_{campo}"'), 1, campo)

    def test_se_cargan_los_valores_guardados(self):
        html = self.pagina()
        for valor in ('value="Hass"', 'value="2026-2027"', 'value="2026-11-20"', 'value="2027-04-20"', 'Regar cada tres días.'):
            self.assertIn(valor, html)
        self.assertRegex(html, r'<option value="planned"\s+selected>')
        self.assertRegex(html, rf'<option value="{self.palto.pk}"\s+selected>')

    def test_la_cabecera_identifica_el_predio_y_el_potrero_y_los_botones_conservan_sus_rutas(self):
        html = self.pagina()
        # La temporada del setUp tiene cultivo y ambas fechas: está completa, así que el título es «Editar».
        self.assertIn('<h1 class="page-title">Editar planificación</h1>', html)
        self.assertIn('Fundo · Lumbrera', html)
        self.assertIn('temporada vigente', html)
        self.assertIn('temporadas anteriores', html)
        self.assertEqual(html.count(f'href="{self.volver}"'), 2)  # «Volver al predio» y «Cancelar»
        for texto in ('Volver al predio', 'Guardar planificación', 'Cancelar'):
            self.assertEqual(html.count(f'>{texto}</'), 1, texto)
        self.assertIn('type="submit">Guardar planificación</button>', html)

    def test_las_observaciones_guardadas_no_se_pierden(self):
        TemporadaCultivo.objects.filter(pk=self.temporada.pk).update(observaciones='Texto antiguo\ncon dos líneas')
        self.assertIn('Texto antiguo\ncon dos líneas', self.pagina())

    def test_guardar_sigue_funcionando_y_vuelve_al_predio(self):
        respuesta = self.client.post(self.editar, self.datos(variedad='Fuerte', fecha_cosecha='2027-05-01'))
        self.assertRedirects(respuesta, f'/predios/{self.predio.pk}/', fetch_redirect_response=False)
        self.temporada.refresh_from_db()
        self.assertEqual((self.temporada.variedad, str(self.temporada.fecha_cosecha)), ('Fuerte', '2027-05-01'))

    def test_los_errores_siguen_visibles_y_se_conservan_los_datos_escritos(self):
        respuesta = self.client.post(self.editar, self.datos(variedad='Otra', fecha_cosecha='2026-01-01'))
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        self.assertRegex(html, r'<small class="error" role="alert">La cosecha no puede ser anterior a la siembra\.</small>')
        self.assertIn('field has-error', html)
        self.assertIn('value="Otra"', html)
        self.temporada.refresh_from_db()
        self.assertEqual(self.temporada.variedad, 'Hass')  # nada se guardó

    def test_el_cultivo_sigue_siendo_obligatorio(self):
        respuesta = self.client.post(self.editar, self.datos(cultivo=''))
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        self.assertRegex(html, r'for="id_cultivo">Cultivo <span class="req"')
        self.assertRegex(html, r'<div class="field has-error"[^>]*>\s*<label for="id_cultivo"')
        self.assertIn('role="alert"', html)

    def test_una_fecha_invalida_no_se_guarda(self):
        respuesta = self.client.post(self.editar, self.datos(fecha_siembra='no-es-fecha'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('field has-error', respuesta.content.decode())
        self.temporada.refresh_from_db()
        self.assertEqual(str(self.temporada.fecha_siembra), '2026-11-20')


class TemporadaFormEstilosTests(SimpleTestCase):
    def leer(self, *partes):
        return CORE.joinpath(*partes).read_text(encoding='utf-8')

    def test_la_plantilla_agrupa_los_campos_sin_cambiar_el_formulario(self):
        html = self.leer('templates', 'temporada_form.html')
        posiciones = [html.index(titulo) for titulo, _ in GRUPOS]
        self.assertEqual(posiciones, sorted(posiciones))
        for campo in CAMPOS:
            self.assertEqual(html.count(f'field=form.{campo}'), 1, campo)
        self.assertIn('field=form.observaciones wide=True', html)
        self.assertIn('class="season-grid season-dates"', html)  # las dos fechas juntas
        self.assertIn("css/temporada-form.css", html)
        self.assertNotIn('activity-form-card', html)
        self.assertNotIn('style=', html)  # sin estilos en línea
        for conservado in ('{% csrf_token %}', 'form.non_field_errors', 'is_past_season', 'Esta es una temporada anterior.',
                           "{% url 'predio_detail' predio.pk %}#potrero-{{ potrero.pk }}"):
            self.assertIn(conservado, html)

    def test_estilos_centrados_de_ancho_acotado_y_una_columna_en_movil(self):
        css = self.leer('static', 'css', 'temporada-form.css')
        self.assertRegex(css, r'\.season-page \{\s*width: 100%;\s*max-width: 760px;\s*margin: 0 auto;')
        self.assertRegex(css, r'\.season-grid \{\s*display: grid;\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);')
        movil = css[css.index('@media (max-width: 600px)'):]
        for regla in ('grid-template-columns: 1fr;', 'flex-direction: column;', 'width: 100%;'):
            self.assertIn(regla, movil)
        self.assertIn('height: 84px;', css)  # observaciones más compactas
        self.assertIn('.season-actions .button {', css)
        self.assertNotIn('font-family', css)
        self.assertNotRegex(css, r'font-weight:\s*\d')
