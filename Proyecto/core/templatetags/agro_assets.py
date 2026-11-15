"""Librerías del navegador (íconos, mapa, PDF, fuentes): copia local primero, CDN solo si falta.

Si existe ``core/static/vendor/<archivo>`` (lo crea ``descargar-librerias.ps1``), la página lo usa y deja de
depender de internet para dibujar íconos, menús y mapas. Si no existe, usa el CDN como hasta ahora.
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def vendor(archivo, cdn):
    ruta = f'vendor/{archivo}'
    return static(ruta) if finders.find(ruta) else cdn


@register.filter
def cifra(valor, decimales=2):
    """Número en formato chileno sin ceros de relleno: 12.5 → «12,5», 32000 → «32.000», 0.0125 → «0,0125».

    ``decimales`` es el máximo que se muestra; los valores menores que 1 se muestran con hasta cuatro
    decimales para no esconder superficies chicas.
    """
    try:
        numero = Decimal(str(valor))
        decimales = int(decimales)
    except (InvalidOperation, TypeError, ValueError):
        return valor if valor is not None else ''
    if 0 < abs(numero) < 1:
        decimales = max(decimales, 4)
    redondeado = round(numero, decimales)
    entero, _, fraccion = f'{redondeado:f}'.partition('.')
    fraccion = fraccion.rstrip('0')
    signo = '-' if entero.startswith('-') else ''
    entero = f'{int(entero.lstrip("-") or 0):,}'.replace(',', '.')
    return f'{signo}{entero},{fraccion}' if fraccion else f'{signo}{entero}'


def _anillos_exteriores(geometria):
    """Anillos exteriores de un GeoJSON (Feature, Polygon o MultiPolygon) o de una lista simple de puntos."""
    if isinstance(geometria, dict):
        if geometria.get('type') == 'Feature':
            return _anillos_exteriores(geometria.get('geometry'))
        if geometria.get('type') == 'FeatureCollection':
            return [anillo for feature in geometria.get('features') or [] for anillo in _anillos_exteriores(feature)]
        coordenadas = geometria.get('coordinates') or []
        if geometria.get('type') == 'Polygon':
            return [coordenadas[0]] if coordenadas else []
        if geometria.get('type') == 'MultiPolygon':
            return [poligono[0] for poligono in coordenadas if poligono]
        return []
    if isinstance(geometria, (list, tuple)) and geometria and isinstance(geometria[0], (list, tuple)):
        return [geometria] if not isinstance(geometria[0][0], (list, tuple)) else [geometria[0]]
    return []


@register.filter
def vertices_geograficos(geometria):
    """Vértices de una geometría guardada, listos para mostrar: [[{numero, latitud, longitud}, …], …].

    GeoJSON guarda cada punto como [longitud, latitud]; aquí se entregan ya identificados. El punto de cierre
    (igual al primero) no se repite. No modifica la geometría: solo la presenta.
    """
    partes = []
    for anillo in _anillos_exteriores(geometria):
        puntos = []
        for punto in anillo:
            try:
                longitud, latitud = float(punto[0]), float(punto[1])
            except (TypeError, ValueError, IndexError):
                continue
            puntos.append((latitud, longitud))
        if len(puntos) > 1 and puntos[0] == puntos[-1]:
            puntos = puntos[:-1]
        if puntos:
            partes.append(
                [
                    {'numero': numero, 'latitud': f'{latitud:.6f}', 'longitud': f'{longitud:.6f}'}
                    for numero, (latitud, longitud) in enumerate(puntos, start=1)
                ]
            )
    return partes
