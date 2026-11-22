"""Endpoint JSON con el tiempo de un predio (Open-Meteo). Solo lectura: no escribe en la base de datos."""

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET

from .. import clima
from ..models import Predio

# Cuánto se reutiliza una respuesta del proveedor (Open-Meteo actualiza sus modelos cada 15 minutos a 1 hora).
SEGUNDOS_EN_CACHE = 600


def _responder(datos, estado=200):
    respuesta = JsonResponse(datos, status=estado)
    add_never_cache_headers(respuesta)  # el navegador no guarda copia; el ahorro de consultas lo da la caché del servidor
    return respuesta


def _error(estado, codigo, mensaje):
    return _responder({'ok': False, 'error': {'codigo': codigo, 'mensaje': mensaje}}, estado)


@require_GET
def predio_clima_api(request, pk):
    """``GET /api/predios/<id>/clima/``: tiempo actual estimado y pronóstico de los próximos días del predio.

    Solo lo ve su propietario. Usa la geometría o las coordenadas ya guardadas; no modifica nada.
    """
    if not request.user.is_authenticated:
        return _error(401, 'no_autenticado', 'Inicia sesión para consultar el clima.')
    predio = (
        Predio.objects.filter(pk=pk, propietario=request.user)
        .only('id', 'nombre', 'latitud', 'longitud', 'geojson')
        .first()
    )
    if predio is None:
        return _error(404, 'predio_no_encontrado', 'No encontramos ese predio en tu cuenta.')
    centro = clima.centro_representativo(predio.geojson, predio.latitud, predio.longitud)
    if centro is None:
        return _error(
            422,
            'sin_ubicacion',
            'Este predio no tiene una ubicación registrada, así que no se puede consultar el clima.',
        )
    clave = clima.clave_cache(centro['latitud'], centro['longitud'])
    datos = cache.get(clave)
    en_cache = datos is not None
    if datos is None:
        try:
            datos = clima.pronostico(
                centro['latitud'], centro['longitud'], timeout=getattr(settings, 'CLIMA_TIMEOUT_SEGUNDOS', clima.TIMEOUT_SEGUNDOS)
            )
        except clima.ClimaError as exc:
            return _error(exc.estado, exc.codigo, exc.mensaje)
        cache.set(clave, datos, getattr(settings, 'CLIMA_CACHE_SEGUNDOS', SEGUNDOS_EN_CACHE))
    return _responder(
        {
            'ok': True,
            'predio': {'id': predio.pk, 'nombre': predio.nombre},
            'ubicacion': {
                'latitud': round(centro['latitud'], 5),
                'longitud': round(centro['longitud'], 5),
                'origen': centro['origen'],
                'metodo': centro['metodo'],
            },
            'proveedor': clima.PROVEEDOR,
            'obtenido_en': datos['obtenido_en'],
            'en_cache': en_cache,
            'zona_horaria': datos['zona_horaria'],
            'unidades': datos['unidades'],
            'actual': datos['actual'],
            'pronostico': datos['pronostico'],
        }
    )
