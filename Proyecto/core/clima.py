"""Pronóstico del tiempo de un predio, consultado a Open-Meteo (https://open-meteo.com).

Open-Meteo es gratuito, no pide clave de API (uso no comercial) y sus datos tienen licencia CC BY 4.0: hay que citarlo.
Este módulo no usa la base de datos ni las vistas: recibe coordenadas, consulta al proveedor, valida y normaliza.

Qué se muestra y de dónde sale:

* ``actual``: el instante más reciente que estima el modelo (bloque ``current`` de Open-Meteo, cada 15 minutos).
  NO es una medición de una estación: es una estimación de un modelo meteorológico.
* ``pronostico``: valores diarios futuros (bloque ``daily``): temperatura mínima y máxima, probabilidad máxima de
  precipitación, lluvia acumulada y viento máximo. La probabilidad de precipitación existe solo en el pronóstico
  (Open-Meteo no la entrega como valor «actual»), por eso no aparece junto a los valores actuales.

Si el proveedor no responde o entrega algo inesperado se lanza ``ClimaError``. Nunca se rellena con valores supuestos:
un dato fuera de rango o ausente se informa como ``None``.

Ubicación del predio: si tiene polígono, se usa su centro representativo (ver ``centro_representativo``) sin modificar
la geometría guardada; si no, las coordenadas guardadas del predio; si tampoco existen, no se consulta nada.
"""

import http.client
import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from django.core.exceptions import ValidationError

from .geometry import coordinates_from_geojson, point_in_polygon

URL_API = 'https://api.open-meteo.com/v1/forecast'
PROVEEDOR = {'nombre': 'Open-Meteo', 'url': 'https://open-meteo.com/', 'licencia': 'CC BY 4.0'}
DIAS_PRONOSTICO = 5  # hoy y los 4 días siguientes
TIMEOUT_SEGUNDOS = 6
MAX_BYTES_RESPUESTA = 200_000
DECIMALES_COORDENADA = 3  # ~110 m: mucho menos que la resolución de los modelos (1 a 11 km)

VARIABLES_ACTUALES = ('temperature_2m', 'relative_humidity_2m', 'wind_speed_10m', 'precipitation', 'weather_code')
VARIABLES_DIARIAS = (
    'weather_code',
    'temperature_2m_max',
    'temperature_2m_min',
    'precipitation_probability_max',
    'precipitation_sum',
    'wind_speed_10m_max',
)
# Unidades que se piden de forma explícita (así no dependen de los valores por defecto del proveedor).
UNIDADES_SOLICITADAS = {'temperature_unit': 'celsius', 'wind_speed_unit': 'kmh', 'precipitation_unit': 'mm'}
UNIDADES_ESPERADAS = {
    'temperatura': '°C',
    'humedad': '%',
    'viento': 'km/h',
    'precipitacion': 'mm',
    'prob_precipitacion': '%',
}
# Código WMO (weather_code) → (descripción, icono de Lucide).
CODIGOS_TIEMPO = {
    0: ('Despejado', 'sun'),
    1: ('Mayormente despejado', 'sun'),
    2: ('Parcialmente nublado', 'cloud-sun'),
    3: ('Nublado', 'cloud'),
    45: ('Niebla', 'cloud-fog'),
    48: ('Niebla con escarcha', 'cloud-fog'),
    51: ('Llovizna ligera', 'cloud-drizzle'),
    53: ('Llovizna moderada', 'cloud-drizzle'),
    55: ('Llovizna intensa', 'cloud-drizzle'),
    56: ('Llovizna helada ligera', 'cloud-drizzle'),
    57: ('Llovizna helada intensa', 'cloud-drizzle'),
    61: ('Lluvia ligera', 'cloud-rain'),
    63: ('Lluvia moderada', 'cloud-rain'),
    65: ('Lluvia intensa', 'cloud-rain'),
    66: ('Lluvia helada ligera', 'cloud-rain'),
    67: ('Lluvia helada intensa', 'cloud-rain'),
    71: ('Nevada ligera', 'snowflake'),
    73: ('Nevada moderada', 'snowflake'),
    75: ('Nevada intensa', 'snowflake'),
    77: ('Granos de nieve', 'snowflake'),
    80: ('Chubascos ligeros', 'cloud-rain'),
    81: ('Chubascos moderados', 'cloud-rain'),
    82: ('Chubascos violentos', 'cloud-rain'),
    85: ('Chubascos de nieve ligeros', 'snowflake'),
    86: ('Chubascos de nieve intensos', 'snowflake'),
    95: ('Tormenta eléctrica', 'cloud-lightning'),
    96: ('Tormenta con granizo ligero', 'cloud-lightning'),
    99: ('Tormenta con granizo intenso', 'cloud-lightning'),
}


class ClimaError(Exception):
    """Fallo controlado al obtener el pronóstico: lleva un código estable, un mensaje para la persona y el estado HTTP."""

    def __init__(self, codigo, mensaje, estado=502):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.estado = estado


# ---------------------------------------------------------------- ubicación del predio

def _punto_valido(punto):
    try:
        lng, lat = punto
    except (TypeError, ValueError):
        return False
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lng, lat)
    ) and -180 <= lng <= 180 and -85 <= lat <= 85


def _centroide(puntos):
    """Centroide del polígono ponderado por área.

    Se trabaja en una proyección plana local (equirectangular) alrededor del centro de los vértices: la longitud se
    escala por cos(latitud) para que las distancias este-oeste y norte-sur sean comparables. Para predios (hectáreas
    a pocos kilómetros) el error es despreciable. Si el área es casi cero se usa el promedio de los vértices.
    """
    lng0 = sum(p[0] for p in puntos) / len(puntos)
    lat0 = sum(p[1] for p in puntos) / len(puntos)
    k = math.cos(math.radians(lat0))
    xy = [((p[0] - lng0) * k, p[1] - lat0) for p in puntos]
    area2 = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        cruce = x1 * y2 - x2 * y1
        area2 += cruce
        cx += (x1 + x2) * cruce
        cy += (y1 + y2) * cruce
    if abs(area2) < 1e-14 or k <= 0:
        return lng0, lat0
    return lng0 + (cx / (3 * area2)) / k, lat0 + cy / (3 * area2)


def _punto_interior_mas_ancho(puntos, lat):
    """Punto medio del tramo más ancho de la recta de latitud ``lat`` que queda dentro del polígono (o ``None``)."""
    cortes = []
    for (x1, y1), (x2, y2) in zip(puntos, puntos[1:] + puntos[:1]):
        if (y1 > lat) != (y2 > lat):
            cortes.append((x2 - x1) * (lat - y1) / (y2 - y1) + x1)
    cortes.sort()
    tramos = [(cortes[i], cortes[i + 1]) for i in range(0, len(cortes) - 1, 2)]
    if not tramos:
        return None
    desde, hasta = max(tramos, key=lambda tramo: tramo[1] - tramo[0])
    return (desde + hasta) / 2


def centro_representativo(geojson, latitud, longitud):
    """Punto con el que se consulta el tiempo de un predio. Devuelve un dict o ``None`` si no hay ubicación.

    1. Con polígono: centroide ponderado por área. Si el predio es cóncavo y el centroide cae fuera del polígono, se
       usa el punto medio del tramo más ancho del polígono a esa misma latitud (siempre queda dentro).
    2. Sin polígono utilizable: las coordenadas guardadas del predio (``latitud`` y ``longitud``), si ambas existen.
    3. Sin ninguna: ``None``. No se inventa ni se usa una ubicación fija.

    La geometría guardada nunca se modifica.
    """
    puntos = None
    if geojson:
        try:
            puntos = [list(p) for p in coordinates_from_geojson(geojson)]
        except (ValidationError, TypeError):
            puntos = None
    if puntos and len(puntos) >= 3 and all(_punto_valido(p) for p in puntos):
        if max(p[0] for p in puntos) - min(p[0] for p in puntos) < 180:
            lng, lat = _centroide(puntos)
            metodo = 'Centroide del polígono, ponderado por área'
            if not point_in_polygon((lng, lat), puntos):
                lng_interior = _punto_interior_mas_ancho(puntos, lat)
                if lng_interior is not None:
                    lng = lng_interior
                    metodo = 'Punto interior más ancho del polígono (el centroide cae fuera de un predio cóncavo)'
            if _punto_valido((lng, lat)):
                return {'latitud': lat, 'longitud': lng, 'origen': 'poligono', 'metodo': metodo}
    if _punto_valido((longitud, latitud)):
        return {
            'latitud': float(latitud),
            'longitud': float(longitud),
            'origen': 'coordenadas_guardadas',
            'metodo': 'Coordenadas guardadas del predio',
        }
    return None


# ---------------------------------------------------------------- consulta a Open-Meteo

def _redondear(valor):
    return round(float(valor), DECIMALES_COORDENADA)


def clave_cache(latitud, longitud):
    return f'clima:v1:{_redondear(latitud):.{DECIMALES_COORDENADA}f}:{_redondear(longitud):.{DECIMALES_COORDENADA}f}'


def construir_url(latitud, longitud):
    consulta = {
        'latitude': f'{_redondear(latitud):.{DECIMALES_COORDENADA}f}',
        'longitude': f'{_redondear(longitud):.{DECIMALES_COORDENADA}f}',
        'current': ','.join(VARIABLES_ACTUALES),
        'daily': ','.join(VARIABLES_DIARIAS),
        'timezone': 'auto',
        'forecast_days': DIAS_PRONOSTICO,
        **UNIDADES_SOLICITADAS,
    }
    return f'{URL_API}?{urllib.parse.urlencode(consulta, safe=",")}'


def _abrir(url, timeout):
    """Única función que sale a internet (las pruebas la sustituyen). Devuelve ``(estado, cuerpo_en_bytes)``."""
    peticion = urllib.request.Request(
        url, headers={'Accept': 'application/json', 'User-Agent': 'AgroAsesor/1.0 (proyecto academico)'}
    )
    with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:  # URL https fija (URL_API)
        return respuesta.status, respuesta.read(MAX_BYTES_RESPUESTA + 1)


def consultar(latitud, longitud, timeout=TIMEOUT_SEGUNDOS):
    """Pide el pronóstico a Open-Meteo y devuelve el JSON ya decodificado (sin validar el contenido)."""
    try:
        estado, cuerpo = _abrir(construir_url(latitud, longitud), timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ClimaError(
                'proveedor_limite', 'Open-Meteo recibió demasiadas consultas. Intenta de nuevo en unos minutos.', 503
            ) from exc
        raise ClimaError('proveedor_error', 'Open-Meteo respondió con un error. Intenta de nuevo más tarde.') from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ClimaError('tiempo_agotado', 'Open-Meteo tardó demasiado en responder.', 504) from exc
        raise ClimaError('proveedor_no_disponible', 'No se pudo conectar con Open-Meteo en este momento.') from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ClimaError('tiempo_agotado', 'Open-Meteo tardó demasiado en responder.', 504) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ClimaError('proveedor_no_disponible', 'No se pudo conectar con Open-Meteo en este momento.') from exc
    if estado != 200 or len(cuerpo) > MAX_BYTES_RESPUESTA:
        raise ClimaError('respuesta_invalida', 'Open-Meteo entregó una respuesta que no se pudo usar.')
    try:
        datos = json.loads(cuerpo.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ClimaError('respuesta_invalida', 'Open-Meteo entregó una respuesta que no se pudo usar.') from exc
    return datos


# ---------------------------------------------------------------- validación y normalización

def _numero(valor, minimo, maximo, decimales=1):
    """Número finito dentro de un rango físicamente posible; si no, ``None`` (nunca se corrige ni se inventa)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return None
    if not minimo <= valor <= maximo:
        return None
    return round(valor, decimales) if decimales else int(round(valor))


def _hora_iso(valor):
    if not isinstance(valor, str):
        return None
    try:
        datetime.fromisoformat(valor)
    except ValueError:
        return None
    return valor


def _fecha_iso(valor):
    if not isinstance(valor, str):
        return None
    try:
        date.fromisoformat(valor)
    except ValueError:
        return None
    return valor


def _codigo_tiempo(valor):
    codigo = _numero(valor, 0, 99, decimales=0)
    if codigo is None or codigo not in CODIGOS_TIEMPO:
        return None, None, None
    descripcion, icono = CODIGOS_TIEMPO[codigo]
    return codigo, descripcion, icono


def _unidades(bloque, claves):
    """Unidad que informa el proveedor; si falta o es rara, la que se pidió de forma explícita."""
    resultado = {}
    for nombre, clave in claves.items():
        valor = bloque.get(clave) if isinstance(bloque, dict) else None
        resultado[nombre] = valor if isinstance(valor, str) and 0 < len(valor) <= 12 else UNIDADES_ESPERADAS[nombre]
    return resultado


def _normalizar_actual(datos):
    actual = datos.get('current')
    if not isinstance(actual, dict):
        return None
    hora = _hora_iso(actual.get('time'))
    if hora is None:
        return None
    codigo, descripcion, icono = _codigo_tiempo(actual.get('weather_code'))
    intervalo = _numero(actual.get('interval'), 60, 24 * 60 * 60, decimales=0)  # segundos
    resultado = {
        'tipo': 'estimado_por_modelo',
        'hora': hora,
        'intervalo_min': intervalo // 60 if intervalo else None,
        'temperatura': _numero(actual.get('temperature_2m'), -90, 65),
        'humedad': _numero(actual.get('relative_humidity_2m'), 0, 100, decimales=0),
        'viento': _numero(actual.get('wind_speed_10m'), 0, 450),
        'precipitacion': _numero(actual.get('precipitation'), 0, 1000),
        'codigo_tiempo': codigo,
        'descripcion': descripcion,
        'icono': icono,
    }
    valores = [resultado[k] for k in ('temperatura', 'humedad', 'viento', 'precipitacion')]
    return resultado if any(v is not None for v in valores) else None


def _normalizar_pronostico(datos):
    diario = datos.get('daily')
    if not isinstance(diario, dict) or not isinstance(diario.get('time'), list):
        return []
    fechas = diario['time'][:DIAS_PRONOSTICO]

    def serie(nombre):
        valores = diario.get(nombre)
        return valores if isinstance(valores, list) else []

    series = {nombre: serie(nombre) for nombre in VARIABLES_DIARIAS}
    dias = []
    for indice, texto in enumerate(fechas):
        fecha = _fecha_iso(texto)
        if fecha is None:
            continue

        def valor(nombre):
            lista = series[nombre]
            return lista[indice] if indice < len(lista) else None

        codigo, descripcion, icono = _codigo_tiempo(valor('weather_code'))
        dia = {
            'tipo': 'pronostico',
            'fecha': fecha,
            'temp_max': _numero(valor('temperature_2m_max'), -90, 65),
            'temp_min': _numero(valor('temperature_2m_min'), -90, 65),
            'prob_precipitacion': _numero(valor('precipitation_probability_max'), 0, 100, decimales=0),
            'precipitacion': _numero(valor('precipitation_sum'), 0, 2000),
            'viento_max': _numero(valor('wind_speed_10m_max'), 0, 450),
            'codigo_tiempo': codigo,
            'descripcion': descripcion,
            'icono': icono,
        }
        if any(dia[k] is not None for k in ('temp_max', 'temp_min', 'prob_precipitacion', 'precipitacion', 'viento_max')):
            dias.append(dia)
    return dias


def normalizar(datos):
    """Valida la respuesta de Open-Meteo y devuelve solo lo necesario. Lanza ``ClimaError`` si no sirve."""
    if not isinstance(datos, dict) or datos.get('error'):
        raise ClimaError('proveedor_error', 'Open-Meteo no pudo entregar el pronóstico para esta ubicación.')
    actual = _normalizar_actual(datos)
    pronostico = _normalizar_pronostico(datos)
    if actual is None and not pronostico:
        raise ClimaError('respuesta_invalida', 'Open-Meteo entregó una respuesta sin datos utilizables.')
    zona = datos.get('timezone')
    unidades = _unidades(datos.get('current_units'), {
        'temperatura': 'temperature_2m', 'humedad': 'relative_humidity_2m',
        'viento': 'wind_speed_10m', 'precipitacion': 'precipitation',
    })
    unidades_diarias = _unidades(datos.get('daily_units'), {
        'prob_precipitacion': 'precipitation_probability_max',
        'temperatura': 'temperature_2m_max', 'viento': 'wind_speed_10m_max', 'precipitacion': 'precipitation_sum',
    })
    unidades['prob_precipitacion'] = unidades_diarias['prob_precipitacion']
    return {
        'zona_horaria': zona if isinstance(zona, str) and 0 < len(zona) <= 64 else None,
        'actual': actual,
        'pronostico': pronostico,
        'unidades': unidades,
    }


def pronostico(latitud, longitud, timeout=TIMEOUT_SEGUNDOS):
    """Consulta, valida y normaliza. ``obtenido_en`` es el momento (UTC) en que se pidió a Open-Meteo."""
    datos = normalizar(consultar(latitud, longitud, timeout))
    datos['obtenido_en'] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return datos
