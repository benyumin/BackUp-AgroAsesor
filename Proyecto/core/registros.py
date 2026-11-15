"""Eliminar, archivar y restaurar predios y potreros sin perder información relacionada.

Reglas (las mismas para la página de confirmación, la ventana emergente y el servidor):

* **Eliminar** borra el registro de forma definitiva y solo se permite si no tiene información asociada:
  - un potrero, sin actividades, cálculos guardados ni temporadas;
  - un predio, sin potreros (ni siquiera archivados).
  El historial del predio nunca se borra: se conserva con su resumen y se agrega una línea «eliminado».
* **Archivar** está siempre disponible: el registro deja de aparecer en listas, mapas, calculadoras, agenda y
  reportes, pero todo lo asociado se conserva y vuelve al **restaurar**.
* Solo el dueño del predio puede hacer estas acciones (las vistas buscan el registro filtrando por dueño).
"""

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Potrero, Predio, PredioHistorial


class NoSePuedeEliminar(ValidationError):
    """El registro tiene información asociada; se ofrece archivarlo."""


@dataclass
class Revision:
    """Qué se puede hacer con un registro y por qué."""

    tipo: str  # 'predio' | 'potrero'
    nombre: str
    detalle: str  # «Fundo Yarek · 9 ha»
    bloqueos: list = field(default_factory=list)  # «3 actividades», «1 cálculo guardado»…
    archivado: bool = False

    @property
    def puede_eliminar(self):
        return not self.bloqueos

    def como_dict(self):
        return {
            'tipo': self.tipo,
            'nombre': self.nombre,
            'detalle': self.detalle,
            'bloqueos': self.bloqueos,
            'puede_eliminar': self.puede_eliminar,
            'archivado': self.archivado,
        }


def _cuenta(n, singular, plural):
    return f'{n} {singular if n == 1 else plural}'


def _ha(valor):
    texto = f'{valor.normalize():f}'.replace('.', ',') if valor is not None else '—'
    return f'{texto} ha'


def revisar(registro):
    """Lista lo que impide eliminar el registro de forma definitiva."""
    if isinstance(registro, Predio):
        bloqueos = []
        potreros = Potrero.todos.filter(predio=registro)
        vigentes = potreros.filter(archivado_en__isnull=True).count()
        archivados = potreros.count() - vigentes
        if vigentes:
            bloqueos.append(_cuenta(vigentes, 'potrero', 'potreros'))
        if archivados:
            bloqueos.append(_cuenta(archivados, 'potrero archivado', 'potreros archivados'))
        return Revision('predio', registro.nombre, f'{registro.nombre} · {_ha(registro.superficie_ha)}', bloqueos,
                        archivado=registro.archivado_en is not None)
    bloqueos = []
    for cantidad, singular, plural in (
        (registro.actividades.count(), 'actividad registrada', 'actividades registradas'),
        (registro.calculos.count(), 'cálculo guardado', 'cálculos guardados'),
        (registro.temporadas.count(), 'temporada de cultivo', 'temporadas de cultivo'),
    ):
        if cantidad:
            bloqueos.append(_cuenta(cantidad, singular, plural))
    predio = Predio.todos.get(pk=registro.predio_id)
    return Revision('potrero', registro.nombre, f'{registro.nombre} · {_ha(registro.superficie_ha)} · {predio.nombre}',
                    bloqueos, archivado=registro.archivado_en is not None)


def _historial(predio, potrero, accion, resumen, usuario):
    PredioHistorial.objects.create(predio=predio, potrero=potrero, accion=accion, resumen=resumen, usuario=usuario)


@transaction.atomic
def eliminar(registro, usuario):
    revision = revisar(registro)
    if not revision.puede_eliminar:
        raise NoSePuedeEliminar(
            f'No se puede eliminar «{revision.nombre}» porque tiene {", ".join(revision.bloqueos)}. '
            'Puedes archivarlo para ocultarlo sin perder esa información.'
        )
    if isinstance(registro, Predio):
        # El historial del predio queda guardado sin enlace (on_delete=SET_NULL) y con esta última línea.
        _historial(None, None, PredioHistorial.Accion.ELIMINADO, f'Predio eliminado: {revision.detalle}.', usuario)
    else:
        predio = Predio.todos.get(pk=registro.predio_id)
        _historial(predio, None, PredioHistorial.Accion.POTRERO_ELIMINADO,
                   f'Potrero eliminado: {revision.detalle}.', usuario)
    registro.delete()
    return revision


@transaction.atomic
def archivar(registro, usuario):
    revision = revisar(registro)
    if registro.archivado_en:
        return revision
    registro.archivado_en = timezone.now()
    registro.save(update_fields=['archivado_en', 'actualizado'])
    if isinstance(registro, Predio):
        _historial(registro, None, PredioHistorial.Accion.ARCHIVADO, f'Predio archivado: {revision.detalle}.', usuario)
    else:
        _historial(Predio.todos.get(pk=registro.predio_id), registro, PredioHistorial.Accion.POTRERO_ARCHIVADO,
                   f'Potrero archivado: {revision.detalle}.', usuario)
    return revision


@transaction.atomic
def restaurar(registro, usuario):
    """Vuelve a mostrar el registro. Un potrero se valida otra vez (superficie y superposición con los vigentes)."""
    revision = revisar(registro)
    if isinstance(registro, Potrero):
        predio = Predio.todos.get(pk=registro.predio_id)
        if predio.archivado_en:
            raise ValidationError(f'Primero restaura el predio «{predio.nombre}».')
    if not registro.archivado_en:
        return revision
    if isinstance(registro, Potrero):
        registro.full_clean()  # puede fallar si otro potrero ocupó su superficie mientras estaba archivado
    registro.archivado_en = None
    registro.save(update_fields=['archivado_en', 'actualizado'])
    if isinstance(registro, Predio):
        _historial(registro, None, PredioHistorial.Accion.RESTAURADO, f'Predio restaurado: {revision.detalle}.', usuario)
    else:
        _historial(Predio.todos.get(pk=registro.predio_id), registro, PredioHistorial.Accion.POTRERO_RESTAURADO,
                   f'Potrero restaurado: {revision.detalle}.', usuario)
    return revision


def buscar(usuario, tipo, pk):
    """El registro del usuario (incluye archivados). Lanza Predio/Potrero.DoesNotExist si no es suyo."""
    if tipo == 'predio':
        return Predio.todos.get(pk=pk, propietario=usuario)
    if tipo == 'potrero':
        return Potrero.todos.select_related('predio').get(pk=pk, predio__propietario=usuario)
    raise Predio.DoesNotExist


def archivados(usuario):
    """Predios archivados y potreros archivados de predios vigentes, para la página «Archivados»."""
    return {
        'predios': list(Predio.todos.filter(propietario=usuario, archivado_en__isnull=False).order_by('-archivado_en')),
        'potreros': list(
            Potrero.todos.filter(predio__propietario=usuario, archivado_en__isnull=False,
                                 predio__archivado_en__isnull=True)
            .select_related('predio', 'cultivo')
            .order_by('-archivado_en')
        ),
    }
