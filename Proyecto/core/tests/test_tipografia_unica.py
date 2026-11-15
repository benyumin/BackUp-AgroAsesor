"""Una sola tipografía (Outfit) en todo el proyecto: plataforma, acceso y páginas de la API Flask."""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

RAIZ = Path(__file__).resolve().parent.parent.parent
OTRAS = re.compile(r'Source Sans|source-sans|family=Inter|\bInter\b|Roboto|Poppins|Montserrat|Arial|Helvetica', re.I)


class TipografiaUnicaTests(TestCase):
    def fuentes_declaradas(self, texto):
        return re.findall(r'font-family\s*:\s*([^;}]+)', texto)

    def test_todas_las_hojas_de_estilo_y_plantillas_declaran_outfit(self):
        archivos = [
            *sorted((RAIZ / 'core' / 'static' / 'css').glob('*.css')),
            *sorted((RAIZ / 'core' / 'templates').rglob('*.html')),
            *sorted((RAIZ / 'backend_flask' / 'app' / 'templates').glob('*.html')),
        ]
        self.assertGreater(len(archivos), 20)
        for archivo in archivos:
            texto = archivo.read_text(encoding='utf-8')
            self.assertIsNone(OTRAS.search(texto), f'{archivo.name}: tipografía distinta de Outfit')
            for declaracion in self.fuentes_declaradas(texto):
                valor = declaracion.strip()
                # Solo se aceptan Outfit, herencia, variables del tema y monoespaciada para código.
                self.assertTrue(
                    valor.startswith(('Outfit', 'inherit', 'var(--')) or 'monospace' in valor,
                    f'{archivo.name}: font-family: {valor}',
                )

    def test_la_base_de_la_plataforma_y_las_paginas_flask_usan_outfit(self):
        body = (RAIZ / 'core' / 'static' / 'css' / 'body.css').read_text(encoding='utf-8')
        self.assertIn('font-family: var(--font-sans, Outfit, "Segoe UI", sans-serif);', body)
        tema = (RAIZ / 'core' / 'static' / 'css' / 'tema-figma.css').read_text(encoding='utf-8')
        self.assertIn('--font-sans: Outfit, "Segoe UI", sans-serif;', tema)
        for nombre in ('consola.html', 'catalogo.html'):
            html = (RAIZ / 'backend_flask' / 'app' / 'templates' / nombre).read_text(encoding='utf-8')
            self.assertIn('family=Outfit', html, nombre)

    def test_las_paginas_cargan_solo_la_fuente_outfit(self):
        user = get_user_model().objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        paginas = ['/login/', '/login/?mode=register', '/cuenta/recuperar/', '/politicas/privacidad-y-cookies/']
        for url in paginas:
            html = self.client.get(url).content.decode()
            self.assertRegex(html, r'outfit\.css|family=Outfit', url)
            self.assertIsNone(OTRAS.search(html), url)
        self.client.force_login(user)
        for url in ('/', '/predios/', '/productos/', '/decisiones/', '/actividades/', '/reportes/', '/calendario/',
                    '/busqueda/', '/predios/nuevo/', '/cuenta/perfil/'):
            html = self.client.get(url).content.decode()
            self.assertRegex(html, r'outfit\.css|family=Outfit', url)
            self.assertNotRegex(html, r'family=(?!Outfit)[A-Z]', url)  # ninguna otra fuente de Google
