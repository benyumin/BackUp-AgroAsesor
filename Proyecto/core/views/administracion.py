"""Panel de administración propio de AgroAsesor (solo personal) y carga de los cultivos base.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from ..catalog import (
    CROP_GROUPS,
    ensure_base_crops,
)
from .base import render_page


@login_required
def admin_panel(request):
    if not request.user.is_staff:
        raise PermissionDenied
    from ..estadisticas import resumen_administrador

    return render_page(
        request,
        'admin.html',
        'admin_panel',
        {
            'stats': resumen_administrador(),
            'base_crops_total': sum(len(names) for _group, names in CROP_GROUPS),
        },
    )


@login_required
@require_POST
def cultivos_base_cargar(request):
    """Acción explícita del administrador: carga los cultivos base que falten."""
    if not request.user.is_staff:
        raise PermissionDenied
    created = ensure_base_crops()
    if created:
        messages.success(request, f'Se cargaron {created} cultivos base. Ya aparecen al registrar potreros y cultivos.')
    else:
        messages.info(request, 'Los cultivos base ya estaban cargados; no se cambió nada.')
    return redirect('admin_panel')
