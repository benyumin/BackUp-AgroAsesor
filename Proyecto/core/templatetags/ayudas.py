"""``{% ayuda 'clave' %}``: consejo breve y plegable de una sección (contenidos en core/ayudas.py)."""

from django import template

from ..ayudas import AYUDAS

register = template.Library()


@register.inclusion_tag('partials/ayuda.html', takes_context=True)
def ayuda(context, clave):
    datos = AYUDAS.get(clave)
    ocultas = context.get('ayudas_ocultas') or set()
    return {
        'clave': clave,
        'ayuda': datos,
        'abierta': bool(datos) and clave not in ocultas,
        'csrf_token': context.get('csrf_token'),
    }
