"""Calendario de siembra: línea de tiempo del año, próximos pasos por potrero y evaluación de fechas de siembra
y cosecha contra las referencias agronómicas (``core.agronomy``).
"""

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from ..agronomy import evaluate_harvest_date, evaluate_sowing_date, find_reference
from ..catalog import (
    default_zone,
)
from ..models import (
    ActividadAgricola,
    ConfiguracionAgronomica,
    Cultivo,
    Potrero,
    Predio,
    ReferenciaAgronomica,
    Zona,
)
from ..seasons import (
    planning_state,
)
from ..pdf import respuesta_pdf
from ..pdf.informes import informe_calendario
from .base import render_page
from .contexto_predios import ACTIVITY_ICONS, EN_POTREROS_VIGENTES, farm_cards, plan_form_url


def calendar_reference_payload(reference):
    return {
        'id': reference.pk,
        'cropId': reference.cultivo_id,
        'crop': reference.cultivo.nombre,
        'zoneId': reference.zona_id,
        'zone': reference.zona.nombre,
        'referenceYear': reference.anio_referencia,
        'variety': reference.variedad,
        'scientificName': reference.nombre_cientifico,
        'botanicalFamily': reference.familia_botanica,
        'cropType': reference.tipo_cultivo,
        'sowingConditions': reference.condicion_siembra,
        'requirements': reference.requerimientos,
        'sowingDepth': reference.profundidad_siembra,
        'phenologicalStages': reference.etapas_fenologicas,
        'growthConditions': reference.condiciones_crecimiento,
        'management': reference.manejo_general,
        'maturityIndicators': reference.indicadores_madurez,
        'harvestConsiderations': reference.consideraciones_cosecha,
        'pests': reference.plagas,
        'diseases': reference.enfermedades,
        'weeds': reference.malezas,
        'integratedManagement': reference.manejo_integrado,
        'sowingStartMonth': reference.siembra_inicio_mes,
        'sowingStartDay': reference.siembra_inicio_dia,
        'sowingEndMonth': reference.siembra_fin_mes,
        'sowingEndDay': reference.siembra_fin_dia,
        'harvestStartMonth': reference.cosecha_inicio_mes,
        'harvestStartDay': reference.cosecha_inicio_dia,
        'harvestEndMonth': reference.cosecha_fin_mes,
        'harvestEndDay': reference.cosecha_fin_dia,
        'cycleDays': reference.duracion_ciclo_dias,
        'yieldTonHa': (float(reference.rendimiento_ton_ha) if reference.rendimiento_ton_ha is not None else None),
        'status': reference.estado,
        'sourceInstitution': reference.institucion_fuente,
        'sourceDocument': reference.documento_fuente,
        'sourceUrl': reference.url_fuente,
        'sourceDate': (reference.fecha_consulta_fuente.isoformat() if reference.fecha_consulta_fuente else ''),
        'sourceVerified': reference.tiene_fuente_verificada,
    }


def _days_text(days):
    return f'{days} día' if days == 1 else f'{days} días'


def _sowing_next_steps(user, farms, today):
    """Próximas labores del agricultor y qué le falta completar en sus potreros."""
    pending = list(
        ActividadAgricola.objects.filter(
            EN_POTREROS_VIGENTES,
            usuario=user,
            estado=ActividadAgricola.Estado.PENDIENTE,
            potrero__predio__propietario=user,
        )
        .select_related('potrero', 'potrero__predio')
        .order_by('fecha_programada', 'pk')
    )
    upcoming = []
    for activity in pending[:6]:
        days = (activity.fecha_programada - today).days
        if days < 0:
            label, tone = f'Atrasada {_days_text(-days)}', 'late'
        elif days == 0:
            label, tone = 'Hoy', 'today'
        elif days == 1:
            label, tone = 'Mañana', 'soon'
        else:
            label, tone = f'En {_days_text(days)}', 'soon' if days <= 7 else 'later'
        upcoming.append(
            {
                'activity': activity,
                'label': label,
                'tone': tone,
                'icon': ACTIVITY_ICONS.get(activity.tipo, 'clipboard-list'),
            }
        )
    overdue = sum(1 for activity in pending if activity.fecha_programada < today)
    sowing_planned = {activity.potrero_id for activity in pending if activity.tipo == ActividadAgricola.Tipo.SIEMBRA}

    tasks = []
    for farm in farms:
        plots = list(farm.potreros.all())
        if not plots:
            tasks.append(
                {
                    'icon': 'layout-grid',
                    'tone': 'info',
                    'text': f'Agrega el primer potrero de «{farm.nombre}»',
                    'hint': 'Sin potreros no se puede planificar la siembra.',
                    'url': reverse('potrero_create', kwargs={'predio_pk': farm.pk}),
                    'cta': 'Agregar potrero',
                }
            )
            continue
        for plot in plots:
            state = planning_state(plot)
            edit_url = reverse('potrero_edit', kwargs={'predio_pk': farm.pk, 'pk': plot.pk})
            plan_url = plan_form_url(plot, state)  # las fechas se completan en la temporada vigente
            sowing_date = state['sowing_date']
            if not state['has_crop']:
                tasks.append(
                    {
                        'icon': 'sprout',
                        'tone': 'warn',
                        'text': f'Asigna un cultivo a «{plot.nombre}»',
                        'hint': f'{farm.nombre} · {number_format(plot.superficie_ha, decimal_pos=2)} ha',
                        'url': edit_url,
                        'cta': 'Asignar cultivo',
                    }
                )
            elif not state['steps'][1]:
                tasks.append(
                    {
                        'icon': 'calendar-plus',
                        'tone': 'warn',
                        'text': f'Registra la fecha de siembra de «{plot.nombre}»',
                        'hint': f'{plot.cultivo.nombre} · {farm.nombre}',
                        'url': plan_url,
                        'cta': 'Registrar fecha',
                    }
                )
            elif not state['steps'][2]:
                tasks.append(
                    {
                        'icon': 'wheat',
                        'tone': 'info',
                        'text': f'Agrega la cosecha prevista de «{plot.nombre}»',
                        'hint': f'{plot.cultivo.nombre} · siembra {sowing_date:%d/%m/%Y}',
                        'url': plan_url,
                        'cta': 'Agregar fecha',
                    }
                )
            if sowing_date and sowing_date >= today and plot.pk not in sowing_planned:
                query = urlencode({'potrero': plot.pk, 'tipo': 'siembra', 'fecha': sowing_date.isoformat()})
                tasks.append(
                    {
                        'icon': 'clipboard-list',
                        'tone': 'action',
                        'text': f'Programa la labor de siembra de «{plot.nombre}»',
                        'hint': f'Siembra prevista el {sowing_date:%d/%m/%Y}',
                        'url': reverse('actividad_create') + '?' + query,
                        'cta': 'Programar labor',
                    }
                )
    return {
        'upcoming': upcoming,
        'pending_total': len(pending),
        'overdue': overdue,
        'tasks': tasks[:8],
        'tasks_total': len(tasks),
    }


def _sowing_timeline(farms, year, today):
    """Barras siembra -> cosecha de los potreros del usuario para un año (posiciones en %)."""
    import calendar as month_calendar

    def position(day):
        day = min(max(day, date(year, 1, 1)), date(year, 12, 31))
        days_in_month = month_calendar.monthrange(year, day.month)[1]
        return (day.month - 1 + (day.day - 1) / days_in_month) / 12 * 100

    rows, undated, outside = [], 0, 0
    for farm in farms:
        for plot in farm.potreros.all():
            sow, harvest = plot.fecha_siembra, plot.fecha_cosecha
            if not (sow or harvest):
                undated += 1
                continue
            start, end = sow or harvest, harvest or sow
            if end < date(year, 1, 1) or start > date(year, 12, 31):
                outside += 1
                continue
            left = position(start)
            right = position(end) + (100 / 12 / 30 if end.year == year else 0)
            width = max(right - left, 1.6)
            if sow and harvest:
                kind = 'range'
            elif sow:
                kind = 'sow'
            else:
                kind = 'harvest'
            rows.append(
                {
                    'farm': farm,
                    'plot': plot,
                    'kind': kind,
                    'start': start,
                    'end': end,
                    'left': f'{left:.2f}',
                    'width': f'{min(width, 100 - left):.2f}',
                    'cut_left': start.year < year,
                    'cut_right': end.year > year,
                    'crop': plot.cultivo.nombre if plot.cultivo_id else '',
                }
            )
    rows.sort(key=lambda row: (row['start'], row['farm'].nombre, row['plot'].nombre))
    return {
        'rows': rows,
        'undated': undated,
        'outside': outside,
        'year': year,
        'today': f'{position(today):.2f}' if today.year == year else '',
        'months': ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC'],
    }


def referencias_visibles():
    """Referencias que muestra el calendario: cultivo y zona activos, y no retiradas."""
    return (
        ReferenciaAgronomica.objects.filter(cultivo__activo=True, zona__activo=True)
        .exclude(estado=ReferenciaAgronomica.Estado.RETIRADA)
        .select_related('cultivo', 'zona')
    )


@login_required
def calendario_pdf(request, pk):
    """Informe PDF de una referencia del calendario (la misma que se ve en pantalla)."""
    referencia = get_object_or_404(referencias_visibles(), pk=pk)
    anio = request.GET.get('anio', '')
    anio = int(anio) if anio.isdigit() and 2000 <= int(anio) <= 2100 else timezone.localdate().year
    contenido = informe_calendario(referencia, anio, autor=request.user.get_full_name())
    return respuesta_pdf(contenido, f'agroasesor-calendario-{slugify(referencia.cultivo.nombre)}-{anio}.pdf')


@login_required
def calendario(request):
    zones = list(Zona.objects.filter(activo=True).order_by('nombre'))
    current_year = timezone.localdate().year
    try:
        selected_year = int(request.GET.get('year', current_year))
    except (TypeError, ValueError):
        selected_year = current_year
    if not 1900 <= selected_year <= 2100:
        selected_year = current_year

    selected_zone = request.GET.get('zone', '')
    if selected_zone and not any(str(zone.pk) == selected_zone for zone in zones):
        selected_zone = ''
    selected_crop = request.GET.get('crop', '')
    if selected_crop and not (
        selected_crop.isdigit() and Cultivo.objects.filter(pk=selected_crop, activo=True).exists()
    ):
        selected_crop = ''
    selected_variety = request.GET.get('variety', '*').strip()[:150]

    # Se envían todas las referencias vigentes (todas las zonas, cultivos y años): el año,
    # la zona y el cultivo se filtran en el navegador sin recargar la página.
    references = list(
        referencias_visibles().order_by(
            'cultivo__nombre',
            'zona__nombre',
            'variedad',
            'pk',
        )
    )
    if not selected_zone and zones:
        # Sin zona en la URL se abre la del primer predio del usuario (si la tiene) y, si no,
        # la zona con más ventanas registradas (a igualdad, la primera por nombre).
        own_zone = (
            Predio.objects.filter(
                propietario=request.user,
                zona__in=zones,
            )
            .values_list('zona_id', flat=True)
            .first()
        )
        preferred_zone = default_zone()
        if own_zone:
            selected_zone = str(own_zone)
        elif preferred_zone and preferred_zone in zones:
            selected_zone = str(preferred_zone.pk)  # Melipilla, la zona de trabajo por defecto
        else:
            windows = Counter(reference.zona_id for reference in references)
            selected_zone = str(max(zones, key=lambda zone: windows[zone.pk]).pk)
    # Registro de siembra: todos los predios del usuario con sus potreros, siempre leídos de la
    # base de datos, para que cada predio/potrero recién creado aparezca aquí sin pasos extra.
    sowing_farms = list(
        Predio.objects.filter(propietario=request.user)
        .select_related('zona')
        .prefetch_related(
            Prefetch(
                'potreros',
                queryset=Potrero.objects.select_related('cultivo')
                .prefetch_related('temporadas__cultivo')
                .order_by('nombre', 'pk'),
            )
        )
        .order_by('nombre', 'pk')
    )
    try:
        timeline_year = int(request.GET.get('anio', current_year))
    except (TypeError, ValueError):
        timeline_year = current_year
    if not 2000 <= timeline_year <= 2100:
        timeline_year = current_year
    timeline = _sowing_timeline(sowing_farms, timeline_year, timezone.localdate())
    next_steps = _sowing_next_steps(request.user, sowing_farms, timezone.localdate())
    years = {current_year, current_year + 1, selected_year}
    years.update(reference.anio_referencia for reference in references if reference.anio_referencia)
    return render_page(
        request,
        'calendario.html',
        'calendario',
        {
            'calendar_references': [calendar_reference_payload(reference) for reference in references],
            'calendar_crops': Cultivo.objects.filter(activo=True).order_by('nombre'),
            'calendar_zones': zones,
            'calendar_years': sorted(years),
            'calendar_current_year': selected_year,
            'calendar_default_zone': int(selected_zone) if selected_zone else '',
            'calendar_selected_crop': selected_crop,
            'calendar_selected_variety': selected_variety or '*',
            'sowing_farms': sowing_farms,
            'farm_cards': farm_cards(request.user, sowing_farms),
            'timeline': timeline,
            'next_steps': next_steps,
            'timeline_prev': timeline_year - 1,
            'timeline_next': timeline_year + 1,
            'sowing_plot_total': sum(len(farm.potreros.all()) for farm in sowing_farms),
            'sowing_area_total': sum((farm.superficie_ha for farm in sowing_farms), Decimal('0')),
            'sowing_planted_total': sum(
                1 for farm in sowing_farms for plot in farm.potreros.all() if planning_state(plot)['steps'][1]
            ),
            'calendar_has_unverified': any(not reference.tiene_fuente_verificada for reference in references),
        },
    )


@login_required
@require_POST
def evaluate_calendar_date(request):
    if request.content_type != 'application/json':
        return JsonResponse({'error': 'Envía los datos como JSON.'}, status=415)
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'El cuerpo JSON no se pudo interpretar.'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Los datos deben ser un objeto JSON.'}, status=400)

    evaluation = payload.get('evaluation')
    if evaluation not in {'sowing', 'harvest'}:
        return JsonResponse({'error': 'Selecciona una evaluación válida.'}, status=400)
    crop_id = payload.get('crop_id')
    zone_id = payload.get('zone_id')
    if not str(crop_id or '').isdigit() or not str(zone_id or '').isdigit():
        return JsonResponse({'error': 'Selecciona un cultivo y una zona.'}, status=400)
    if not Cultivo.objects.filter(pk=crop_id, activo=True).exists():
        return JsonResponse({'error': 'El cultivo seleccionado no está disponible.'}, status=400)
    if not Zona.objects.filter(pk=zone_id, activo=True).exists():
        return JsonResponse({'error': 'La zona seleccionada no está disponible.'}, status=400)
    try:
        proposal = date.fromisoformat(str(payload.get('proposed_date', '')))
        sowing_date = date.fromisoformat(str(payload.get('sowing_date', ''))) if evaluation == 'harvest' else None
    except ValueError:
        return JsonResponse({'error': 'Ingresa fechas válidas.'}, status=400)
    if evaluation == 'harvest' and proposal < sowing_date:
        return JsonResponse(
            {'error': 'La fecha de cosecha no puede ser anterior a la fecha de siembra.'},
            status=400,
        )
    variety = str(payload.get('variety') or '').strip()
    if len(variety) > 150:
        return JsonResponse({'error': 'La variedad supera la longitud permitida.'}, status=400)

    reference = find_reference(
        crop_id,
        zone_id,
        variety,
        year=(sowing_date or proposal).year,
    )
    configuration = ConfiguracionAgronomica.objects.get_or_create(pk=1)[0]
    if evaluation == 'sowing':
        result = evaluate_sowing_date(
            proposal,
            reference,
            configuration.margen_ventana_dias,
        )
    else:
        result = evaluate_harvest_date(
            sowing_date,
            proposal,
            reference,
            configuration.margen_ventana_dias,
        )
    result.update(
        {
            'proposed_date': proposal.isoformat(),
            'sowing_date': sowing_date.isoformat() if sowing_date else '',
            'reference_context': (
                {
                    'crop': reference.cultivo.nombre,
                    'zone': reference.zona.nombre,
                    'variety': reference.variedad or 'Referencia general del cultivo',
                    'year': reference.anio_referencia,
                }
                if reference and reference.tiene_fuente_verificada
                else None
            ),
            'margin_days': configuration.margen_ventana_dias,
            'source': (
                {
                    'institution': reference.institucion_fuente,
                    'document': reference.documento_fuente,
                    'url': reference.url_fuente,
                    'consulted_at': reference.fecha_consulta_fuente.isoformat(),
                }
                if reference and reference.tiene_fuente_verificada
                else None
            ),
            'factors_not_evaluated': [
                'Tipo y condición del suelo',
                'Pronóstico y variabilidad climática local',
                'Manejo y estado fenológico observados en terreno',
            ],
            'disclaimer': (
                'Evaluación orientativa basada en una ventana referencial; no garantiza '
                'la viabilidad de siembra ni la madurez real del cultivo.'
            ),
        }
    )
    return JsonResponse(result)
