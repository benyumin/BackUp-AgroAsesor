"""Actividades agrícolas: listado, alta, detalle, edición, cambio de estado, borrado y planificación desde un
cálculo. Cada consulta filtra por el usuario conectado, de modo que nadie ve actividades ajenas.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import (
    UNIDADES_DOSIS,
    ActividadAgricolaForm,
)
from ..models import (
    ActividadAgricola,
    Potrero,
)
from ..seasons import (
    current_season,
)
from .base import render_page
from .contexto_predios import EN_POTREROS_VIGENTES, plot_season_summary


def _activities_for_user(user):
    return ActividadAgricola.objects.filter(
        EN_POTREROS_VIGENTES,
        usuario=user,
    ).select_related('potrero', 'potrero__predio', 'potrero__cultivo', 'temporada__cultivo', 'insumo__tipo')


# Recomendaciones generales de Buenas Prácticas Agrícolas por tipo de labor.
# Son pautas de manejo y registro, no dosis ni prescripciones.
ACTIVITY_GUIDANCE = {
    'preparacion': {
        'title': 'Preparación de suelo',
        'tips': [
            'Labra con humedad de suelo adecuada (ni seco ni barroso) para evitar terrones y compactación.',
            'Registra el implemento usado y la profundidad de trabajo.',
            'Si corresponde, toma muestras de suelo antes de preparar para definir la fertilización.',
        ],
    },
    'siembra': {
        'title': 'Siembra',
        'tips': [
            'Usa semilla certificada y guarda la etiqueta: variedad, lote, germinación y pureza.',
            'Compara la fecha con la ventana referencial del Calendario antes de sembrar.',
            'Calibra la sembradora para la densidad calculada en «Cuánto llevar».',
        ],
    },
    'fertilizacion': {
        'title': 'Fertilización',
        'tips': [
            'Define la dosis a partir de un análisis de suelo y la recomendación técnica.',
            'Fracciona el nitrógeno según la etapa del cultivo cuando la recomendación lo indique.',
            'Evita aplicar antes de lluvias intensas o sobre suelo saturado para reducir pérdidas.',
        ],
    },
    'riego': {
        'title': 'Riego',
        'tips': [
            'Decide el riego según la humedad del suelo y la etapa del cultivo.',
            'Registra tiempo de riego o volumen aplicado y revisa la uniformidad.',
            'Evita encharcamientos prolongados que favorecen enfermedades de raíz.',
        ],
    },
    'monitoreo': {
        'title': 'Monitoreo',
        'tips': [
            'Recorre el potrero en zigzag o por estaciones fijas para que la muestra sea representativa.',
            'Anota plagas, enfermedades y malezas con su intensidad y la etapa del cultivo.',
            'Define con tu asesor el umbral que justifica una intervención.',
        ],
    },
    'control_fitosanitario': {
        'title': 'Aplicación fitosanitaria',
        'tips': [
            'Usa solo productos autorizados por el SAG para ese cultivo y objetivo, con la dosis de la etiqueta.',
            'Usa el equipo de protección personal indicado en la etiqueta.',
            'No apliques con viento fuerte, lluvia inminente o temperaturas altas.',
            'Respeta el período de carencia y el tiempo de reingreso; señaliza el potrero.',
            'Haz triple lavado de los envases y entrégalos a un centro de acopio autorizado.',
            'Registra producto, dosis, superficie, fecha y aplicador en el cuaderno de campo.',
        ],
    },
    'cosecha': {
        'title': 'Cosecha',
        'tips': [
            'Verifica que haya terminado el período de carencia de la última aplicación.',
            'Confirma la madurez con indicadores observados en terreno, no solo con el calendario.',
            'Registra la producción obtenida para mejorar tus estimaciones de rendimiento.',
        ],
    },
    'otra': {
        'title': 'Otra labor',
        'tips': ['Describe la labor y su resultado en observaciones para mantener el historial completo.'],
    },
}


def _activity_form_context(request, form, heading, activity=None):
    plots = (
        Potrero.objects.filter(predio__propietario=request.user)
        .select_related(
            'predio',
            'cultivo',
        )
        .prefetch_related('temporadas__cultivo')
        .order_by('predio__nombre', 'nombre')
    )
    plot_data = []
    for plot in plots:
        summary = plot_season_summary(plot)
        plot_data.append(
            {
                'id': plot.pk,
                'name': plot.nombre,
                'farm': plot.predio.nombre,
                'areaHa': float(plot.superficie_ha),
                'crop': plot.cultivo.nombre if plot.cultivo_id else '',
                'cropId': plot.cultivo_id,
                'variety': plot.variedad,
                'sowing': plot.fecha_siembra.isoformat() if plot.fecha_siembra else '',
                'harvest': plot.fecha_cosecha.isoformat() if plot.fecha_cosecha else '',
                'currentSeasonId': summary['currentSeasonId'],
                'seasons': summary['seasons'],
            }
        )
    upcoming = (
        _activities_for_user(request.user)
        .filter(
            estado=ActividadAgricola.Estado.PENDIENTE,
        )
        .order_by('fecha_programada', 'pk')
    )
    if activity:
        upcoming = upcoming.exclude(pk=activity.pk)
    return {
        'form': form,
        'heading': heading,
        'activity': activity,
        'activity_plots': plot_data,
        'activity_guidance': ACTIVITY_GUIDANCE,
        'upcoming_activities': list(upcoming[:40]),
    }


def _activity_initial(request):
    initial = {}
    params = request.GET
    plot_id = params.get('potrero', '')
    if plot_id.isdigit() and Potrero.objects.filter(pk=plot_id, predio__propietario=request.user).exists():
        initial['potrero'] = int(plot_id)
        plot = Potrero.objects.get(pk=plot_id)
        season = current_season(plot)
        if season:
            initial['temporada'] = season.pk
    if params.get('tipo') in ActividadAgricola.Tipo.values:
        initial['tipo'] = params['tipo']
        initial['nombre'] = ACTIVITY_GUIDANCE[params['tipo']]['title']
    try:
        if params.get('fecha'):
            initial['fecha_programada'] = date.fromisoformat(params['fecha'])
    except ValueError:
        pass
    if params.get('insumo', '').isdigit():
        initial['insumo'] = int(params['insumo'])
    for key, field in (('dosis', 'dosis'), ('superficie', 'superficie_ha')):
        try:
            value = Decimal(params.get(key, ''))
            if value.is_finite() and value > 0:
                initial[field] = value
        except (InvalidOperation, ValueError):
            pass
    if params.get('unidad') in dict(UNIDADES_DOSIS):
        initial['unidad_dosis'] = params['unidad']
    if params.get('objetivo'):
        initial['objetivo'] = params['objetivo'][:150]
    return initial


@login_required
def activity_list(request):
    today = timezone.localdate()
    base = _activities_for_user(request.user)
    activities = base
    filters = {
        'estado': request.GET.get('estado', ''),
        'tipo': request.GET.get('tipo', ''),
        'potrero': request.GET.get('potrero', ''),
        'desde': request.GET.get('desde', ''),
        'hasta': request.GET.get('hasta', ''),
        'q': request.GET.get('buscar', '').strip(),
        'vista': 'calendario' if request.GET.get('vista') == 'calendario' else 'lista',
    }
    if filters['estado'] == 'vencida':
        activities = activities.filter(estado=ActividadAgricola.Estado.PENDIENTE, fecha_programada__lt=today)
    elif filters['estado'] in ActividadAgricola.Estado.values:
        activities = activities.filter(estado=filters['estado'])
    if filters['tipo'] in ActividadAgricola.Tipo.values:
        activities = activities.filter(tipo=filters['tipo'])
    if filters['potrero'].isdigit():
        activities = activities.filter(potrero_id=filters['potrero'])
    for key, lookup in (('desde', 'fecha_programada__gte'), ('hasta', 'fecha_programada__lte')):
        try:
            if filters[key]:
                activities = activities.filter(**{lookup: date.fromisoformat(filters[key])})
        except ValueError:
            filters[key] = ''
    if filters['q']:
        activities = activities.filter(
            Q(nombre__icontains=filters['q'])
            | Q(observaciones__icontains=filters['q'])
            | Q(insumo__nombre__icontains=filters['q'])
            | Q(insumo_otro__icontains=filters['q'])
        )
    activities = activities.order_by('fecha_programada', 'pk')

    try:
        month = date.fromisoformat((request.GET.get('mes') or today.strftime('%Y-%m')) + '-01')
    except ValueError:
        month = today.replace(day=1)
    import calendar as _calendar

    month_days = _calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    month_activities = {}
    for item in activities.filter(fecha_programada__gte=month_days[0][0], fecha_programada__lte=month_days[-1][-1]):
        month_activities.setdefault(item.fecha_programada, []).append(item)
    weeks = [
        [
            {
                'date': day,
                'in_month': day.month == month.month,
                'is_today': day == today,
                'items': month_activities.get(day, []),
            }
            for day in week
        ]
        for week in month_days
    ]
    previous_month = (month - timedelta(days=1)).replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    pending = base.filter(estado=ActividadAgricola.Estado.PENDIENTE)
    stats = {
        'pendientes': pending.count(),
        'vencidas': pending.filter(fecha_programada__lt=today).count(),
        'proximas': pending.filter(
            fecha_programada__gte=today, fecha_programada__lte=today + timedelta(days=7)
        ).count(),
        'completadas_mes': base.filter(
            estado=ActividadAgricola.Estado.COMPLETADA,
            fecha_realizada__year=today.year,
            fecha_realizada__month=today.month,
        ).count(),
    }
    plots = (
        Potrero.objects.filter(predio__propietario=request.user)
        .select_related('predio', 'cultivo')
        .prefetch_related(
            'temporadas__cultivo',
        )
        .order_by('predio__nombre', 'nombre')
    )
    plan_plots = []
    for plot in plots:
        season = current_season(plot)
        plan_plots.append(
            {
                'plot': plot,
                'season': season,
                'sowing': season.fecha_siembra if season else plot.fecha_siembra,
                'harvest': season.fecha_cosecha if season else plot.fecha_cosecha,
            }
        )
    return render_page(
        request,
        'activity_list.html',
        'actividades',
        {
            'activities': activities,
            'activity_filters': filters,
            'activity_types': ActividadAgricola.Tipo.choices,
            'activity_states': ActividadAgricola.Estado.choices,
            'activity_plots': plots,
            'activity_stats': stats,
            'calendar_weeks': weeks,
            'calendar_month': month,
            'previous_month': previous_month.strftime('%Y-%m'),
            'next_month': next_month.strftime('%Y-%m'),
            'today': today,
            'plan_plots': plan_plots,
        },
    )


@login_required
def activity_create(request):
    form = ActividadAgricolaForm(
        request.POST or None,
        user=request.user,
        instance=ActividadAgricola(usuario=request.user),
        initial=_activity_initial(request) if request.method == 'GET' else None,
    )
    if request.method == 'POST' and form.is_valid():
        activity = form.save()
        messages.success(request, 'La actividad quedó guardada y aparecerá en Hoy, Siembra y el cuaderno de campo.')
        if request.POST.get('siguiente') == 'otra':
            return redirect(f"{reverse('actividad_create')}?potrero={activity.potrero_id}")
        return redirect('actividad_detail', pk=activity.pk)
    return render_page(
        request,
        'activity_form.html',
        'actividades',
        _activity_form_context(
            request,
            form,
            'Programar labor agrícola',
        ),
    )


@login_required
def activity_detail(request, pk):
    activity = get_object_or_404(_activities_for_user(request.user), pk=pk)
    guidance = ACTIVITY_GUIDANCE.get(activity.tipo)
    related = (
        _activities_for_user(request.user)
        .filter(potrero=activity.potrero)
        .exclude(pk=activity.pk)
        .order_by('-fecha_programada')[:6]
    )
    return render_page(
        request,
        'activity_detail.html',
        'actividades',
        {
            'activity': activity,
            'guidance': guidance,
            'related_activities': related,
        },
    )


@login_required
def activity_edit(request, pk):
    activity = get_object_or_404(_activities_for_user(request.user), pk=pk)
    form = ActividadAgricolaForm(request.POST or None, user=request.user, instance=activity)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'La actividad fue actualizada.')
        return redirect('actividad_detail', pk=activity.pk)
    return render_page(
        request,
        'activity_form.html',
        'actividades',
        _activity_form_context(
            request,
            form,
            'Editar actividad agrícola',
            activity,
        ),
    )


@login_required
@require_POST
def activity_change_status(request, pk):
    activity = get_object_or_404(_activities_for_user(request.user), pk=pk)
    new_status = request.POST.get('estado', '')
    if new_status not in ActividadAgricola.Estado.values:
        messages.error(request, 'Selecciona un estado válido para la actividad.')
        return redirect('actividad_detail', pk=activity.pk)
    activity.estado = new_status
    fields = ['estado', 'actualizado']
    if new_status == ActividadAgricola.Estado.COMPLETADA and not activity.fecha_realizada:
        activity.fecha_realizada = timezone.localdate()
        fields.append('fecha_realizada')
    if new_status == ActividadAgricola.Estado.PENDIENTE and activity.fecha_realizada:
        activity.fecha_realizada = None
        fields.append('fecha_realizada')
    activity.save(update_fields=fields)
    messages.success(request, 'El estado de la actividad fue actualizado.')
    destination = request.POST.get('next')
    if destination == 'dashboard':
        return redirect('dashboard')
    if destination == 'calendario':
        return redirect('calendario')
    if destination == 'actividades':
        return redirect('actividad_list')
    return redirect('actividad_detail', pk=activity.pk)


@login_required
def activity_delete(request, pk):
    activity = get_object_or_404(_activities_for_user(request.user), pk=pk)
    if request.method == 'POST':
        activity.delete()
        messages.success(request, 'La actividad fue eliminada.')
        return redirect('actividad_list')
    return render_page(
        request,
        'activity_confirm_delete.html',
        'actividades',
        {
            'activity': activity,
        },
    )


@login_required
@require_POST
@transaction.atomic
def activity_plan(request):
    """Crea actividades a partir de las fechas que el agricultor registró en su temporada.

    No agrega labores ni frecuencias por defecto: solo las que el usuario marca,
    con los intervalos que él mismo indica.
    """
    plot_id = request.POST.get('potrero', '')
    plot = get_object_or_404(Potrero, pk=plot_id if plot_id.isdigit() else 0, predio__propietario=request.user)
    season = current_season(plot)
    sowing = season.fecha_siembra if season else plot.fecha_siembra
    harvest = season.fecha_cosecha if season else plot.fecha_cosecha
    crop = season.cultivo.nombre if season else (plot.cultivo.nombre if plot.cultivo_id else '')
    planned = []

    def add(tipo, nombre, day, observaciones=''):
        exists = ActividadAgricola.objects.filter(
            usuario=request.user, potrero=plot, tipo=tipo, fecha_programada=day
        ).exists()
        if not exists:
            planned.append(
                ActividadAgricola(
                    usuario=request.user,
                    potrero=plot,
                    temporada=season,
                    tipo=tipo,
                    nombre=nombre,
                    fecha_programada=day,
                    observaciones=observaciones,
                    prioridad=(
                        ActividadAgricola.Prioridad.ALTA
                        if tipo in {'siembra', 'cosecha'}
                        else ActividadAgricola.Prioridad.MEDIA
                    ),
                )
            )

    def positive_int(name, maximum):
        try:
            value = int(request.POST.get(name, ''))
        except ValueError:
            return None
        return value if 1 <= value <= maximum else None

    label = f' de {crop}' if crop else ''
    if request.POST.get('incluir_preparacion') and sowing:
        days = positive_int('dias_preparacion', 120)
        if days is None:
            messages.error(request, 'Indica cuántos días antes de la siembra se prepara el suelo (1 a 120).')
            return redirect('actividad_list')
        add('preparacion', f'Preparación de suelo{label}', sowing - timedelta(days=days))
    if request.POST.get('incluir_siembra') and sowing:
        add('siembra', f'Siembra{label}', sowing)
    if request.POST.get('incluir_monitoreo') and sowing and harvest:
        every = positive_int('frecuencia_monitoreo', 60)
        if every is None:
            messages.error(request, 'Indica cada cuántos días quieres monitorear (1 a 60).')
            return redirect('actividad_list')
        day = sowing + timedelta(days=every)
        count = 0
        while day < harvest and count < 40:
            add('monitoreo', f'Monitoreo{label}', day, 'Registrar plagas, enfermedades, malezas y estado del cultivo.')
            day += timedelta(days=every)
            count += 1
    if request.POST.get('incluir_cosecha') and harvest:
        add('cosecha', f'Cosecha{label}', harvest, 'Confirmar madurez y fin de carencia antes de cosechar.')
    if not sowing and not harvest:
        messages.error(
            request,
            'El potrero no tiene fechas de siembra ni de cosecha registradas. Regístralas en Predios o en el Calendario.',
        )
        return redirect('actividad_list')
    for activity in planned:
        activity.full_clean()
        activity.save()
    messages.success(
        request,
        (
            f'Se programaron {len(planned)} actividad(es) para «{plot.nombre}».'
            if planned
            else 'No se agregaron actividades: ya existían en esas fechas o no marcaste ninguna.'
        ),
    )
    return redirect(f"{reverse('actividad_list')}?potrero={plot.pk}")
