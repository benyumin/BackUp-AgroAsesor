"""Eliminar, archivar y restaurar predios y potreros (las reglas están en ``core.registros``).

* ``registro_confirmar`` (GET): página de confirmación; con ``?formato=json`` entrega lo mismo para la ventana
  emergente del menú «⋯» (registro-acciones.js).
* ``registro_accion`` (POST): ejecuta ``eliminar``, ``archivar`` o ``restaurar``. Solo el dueño puede hacerlo.
* ``archivados``: lista lo archivado para restaurarlo.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from .. import registros
from ..models import Potrero, Predio
from .base import render_page

ACCIONES = {
    'eliminar': (registros.eliminar, '«{nombre}» se eliminó.'),
    'archivar': (registros.archivar, '«{nombre}» se archivó. Puedes restaurarlo desde Mis predios › Archivados.'),
    'restaurar': (registros.restaurar, '«{nombre}» volvió a estar disponible.'),
}


def _registro(request, tipo, pk):
    try:
        return registros.buscar(request.user, tipo, pk)
    except (Predio.DoesNotExist, Potrero.DoesNotExist):
        raise Http404('Registro no encontrado.')


def _quiere_json(request):
    return request.GET.get('formato') == 'json' or 'application/json' in request.headers.get('Accept', '')


def _destino(registro, accion):
    """A dónde volver después de la acción."""
    if accion == 'restaurar':
        return reverse('predio_detail', args=[registro.predio_id if isinstance(registro, Potrero) else registro.pk])
    if isinstance(registro, Potrero):
        return reverse('predio_detail', args=[registro.predio_id])
    return reverse('predios')


@login_required
def registro_confirmar(request, tipo, pk):
    registro = _registro(request, tipo, pk)
    revision = registros.revisar(registro)
    accion_url = reverse('registro_accion', args=[tipo, pk])
    if _quiere_json(request):
        return JsonResponse({**revision.como_dict(), 'accion_url': accion_url})
    if revision.archivado:
        volver = reverse('archivados')
    elif isinstance(registro, Potrero):
        volver = reverse('predio_detail', args=[registro.predio_id])
    else:
        volver = reverse('predio_detail', args=[registro.pk])
    return render_page(request, 'registro_eliminar.html', 'predios',
                       {'revision': revision, 'accion_url': accion_url, 'volver_url': volver})


@login_required
@require_POST
def registro_accion(request, tipo, pk):
    registro = _registro(request, tipo, pk)
    accion = request.POST.get('accion', '')
    if accion not in ACCIONES:
        return JsonResponse({'error': 'Acción no válida.'}, status=400)
    funcion, mensaje = ACCIONES[accion]
    destino = _destino(registro, accion)
    try:
        revision = funcion(registro, request.user)
    except ValidationError as exc:
        texto = ' '.join(exc.messages)
        if _quiere_json(request):
            return JsonResponse({'error': texto}, status=409)
        messages.error(request, texto)
        return redirect(reverse('registro_confirmar', args=[tipo, pk]))
    messages.success(request, mensaje.format(nombre=revision.nombre))
    if _quiere_json(request):
        return JsonResponse({'ok': True, 'redirect': destino})
    return redirect(destino)


@login_required
def archivados(request):
    return render_page(request, 'archivados.html', 'predios', {'archivados': registros.archivados(request.user)})
