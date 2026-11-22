"""Registrar potrero: enlace «← Volver al predio» con aviso si hay cambios sin guardar.

Cambio solo de navegación: el enlace usa la ruta existente `predio_detail` del predio padre (no un ID fijo), no es un
botón del formulario y no toca el guardado. El aviso es una confirmación nativa que se muestra solo si el formulario
difiere de su estado inicial. Lo que depende del navegador (clics, confirm, mapa) se comprueba con Playwright;
aquí se protege el contrato del HTML, del CSS y del JS.
"""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Cultivo, Potrero, Predio

User = get_user_model()
PASSWORD = 'Clave-segura-123'
CORE = Path(__file__).resolve().parent.parent
CSS = (CORE / 'static' / 'css' / 'predios.css').read_text(encoding='utf-8').replace('\r\n', '\n')
JS = (CORE / 'static' / 'js' / 'potrero-volver.js').read_text(encoding='utf-8').replace('\r\n', '\n')
MESSAGE = '¿Quieres salir sin guardar? Los cambios realizados en este potrero se perderán.'


class VolverAlPredioBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('agricultor', email='a@example.com', password=PASSWORD)
        self.predio = Predio.objects.create(propietario=self.user, nombre='Mi campo', ubicacion='Melipilla', superficie_ha=10)
        self.otro = Predio.objects.create(propietario=self.user, nombre='Otro campo', ubicacion='Paine', superficie_ha=8)
        self.crop = Cultivo.objects.create(nombre='Zanahoria prueba')
        self.client.force_login(self.user)

    def page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def new_url(self, predio):
        return reverse('potrero_create', args=[predio.pk])


class EnlaceTests(VolverAlPredioBase):
    def test_link_points_to_the_detail_of_the_predio_that_owns_the_new_potrero(self):
        for predio in (self.predio, self.otro):
            with self.subTest(predio=predio.nombre):
                html = self.page(self.new_url(predio))
                expected = reverse('predio_detail', args=[predio.pk])
                self.assertRegex(
                    html,
                    r'<a class="back-to-predio"\s+id="back-to-predio"\s+href="' + re.escape(expected) + '"',
                )
                self.assertEqual(html.count('id="back-to-predio"'), 1)
                self.assertEqual(html.count('Volver al predio'), 1)

    def test_link_goes_between_the_breadcrumbs_and_the_title_outside_the_form(self):
        html = self.page(self.new_url(self.predio))
        crumbs_end = html.index('</nav>', html.index('class="crumbs"'))
        link = html.index('id="back-to-predio"')
        self.assertLess(crumbs_end, link)
        self.assertLess(link, html.index('class="page-title"'))
        self.assertLess(link, html.index('id="terrain-form"'))

    def test_link_is_a_plain_anchor_with_an_arrow_and_never_a_form_button(self):
        html = self.page(self.new_url(self.predio))
        start = html.index('<a class="back-to-predio"')
        anchor = html[start : html.index('</a>', start)]
        self.assertNotIn('<button', anchor)
        self.assertIn('<svg', anchor)  # flecha ←, sin depender de que cargue la librería de íconos
        self.assertIn('aria-hidden="true"', anchor)
        self.assertEqual(html.count('Guardar potrero'), 1)  # «Guardar potrero» queda igual

    def test_link_is_only_on_the_new_potrero_page(self):
        potrero = Potrero.objects.create(predio=self.predio, nombre='Lote A', superficie_ha=2, cultivo=self.crop)
        for url in (
            reverse('potrero_edit', args=[self.predio.pk, potrero.pk]),
            reverse('predio_create'),
            reverse('predio_edit', args=[self.predio.pk]),
        ):
            with self.subTest(url=url):
                html = self.page(url)
                self.assertNotIn('back-to-predio', html)
                self.assertNotIn('potrero-volver.js', html)

    def test_confirmation_script_loads_after_the_map_script(self):
        html = self.page(self.new_url(self.predio))
        self.assertEqual(html.count('js/potrero-volver.js'), 1)
        self.assertLess(html.index('js/predios-map.js'), html.index('js/potrero-volver.js'))

    def test_form_that_comes_back_with_errors_counts_as_unsaved(self):
        before = Potrero.objects.count()
        self.assertNotIn('data-unsaved-start', self.page(self.new_url(self.predio)))
        response = self.client.post(self.new_url(self.predio), {'nombre': '', 'superficie_ha': ''})
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-unsaved-start', response.content.decode())
        self.assertEqual(Potrero.objects.count(), before)

    def test_leaving_through_the_link_creates_and_changes_nothing(self):
        before = (Potrero.objects.count(), Predio.objects.count())
        html = self.page(self.new_url(self.predio))
        href = re.search(r'id="back-to-predio"\s+href="([^"]+)"', html).group(1)
        self.assertEqual(self.client.get(href).status_code, 200)
        self.assertEqual((Potrero.objects.count(), Predio.objects.count()), before)


class ComportamientoJsTests(TestCase):
    def test_confirmation_uses_the_requested_text_and_the_native_confirm(self):
        self.assertIn(MESSAGE, JS)
        self.assertIn('window.confirm(MESSAGE)', JS)
        # Cancelar frena la navegación; sin cambios no se pregunta nada.
        self.assertIn('if (!window.confirm(MESSAGE)) event.preventDefault();', JS)
        self.assertIn('if (!hasUnsavedChanges()) return;', JS)

    def test_changes_are_compared_with_the_initial_state_taken_after_the_map_starts(self):
        self.assertIn('const initialState = snapshot();', JS)
        self.assertIn('snapshot() !== initialState', JS)
        self.assertIn("name !== 'csrfmiddlewaretoken'", JS)
        self.assertIn('new FormData(form)', JS)  # nombre, cultivo, superficie, ubicación y el contorno oculto

    def test_script_only_reads_the_form(self):
        for forbidden in ('history.back', 'history.go', 'beforeunload', 'submit', 'fetch(', 'XMLHttpRequest', 'localStorage', 'sessionStorage', '.reset('):
            self.assertNotIn(forbidden, JS, forbidden)

    def test_link_is_looked_up_once_and_guarded(self):
        self.assertEqual(JS.count("getElementById('back-to-predio')"), 1)
        self.assertEqual(JS.count("link.addEventListener('click'"), 1)
        self.assertIn('if (!link || !form) return;', JS)


class EstilosTests(TestCase):
    def test_link_has_hover_and_visible_focus_in_the_corporate_green(self):
        css = re.sub(r'\s+', ' ', CSS)
        self.assertIn('.back-to-predio { display: inline-flex; align-items: center;', css)
        self.assertIn('color: var(--green);', css[css.index('.back-to-predio {') :])
        self.assertIn('.back-to-predio:hover {', css)
        self.assertIn('.back-to-predio:focus-visible { border-radius: 8px; outline: 2px solid var(--green);', css)
        self.assertIn('prefers-reduced-motion', css[css.index('.back-to-predio:focus-visible') :])
        self.assertNotIn('font-family', css[css.index('.back-to-predio {') : css.index('.back-to-predio:focus-visible')])

    def test_rules_live_before_the_scoped_predio_block(self):
        self.assertLess(
            CSS.index('.back-to-predio {'),
            CSS.index('Registrar / editar predio: mapa a la izquierda'),
        )
