"""Pantalla de inicio (panel del agricultor).
"""

from django.contrib.auth.decorators import login_required

from ..models import (
    ActividadAgricola,
    CalculoAgricola,
)
from .base import render_page
from .contexto_predios import EN_POTREROS_VIGENTES, farm_payload
from .reportes import describe_calculation


@login_required
def dashboard(request):
    farm_data = farm_payload(request.user)
    pending = (
        ActividadAgricola.objects.filter(
            EN_POTREROS_VIGENTES,
            usuario=request.user,
            potrero__predio__propietario=request.user,
            estado=ActividadAgricola.Estado.PENDIENTE,
        )
        .select_related('potrero', 'potrero__predio')
        .order_by('fecha_programada', 'pk')
    )
    return render_page(
        request,
        'dashboard.html',
        'dashboard',
        {
            'farm_data': farm_data,
            'dashboard_predios': farm_data['predios'],
            'dashboard_plots': farm_data['plots'],
            'dashboard_activities': pending[:5],
            'pending_activity_count': pending.count(),
            'dashboard_calculations': [
                describe_calculation(item)
                for item in CalculoAgricola.objects.filter(
                    EN_POTREROS_VIGENTES,
                    usuario=request.user,
                    potrero__predio__propietario=request.user,
                )
                .select_related('potrero__predio')
                .order_by('-creado', '-pk')[:4]
            ],
        },
    )
