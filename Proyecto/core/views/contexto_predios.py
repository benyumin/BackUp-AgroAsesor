"""Datos de predios y potreros que comparten varias pantallas (inicio, Mis predios, ficha, calendario).

Arma diccionarios listos para las plantillas y para el JavaScript del mapa: botones por potrero según el estado de
su planificación, siguiente paso del predio y el paquete ``farm_payload`` que se envía como JSON a la página.
"""

from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from ..models import (
    ActividadAgricola,
    Potrero,
    Predio,
)
from ..seasons import (
    PLAN_TOTAL,
    planning_state,
    season_payload,
)

# Al contar los potreros de un predio no se cuentan los archivados (el administrador por defecto ya los excluye,
# pero un Count() une la tabla directamente).
POTREROS_VIGENTES = Q(potreros__archivado_en__isnull=True)
# Actividades y cálculos de potreros y predios vigentes: los de un registro archivado se conservan, pero no se
# muestran (vuelven al restaurarlo).
EN_POTREROS_VIGENTES = Q(potrero__archivado_en__isnull=True, potrero__predio__archivado_en__isnull=True)


def map_config():
    return {
        'tile_url': settings.MAP_TILE_URL,
        'attribution': settings.MAP_TILE_ATTRIBUTION,
        # Clave pública del navegador de Google Maps (se protege con restricciones de sitio en Google Cloud).
        'google_maps_key': settings.GOOGLE_MAPS_API_KEY,
        'google_map_id': settings.GOOGLE_MAPS_MAP_ID,
        'debug': settings.DEBUG,
    }


def _task_summary(user, today):
    """Labores pendientes/vencidas por potrero y predio, y la próxima de cada potrero (solo lectura)."""
    from django.utils.formats import date_format

    plots, farms = {}, {}
    pending = (
        ActividadAgricola.objects.filter(
            EN_POTREROS_VIGENTES,
            usuario=user,
            estado=ActividadAgricola.Estado.PENDIENTE,
            potrero__predio__propietario=user,
        )
        .select_related('potrero')
        .order_by('fecha_programada', 'pk')
    )
    for activity in pending:
        overdue = activity.fecha_programada < today
        plot = plots.setdefault(activity.potrero_id, {'pending': 0, 'overdue': 0, 'next': None})
        farm = farms.setdefault(activity.potrero.predio_id, {'pending': 0, 'overdue': 0})
        plot['pending'] += 1
        farm['pending'] += 1
        if overdue:
            plot['overdue'] += 1
            farm['overdue'] += 1
        if plot['next'] is None:
            plot['next'] = {
                'label': activity.get_tipo_display(),
                'type': activity.tipo,
                'icon': ACTIVITY_ICONS.get(activity.tipo, 'clipboard-list'),
                'date': date_format(activity.fecha_programada, 'j M'),
                'overdue': overdue,
            }
    return plots, farms


def plan_form_url(plot, state):
    """Formulario donde se completa la planificación: la temporada vigente o, si aún no existe, su creación.

    Nunca el formulario general del potrero: allí no se edita la temporada y el botón volvería a la misma pantalla.
    """
    kwargs = {'predio_pk': plot.predio_id, 'potrero_pk': plot.pk}
    season = state['season']
    if season is not None:
        return reverse('temporada_edit', kwargs={**kwargs, 'pk': season.pk})
    return reverse('temporada_create', kwargs=kwargs)


def _plot_ui(plot, farm, tasks):
    state = planning_state(plot)  # el progreso sale de la temporada vigente, no de campos sueltos del potrero
    info = tasks.get(plot.pk, {})
    plan_query = {'potrero': plot.pk}
    if plot.cultivo_id:
        plan_query['crop'] = plot.cultivo_id
    if farm.zona_id:
        plan_query['zone'] = farm.zona_id
    edit_url = reverse('potrero_edit', kwargs={'predio_pk': farm.pk, 'pk': plot.pk})
    plan_form = plan_form_url(plot, state)
    # Acción principal del potrero según lo que le falta: sin cultivo se asigna; con cultivo se completa la
    # planificación en la temporada vigente; con los tres pasos hechos se calcula.
    if not state['has_crop']:
        primary = {'label': 'Asignar cultivo', 'url': edit_url}
    elif not state['complete']:
        primary = {'label': 'Completar planificación', 'url': plan_form}
    else:
        primary = {'label': 'Calcular semillas', 'url': decision_url('semillas', plot)}
    tool_actions = []
    if state['has_crop']:
        tool_actions = [
            {'label': 'Calcular semillas', 'url': decision_url('semillas', plot), 'icon': 'sprout'},
            {'label': 'Calcular insumos', 'url': decision_url('insumos', plot), 'icon': 'flask-conical'},
            {'label': 'Ver si alcanza', 'url': decision_url('alcanza', plot), 'icon': 'warehouse'},
        ]
    return {
        'primary': primary,
        'tool_actions': tool_actions,
        # Con la planificación completa, además de «Calcular semillas» se ofrecen las otras dos calculadoras.
        'extra_actions': tool_actions[1:] if state['complete'] else [],
        'edit_url': edit_url,
        'plan_form_url': plan_form,
        # Único acceso a la planificación en la ficha del predio: «Completar» mientras falte algo, «Editar» después.
        'plan_action': (
            {'label': 'Editar planificación' if state['complete'] else 'Completar planificación', 'url': plan_form}
            if state['has_crop']
            else None
        ),
        'season_id': state['season'].pk if state['season'] else None,
        'can_calculate': state['has_crop'] and state['steps'][1],
        'status': plot_season_summary(plot)['status'],
        'has_crop': state['has_crop'],
        'plan_done': state['done'],
        'plan_total': PLAN_TOTAL,
        'plan_complete': state['complete'],
        'plan_steps': state['steps'],
        'pending': info.get('pending', 0),
        'overdue': info.get('overdue', 0),
        'next': info.get('next'),
        'task_url': reverse('actividad_create') + '?' + urlencode({'potrero': plot.pk}),
        'plan_url': reverse('calendario') + '?' + urlencode(plan_query),
    }


def _farm_next_step(farm, plots, pending=0):
    """Una única recomendación para el predio, según lo que ya tiene registrado (solo presentación)."""
    plots = sorted(plots, key=lambda item: item.pk)
    if not plots:
        return {
            'key': 'potrero',
            'title': 'Crea tu primer potrero',
            'text': 'Divide tu predio en sectores o usa todo el predio como un solo potrero.',
            'cta': 'Agregar potrero',
            'url': reverse('potrero_create', kwargs={'predio_pk': farm.pk}),
        }
    states = {plot.pk: planning_state(plot) for plot in plots}
    without_crop = next((plot for plot in plots if not states[plot.pk]['has_crop']), None)
    if without_crop:
        return {
            'key': 'cultivo',
            'title': f'Asigna un cultivo a «{without_crop.nombre}»',
            'text': 'Con el cultivo podrás planificar la siembra y usar las herramientas de cálculo.',
            'cta': 'Asignar cultivo',
            'url': reverse('potrero_edit', kwargs={'predio_pk': farm.pk, 'pk': without_crop.pk}),
        }
    unplanned = next((plot for plot in plots if not states[plot.pk]['complete']), None)
    if unplanned:
        return {
            'key': 'planificacion',
            'title': f'Define la planificación de «{unplanned.nombre}»',
            'text': 'Registra la fecha de siembra y la cosecha prevista.',
            'cta': 'Definir planificación',
            'url': plan_form_url(unplanned, states[unplanned.pk]),
        }
    first = plots[0]
    return {
        'key': 'decisiones',
        'title': f'Calcula lo que necesitas para «{first.nombre}»',
        'text': 'Tu predio ya está planificado: calcula semillas e insumos, o revisa si te alcanza.'
        + ('' if pending else ' Después puedes programar tus labores.'),
        'cta': 'Calcular semillas',
        'url': decision_url('semillas', first),
    }


def _farm_ui(farm, plots, farm_tasks, plot_uis):
    info = farm_tasks.get(farm.pk, {})
    pending = info.get('pending', 0)
    return {
        'plot_count': len(plots),
        'crop_count': len({plot.cultivo_id for plot in plots if plot.cultivo_id}),
        'pending': pending,
        'overdue': info.get('overdue', 0),
        'all_planned': bool(plots) and all(plot_uis[plot.pk]['plan_complete'] for plot in plots),
        'next_step': _farm_next_step(farm, plots, pending),
    }


def decision_url(tool='semillas', plot=None):
    """Enlace al Centro de decisiones con el potrero ya elegido (la herramienta y el cultivo se precargan)."""
    query = {}
    if tool in ('semillas', 'insumos'):
        query['tipo'] = tool
    elif tool == 'alcanza':
        query['herramienta'] = tool
    if plot is not None:
        query['potrero'] = plot.pk if hasattr(plot, 'pk') else plot
    return reverse('decisiones') + ('?' + urlencode(query) if query else '')


def farm_cards(user, predios=None):
    """Predios con sus potreros y los indicadores de presentación que ya salen de los datos existentes."""
    today = timezone.localdate()
    task_plots, task_farms = _task_summary(user, today)
    if predios is None:
        predios = (
            Predio.objects.filter(propietario=user)
            .annotate(potreros_count=Count('potreros', filter=POTREROS_VIGENTES))
            .prefetch_related('potreros__cultivo', 'potreros__temporadas__cultivo')
        )
    cards = []
    for farm in predios:
        plots = list(farm.potreros.all())
        plot_uis = {plot.pk: _plot_ui(plot, farm, task_plots) for plot in plots}
        cards.append(
            {
                'predio': farm,
                'ui': _farm_ui(farm, plots, task_farms, plot_uis),
                'plots': [{'plot': plot, 'ui': plot_uis[plot.pk]} for plot in plots],
            }
        )
    return cards


def farm_payload(user):
    predios = list(
        Predio.objects.filter(propietario=user).select_related('zona').annotate(potreros_count=Count('potreros', filter=POTREROS_VIGENTES))
    )
    plots = list(
        Potrero.objects.filter(predio__propietario=user)
        .select_related('predio__zona', 'cultivo')
        .prefetch_related('temporadas__cultivo')
    )
    assigned_by_farm = {}
    for plot in plots:
        assigned_by_farm[plot.predio_id] = assigned_by_farm.get(plot.predio_id, Decimal('0')) + plot.superficie_ha
    total_farm_area = sum(
        (predio.superficie_ha for predio in predios),
        Decimal('0'),
    )
    assigned_area = sum(
        (plot.superficie_ha for plot in plots),
        Decimal('0'),
    )
    unassigned_area = sum(
        (max(Decimal('0'), predio.superficie_ha - assigned_by_farm.get(predio.pk, Decimal('0'))) for predio in predios),
        Decimal('0'),
    )
    crop_count = (
        Potrero.objects.filter(
            predio__propietario=user,
            cultivo__activo=True,
        )
        .values('cultivo_id')
        .distinct()
        .count()
    )
    distribution_pct = assigned_area / total_farm_area * 100 if total_farm_area else Decimal('0')
    task_plots, task_farms = _task_summary(user, timezone.localdate())
    plots_by_farm = {}
    for plot in sorted(plots, key=lambda item: item.pk):
        plots_by_farm.setdefault(plot.predio_id, []).append(plot)
    plot_ui = {plot.pk: _plot_ui(plot, plot.predio, task_plots) for plot in plots}
    plot_plan = {plot.pk: planning_state(plot) for plot in plots}
    farm_ui = {farm.pk: _farm_ui(farm, plots_by_farm.get(farm.pk, []), task_farms, plot_ui) for farm in predios}
    return {
        'predios': [
            {
                'id': str(p.pk),
                'name': p.nombre,
                'location': p.ubicacion,
                'area': float(p.superficie_ha),
                'cropCount': farm_ui[p.pk]['crop_count'],
                'pendingTasks': farm_ui[p.pk]['pending'],
                'overdueTasks': farm_ui[p.pk]['overdue'],
                'allPlanned': farm_ui[p.pk]['all_planned'],
                'nextStep': farm_ui[p.pk]['next_step'],
                'zoneId': p.zona_id,
                'zoneName': p.zona.nombre if p.zona_id else '',
                'plotCount': p.potreros_count,
                'updated': timezone.localtime(p.actualizado).strftime('%d/%m/%Y'),
                'lat': p.latitud,
                'lng': p.longitud,
                'geojson': p.geojson,
                'perimeterM': float(p.perimetro_m) if p.perimetro_m is not None else None,
                'detailUrl': reverse('predio_detail', kwargs={'pk': p.pk}),
                'setupUrl': reverse('predio_detail', kwargs={'pk': p.pk}) + '?nuevo=1',
                'editUrl': reverse('predio_edit', kwargs={'pk': p.pk}),
                'potreroCreateUrl': reverse('potrero_create', kwargs={'predio_pk': p.pk}),
            }
            for p in predios
        ],
        'plots': [
            {
                **plot_season_summary(p),
                'id': str(p.pk),
                'predio': str(p.predio_id),
                'farmName': p.predio.nombre,
                'zoneId': p.predio.zona_id,
                'zoneName': p.predio.zona.nombre if p.predio.zona_id else '',
                'planDone': plot_ui[p.pk]['plan_done'],
                'planTotal': PLAN_TOTAL,
                'primaryLabel': plot_ui[p.pk]['primary']['label'],
                'primaryUrl': plot_ui[p.pk]['primary']['url'],
                'planFormUrl': plot_ui[p.pk]['plan_form_url'],
                'pendingTasks': plot_ui[p.pk]['pending'],
                'overdueTasks': plot_ui[p.pk]['overdue'],
                'nextTask': plot_ui[p.pk]['next'],
                'taskUrl': plot_ui[p.pk]['task_url'],
                'planUrl': plot_ui[p.pk]['plan_url'],
                'seedsUrl': decision_url('semillas', p),
                'name': p.nombre,
                'crop': p.cultivo.nombre if p.cultivo else '',
                'variety': plot_plan[p.pk]['variety'],
                'sowing': plot_plan[p.pk]['sowing_date'].isoformat() if plot_plan[p.pk]['sowing_date'] else '',
                'harvest': plot_plan[p.pk]['harvest_date'].isoformat() if plot_plan[p.pk]['harvest_date'] else '',
                'areaHa': float(p.superficie_ha),
                'areaM2': float(p.superficie_ha * Decimal('10000')),
                'perimeterM': float(p.perimetro_m) if p.perimetro_m is not None else None,
                'coordinates': p.perimetro,
                'geojson': p.geojson,
                'lat': p.latitud,
                'lng': p.longitud,
                'created': timezone.localtime(p.creado).strftime('%d/%m/%Y'),
                'updated': timezone.localtime(p.actualizado).strftime('%d/%m/%Y'),
                'cropId': str(p.cultivo_id) if p.cultivo_id else '',
                'assignUrl': reverse('potrero_assign_crop', kwargs={'pk': p.pk}),
                'seasonCreateUrl': reverse('temporada_create', kwargs={'predio_pk': p.predio_id, 'potrero_pk': p.pk}),
                'editUrl': reverse('potrero_edit', kwargs={'predio_pk': p.predio_id, 'pk': p.pk}),
                'detailUrl': (reverse('predio_detail', kwargs={'pk': p.predio_id}) + f'?potrero={p.pk}#predio-history'),
            }
            for p in plots
        ],
        'stats': {
            'predios': len(predios),
            'potreros': len(plots),
            'superficie_predios': float(total_farm_area),
            'superficie': float(assigned_area),
            'superficie_sin_asignar': float(unassigned_area),
            'distribucion_pct': float(distribution_pct),
            'distribucion_bar_pct': float(min(Decimal('100'), max(Decimal('0'), distribution_pct))),
            'sobreasignada': assigned_area > total_farm_area,
            'cultivos': crop_count,
        },
    }


def empty_farm_payload():
    """Misma forma que ``farm_payload()`` de una cuenta sin predios, sin consultar la base de datos.

    La usan las pantallas que no leen ``agro-farms`` (ver ``FARM_DATA_TEMPLATES`` en ``views/base.py``).
    """
    return {
        'predios': [],
        'plots': [],
        'stats': {
            'predios': 0,
            'potreros': 0,
            'superficie_predios': 0.0,
            'superficie': 0.0,
            'superficie_sin_asignar': 0.0,
            'distribucion_pct': 0.0,
            'distribucion_bar_pct': 0.0,
            'sobreasignada': False,
            'cultivos': 0,
        },
    }


def plot_season_summary(plot):
    seasons = sorted(plot.temporadas.all(), key=lambda item: (item.creado, item.pk), reverse=True)
    current = seasons[0] if seasons else None
    if current:
        status = current.get_estado_display()
        status_code = current.estado
    elif plot.cultivo_id:
        status, status_code = 'Cultivo registrado sin temporada', 'legacy'
    else:
        status, status_code = 'Sin cultivo', 'none'
    return {
        'status': status,
        'statusCode': status_code,
        'season': current.temporada_agricola if current else '',
        'currentSeasonId': current.pk if current else None,
        'seasons': [season_payload(season) for season in seasons],
    }


ACTIVITY_ICONS = {
    'preparacion': 'shovel',
    'siembra': 'sprout',
    'fertilizacion': 'flask-conical',
    'riego': 'droplets',
    'monitoreo': 'scan-search',
    'control_fitosanitario': 'shield-alert',
    'cosecha': 'wheat',
}
