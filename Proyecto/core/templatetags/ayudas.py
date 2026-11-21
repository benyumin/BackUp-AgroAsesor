"""``{% ayuda 'clave' %}``: consejo breve y plegable de una sección (contenidos en core/ayudas.py)."""

from django import template

from ..ayudas import AYUDAS

register = template.Library()


@register.inclusion_tag('partials/ayuda.html')
def ayuda(clave):
    return {'clave': clave, 'ayuda': AYUDAS.get(clave)}
