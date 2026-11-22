"""Endpoint JSON con el tiempo de un predio (Open-Meteo). Solo lectura: no escribe en la base de datos del agricultor.

La consulta al proveedor, la caché compartida entre instancias, la pausa por límite y los «datos anteriores» viven en
``core.clima_compartido``. Aquí solo se comprueba quién pregunta y de qué predio, y se arma la respuesta.
"""

from django.http import JsonResponse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_GET

from .. import clima, clima_compartido
from ..models import Predio

AVISO_DATOS_ANTERIORES = {
    'proveedor_limite': 'Open-Meteo está limitando las consultas por ahora.',
    'tiempo_agotado': 'Open-Meteo tardó demasiado en responder.',
}
AVISO_DATOS_ANTERIORES_GENERICO = 'No pudimos actualizar el pronóstico.'


def _responder(datos, estado=200, cabeceras=None):
    respuesta = JsonResponse(datos, status=estado)
    add_never_cache_headers(respuesta)  # el navegador no guarda copia; el ahorro de consultas lo da la caché del servidor
    for nombre, valor in (cabeceras or {}).items():
        respuesta[nombre] = valor
    return respuesta


def _error(estado, codigo, mensaje, reintentar_en=None):
    detalle = {'codigo': codigo, 'mensaje': mensaje}
    cabeceras = None
    if reintentar_en:
        detalle['reintentar_en'] = int(reintentar_en)  # segundos que conviene esperar antes de volver a pedirlo
        cabeceras = {'Retry-After': str(int(reintentar_en))}
    return _responder({'ok': False, 'error': detalle}, estado, cabeceras)


def _hace(segundos):
    minutos = int(segundos) // 60
    if minutos < 1:
        return 'menos de 1 minuto'
    if minutos < 60:
        return f'{minutos} min'
    horas, resto = divmod(minutos, 60)
    return f'{horas} h' if not resto else f'{horas} h {resto} min'


def _aviso(resultado):
    if not resultado.desactualizado:
        return None
    causa = AVISO_DATOS_ANTERIORES.get(resultado.motivo, AVISO_DATOS_ANTERIORES_GENERICO)
    return f'{causa} Mostramos el último pronóstico disponible, de hace {_hace(resultado.antiguedad)}.'


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
    try:
        resultado = clima_compartido.obtener_pronostico(centro['latitud'], centro['longitud'])
    except clima.ClimaError as exc:
        return _error(exc.estado, exc.codigo, exc.mensaje, exc.reintentar_en)
    datos = resultado.datos
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
            'en_cache': resultado.en_cache,
            'desactualizado': resultado.desactualizado,
            'antiguedad_segundos': resultado.antiguedad,
            'aviso': _aviso(resultado),
            'zona_horaria': datos['zona_horaria'],
            'unidades': datos['unidades'],
            'actual': datos['actual'],
            'pronostico': datos['pronostico'],
        }
    )
