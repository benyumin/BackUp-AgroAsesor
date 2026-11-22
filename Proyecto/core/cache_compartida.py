"""Almacén de JSON firmado en la base de datos, compartido por todas las instancias del servidor.

En Vercel cada instancia serverless tiene su propia memoria: una caché local (``LocMemCache``) casi nunca se reutiliza.
La base de datos (Supabase) sí es común a todas, así que aquí se guardan los pronósticos del clima y las pausas por
límite de consultas.

Seguridad (las tablas de Supabase nacen abiertas a la API pública; ver la migración 0024, que además activa RLS):

* Solo se guarda JSON (nunca ``pickle``, que permitiría ejecutar código si alguien alterara una fila).
* Cada fila lleva una firma HMAC-SHA256 con una llave derivada de ``SECRET_KEY``. Una fila alterada, creada desde fuera
  de la aplicación o copiada con otra clave/fechas no valida y se ignora como si no existiera.
* La ubicación nunca se guarda: la clave de una zona es un código HMAC opaco (``clave_opaca``) y la carga es solo el
  pronóstico público. No hay usuarios, predios ni coordenadas.

Todas las funciones reciben el tiempo como segundos desde 1970 (enteros) para poder probarlas sin esperar.
"""

import hashlib
import hmac
import json
import logging
from collections import namedtuple
from datetime import datetime, timezone

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import CacheCompartida

logger = logging.getLogger(__name__)

# valor: JSON ya decodificado · obtenido y vence: segundos desde 1970.
Registro = namedtuple('Registro', 'valor obtenido vence')


def _llave(etiqueta):
    return hashlib.sha256(f'agroasesor|{etiqueta}|'.encode() + settings.SECRET_KEY.encode()).digest()


def clave_opaca(etiqueta, *partes):
    """Código estable y no reversible para una clave (p. ej. una zona del mapa); no revela la ubicación."""
    texto = '|'.join(str(parte) for parte in partes).encode()
    return f'{etiqueta}:{hmac.new(_llave("clave"), texto, hashlib.sha256).hexdigest()[:40]}'


def _firma(clave, carga, obtenido, vence):
    texto = f'{clave}\n{int(obtenido)}\n{int(vence)}\n{carga}'.encode()
    return hmac.new(_llave('firma'), texto, hashlib.sha256).hexdigest()


def _fecha(segundos):
    return datetime.fromtimestamp(int(segundos), tz=timezone.utc)


def leer(clave):
    """``Registro`` válido o ``None`` (no existe, firma inválida o JSON ilegible)."""
    fila = CacheCompartida.objects.filter(clave=clave).first()
    if fila is None:
        return None
    obtenido, vence = int(fila.obtenido_en.timestamp()), int(fila.vence_en.timestamp())
    if not hmac.compare_digest(_firma(clave, fila.carga, obtenido, vence), fila.firma):
        logger.warning('Caché compartida: se ignoró una fila con firma inválida (%s).', clave.split(':')[0])
        return None
    try:
        return Registro(json.loads(fila.carga), obtenido, vence)
    except ValueError:
        return None


def _campos(clave, valor, obtenido, vence):
    carga = json.dumps(valor, separators=(',', ':'), sort_keys=True)
    return {
        'carga': carga,
        'firma': _firma(clave, carga, obtenido, vence),
        'obtenido_en': _fecha(obtenido),
        'vence_en': _fecha(vence),
    }


def guardar(clave, valor, obtenido, vence):
    campos = _campos(clave, valor, obtenido, vence)
    if CacheCompartida.objects.filter(clave=clave).update(**campos):
        return
    try:
        with transaction.atomic():
            CacheCompartida.objects.create(clave=clave, **campos)
    except IntegrityError:  # otra instancia la creó a la vez: se actualiza
        CacheCompartida.objects.filter(clave=clave).update(**campos)


def reclamar(clave, ahora, segundos):
    """Marca «estoy consultando». ``True`` si la obtuve; ``False`` si otra marca sigue vigente (otra instancia)."""
    campos = _campos(clave, {}, ahora, ahora + segundos)
    try:
        with transaction.atomic():
            CacheCompartida.objects.create(clave=clave, **campos)
        return True
    except IntegrityError:
        pass
    # Existe: solo se toma si ya venció (quien la puso pudo caerse sin liberarla). La condición hace la toma atómica.
    return CacheCompartida.objects.filter(clave=clave, vence_en__lt=_fecha(ahora)).update(**campos) == 1


def liberar(clave):
    CacheCompartida.objects.filter(clave=clave).delete()


def purgar(antes_de):
    """Borra lo obtenido antes de ``antes_de``: la tabla no crece sin límite."""
    CacheCompartida.objects.filter(obtenido_en__lt=_fecha(antes_de)).delete()
