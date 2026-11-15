"""Consejos de ayuda: plegar uno (queda guardado) o volver a mostrarlos todos (core/ayudas.py)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..ayudas import AYUDAS
from ..models import AyudaOculta


def _volver(request, defecto):
    destino = request.META.get('HTTP_REFERER', '')
    if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(destino)
    return redirect(defecto)


@login_required
@require_POST
def ayuda_ocultar(request, clave):
    if clave not in AYUDAS:
        raise Http404('Consejo no encontrado.')
    AyudaOculta.objects.get_or_create(usuario=request.user, clave=clave)
    if 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'ok': True})
    return _volver(request, 'dashboard')


@login_required
@require_POST
def ayudas_mostrar(request):
    AyudaOculta.objects.filter(usuario=request.user).delete()
    messages.success(request, 'Los consejos de ayuda se volverán a mostrar en cada sección.')
    return redirect('perfil')
