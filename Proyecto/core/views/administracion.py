"""Panel de administración propio de AgroAsesor (solo personal) y carga de los cultivos base.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from ..auth import administrador_requerido
from ..catalog import (
    CROP_GROUPS,
    ensure_base_crops,
)
from .base import render_page


@administrador_requerido
def admin_panel(request):
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


@administrador_requerido
@require_POST
def cultivos_base_cargar(request):
    """Acción explícita del administrador: carga los cultivos base que falten."""
    created = ensure_base_crops()
    if created:
        messages.success(request, f'Se cargaron {created} cultivos base. Ya aparecen al registrar potreros y cultivos.')
    else:
        messages.info(request, 'Los cultivos base ya estaban cargados; no se cambió nada.')
    return redirect('admin_panel')
