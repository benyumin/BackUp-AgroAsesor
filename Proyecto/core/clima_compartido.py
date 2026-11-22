"""Pronóstico del tiempo con caché COMPARTIDA entre instancias, pausa por límite de consultas y «datos anteriores».

Por qué existe: en Vercel cada instancia serverless tiene su propia memoria, así que la caché local de Django casi nunca
se reutiliza y cada visita acababa consultando a Open-Meteo. Además Open-Meteo cuenta los límites por dirección IP de
salida (600 consultas/minuto, 5 000/hora, 10 000/día en el plan gratuito) y en Vercel esa IP se comparte con otras apps.

Qué hace, en orden, ``obtener_pronostico``:

1. Reutiliza un pronóstico vigente (memoria de la instancia → tabla compartida ``core_cachecompartida``). Predios cuyas
   coordenadas coinciden al redondearlas (``CLIMA_PRECISION_COORDENADAS``, por defecto 2 decimales ≈ 1,1 km) comparten
   la misma entrada: más cercanos que la resolución de los modelos (1 a 11 km) y con un solo pedido al proveedor.
2. Si Open-Meteo pidió esperar (HTTP 429), respeta la pausa para TODAS las instancias; no reintenta mientras dure.
3. Si no, consulta una sola vez: otra petición que llegue al mismo tiempo espera un momento acotado a que termine la
   primera en vez de repetir la consulta (``CLIMA_ESPERA_CONCURRENTE_SEGUNDOS``).
4. Si el proveedor falla (límite, error 5xx, tiempo agotado) y hay un pronóstico anterior de hasta
   ``CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS``, lo devuelve marcado como ``desactualizado``. Nunca se inventan datos: sin
   pronóstico anterior se propaga el ``ClimaError``.

Si la tabla compartida no existe todavía (migración 0024 sin aplicar) o la base falla, se sigue con la memoria de la
instancia (sin romper la página) y se vuelve a intentar con la tabla pasado un minuto.

Privacidad: la entrada compartida es solo el pronóstico público de una zona, con clave opaca (ver ``cache_compartida``).
Quién consulta, su predio y su ubicación exacta no se guardan; esos permisos los sigue comprobando la vista.
"""

import logging
import threading
import time
from collections import namedtuple
from types import SimpleNamespace

from django.conf import settings
from django.core.cache import cache as cache_local
from django.db import Error as ErrorBaseDatos
from django.db import transaction

from . import cache_compartida as almacen
from . import clima

logger = logging.getLogger(__name__)

# datos: pronóstico normalizado · en_cache: vino de la caché · desactualizado: es anterior porque no se pudo actualizar
# antiguedad: segundos desde que se pidió a Open-Meteo · motivo: código del fallo cuando está desactualizado.
Resultado = namedtuple('Resultado', 'datos en_cache desactualizado antiguedad motivo')

CLAVE_LIMITE = 'clima-limite'
PAUSA_MINIMA = 5  # segundos
_FALLO = object()  # la tabla compartida no respondió

_ahora = time.time  # sustituibles en las pruebas
_dormir = time.sleep
_monotonico = time.monotonic
_bd_caida_hasta = 0.0
_candados = {}
_guardia = threading.Lock()


def reiniciar():
    """Olvida el estado interno de este proceso (solo para pruebas)."""
    global _bd_caida_hasta
    _bd_caida_hasta = 0.0
    with _guardia:
        _candados.clear()


def _config():
    def valor(nombre, defecto):
        return getattr(settings, nombre, defecto)

    return SimpleNamespace(
        ttl=int(valor('CLIMA_CACHE_SEGUNDOS', 900)),
        antiguo_max=int(valor('CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS', 6 * 3600)),
        decimales=int(valor('CLIMA_PRECISION_COORDENADAS', 2)),
        timeout=float(valor('CLIMA_TIMEOUT_SEGUNDOS', clima.TIMEOUT_SEGUNDOS)),
        pausa=int(valor('CLIMA_ESPERA_LIMITE_SEGUNDOS', 120)),
        pausa_max=int(valor('CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS', 1800)),
        espera_concurrente=float(valor('CLIMA_ESPERA_CONCURRENTE_SEGUNDOS', 3)),
        clave_api=str(valor('CLIMA_API_KEY', '') or ''),
    )


# ---------------------------------------------------------------- acceso a la tabla compartida

def _bd(funcion, *args):
    """Ejecuta una operación sobre la tabla compartida. Si la base falla devuelve ``_FALLO`` y, durante un minuto,
    esta instancia usa solo su memoria (el clima nunca debe romper la página por la caché)."""
    global _bd_caida_hasta
    if not getattr(settings, 'CLIMA_CACHE_COMPARTIDA', True) or _ahora() < _bd_caida_hasta:
        return _FALLO
    try:
        with transaction.atomic():  # punto de guardado: un error no deja rota una transacción en curso
            return funcion(*args)
    except ErrorBaseDatos as exc:
        _bd_caida_hasta = _ahora() + 60
        logger.warning(
            'Caché compartida del clima no disponible (%s); se usa la memoria de esta instancia. '
            '¿Se aplicó la migración 0024?',
            type(exc).__name__,
        )
        return _FALLO


def _leer(clave, cfg):
    """Entrada más reciente entre la memoria local y la tabla compartida (``None`` si no hay)."""
    guardado = cache_local.get(clave)
    registro = almacen.Registro(*guardado) if guardado else None
    if registro and registro.vence > _ahora():
        return registro  # vigente: no hace falta ir a la base
    remoto = _bd(almacen.leer, clave)
    if remoto is not _FALLO and remoto is not None and (registro is None or remoto.obtenido > registro.obtenido):
        registro = remoto
        cache_local.set(clave, tuple(remoto), max(60, cfg.antiguo_max, cfg.ttl))
    return registro


def _guardar(clave, datos, cfg):
    ahora = int(_ahora())
    vence = ahora + cfg.ttl
    cache_local.set(clave, tuple(almacen.Registro(datos, ahora, vence)), max(60, cfg.antiguo_max, cfg.ttl))
    _bd(almacen.guardar, clave, datos, ahora, vence)
    _bd(almacen.purgar, ahora - max(cfg.antiguo_max, 3600))


def _segundos_de_pausa():
    """Segundos que faltan de la pausa por límite del proveedor (0 si no hay pausa)."""
    ahora = _ahora()
    hasta = cache_local.get(CLAVE_LIMITE)
    if not hasta or hasta <= ahora:
        remoto = _bd(almacen.leer, CLAVE_LIMITE)
        hasta = remoto.vence if remoto not in (None, _FALLO) and remoto.vence > ahora else 0
        if hasta:
            cache_local.set(CLAVE_LIMITE, hasta, int(hasta - ahora) + 1)
    return int(hasta - ahora) + 1 if hasta and hasta > ahora else 0


def _fijar_pausa(sugerida, cfg):
    segundos = min(max(sugerida or cfg.pausa, PAUSA_MINIMA), cfg.pausa_max)
    ahora = int(_ahora())
    hasta = ahora + segundos
    cache_local.set(CLAVE_LIMITE, hasta, segundos + 1)
    _bd(almacen.guardar, CLAVE_LIMITE, {'hasta': hasta}, ahora, hasta)
    return segundos


# ---------------------------------------------------------------- coordinación entre peticiones

def _candado(clave):
    with _guardia:
        if len(_candados) > 512:
            _candados.clear()  # quien ya tiene uno lo sigue usando; solo se pierde la coordinación del resto
        return _candados.setdefault(clave, threading.Lock())


def _esperar_resultado(clave, cfg):
    """Sondeo acotado: otra petición está consultando lo mismo; se espera a que deje el resultado."""
    limite = _monotonico() + cfg.espera_concurrente
    while _monotonico() < limite:
        _dormir(0.4)
        registro = _leer(clave, cfg)
        if registro and registro.vence > _ahora():
            return registro
    return None


# ---------------------------------------------------------------- resultado

def _resultado(registro, desactualizado=False, motivo=None):
    return Resultado(registro.valor, True, desactualizado, max(0, int(_ahora() - registro.obtenido)), motivo)


def _o_datos_anteriores(registro, cfg, error):
    """Ante un fallo del proveedor: el último pronóstico válido (marcado) si no es demasiado viejo; si no, el error."""
    if registro is not None and 0 < cfg.antiguo_max and _ahora() - registro.obtenido <= cfg.antiguo_max:
        return _resultado(registro, True, error.codigo)
    raise error


def obtener_pronostico(latitud, longitud):
    """Pronóstico de esa zona. Lanza ``clima.ClimaError`` si no hay datos nuevos ni anteriores utilizables."""
    cfg = _config()
    lat = clima.redondear(latitud, cfg.decimales)
    lon = clima.redondear(longitud, cfg.decimales)
    clave = almacen.clave_opaca('clima', f'{lat:.{cfg.decimales}f}', f'{lon:.{cfg.decimales}f}')

    registro = _leer(clave, cfg)
    if registro and registro.vence > _ahora():
        return _resultado(registro)

    pausa = _segundos_de_pausa()
    if pausa:
        return _o_datos_anteriores(
            registro,
            cfg,
            clima.ClimaError(
                'proveedor_limite',
                'Open-Meteo pidió espaciar las consultas. Intenta de nuevo en unos minutos.',
                503,
                reintentar_en=pausa,
            ),
        )

    candado = _candado(clave)
    tiene_candado = candado.acquire(timeout=cfg.espera_concurrente)  # sin él se consulta igual: no se bloquea a nadie
    marca = clave.replace('clima:', 'clima-consulta:', 1)
    reclamada = False
    try:
        registro = _leer(clave, cfg)  # quizá otra petición lo guardó mientras se esperaba el candado
        if registro and registro.vence > _ahora():
            return _resultado(registro)
        toma = _bd(almacen.reclamar, marca, int(_ahora()), int(cfg.timeout) + 3)
        reclamada = toma is True
        if toma is False:
            # Otra instancia está consultando lo mismo: se espera su resultado un tiempo acotado.
            esperado = _esperar_resultado(clave, cfg)
            if esperado is not None:
                return _resultado(esperado)
        try:
            datos = clima.pronostico(lat, lon, cfg.timeout, cfg.decimales, cfg.clave_api or None)
        except clima.ClimaError as error:
            if error.codigo == 'proveedor_limite':
                error.reintentar_en = _fijar_pausa(error.reintentar_en, cfg)
                logger.warning('Open-Meteo limitó las consultas: pausa compartida de %s s.', error.reintentar_en)
            else:
                logger.warning('Open-Meteo no entregó el pronóstico (%s).', error.codigo)
            return _o_datos_anteriores(registro, cfg, error)
        _guardar(clave, datos, cfg)
        return Resultado(datos, False, False, 0, None)
    finally:
        if reclamada:
            _bd(almacen.liberar, marca)
        if tiene_candado:
            candado.release()
