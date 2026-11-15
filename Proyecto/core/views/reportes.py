"""Reportes: historial de cálculos, actividades y predios con filtros, y exportación a CSV.

``describe_calculation`` convierte un ``CalculoAgricola`` guardado (JSON de entrada y resultado) en un texto legible;
lo usan el inicio y los reportes.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.utils import timezone

from ..models import (
    ActividadAgricola,
    CalculoAgricola,
    Potrero,
    Predio,
    TemporadaCultivo,
)
from ..pdf import respuesta_pdf
from ..pdf.informes import informe_reporte
from .base import render_page
from .contexto_predios import EN_POTREROS_VIGENTES, plot_season_summary

REPORT_TYPES = [
    ('resumen', 'Resumen del predio', 'Superficie, potreros, cultivos actuales y estado de las labores.'),
    ('cuaderno', 'Cuaderno de campo', 'Labores realizadas con producto, dosis, superficie y responsable.'),
    ('cultivos', 'Historial de cultivos', 'Temporadas por potrero: cultivo, variedad, fechas y estado.'),
    ('calculos', 'Cálculos guardados', 'Semillas, insumos y disponibilidad.'),
]


def _fmt_number(value, digits=2):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ''
    text = f'{number:,.{digits}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return text.rstrip('0').rstrip(',') if ',' in text else text


def describe_calculation(calculation):
    """Resumen legible de un cálculo guardado (entrada y resultado JSON)."""
    data, entry = calculation.resultado or {}, calculation.entrada or {}
    operation = calculation.operacion
    detail, value = '', ''
    if operation == 'dose':
        value = f"{_fmt_number(data.get('amount'))} {data.get('unit', '')}"
        detail = (
            f"{_fmt_number(data.get('dose'), 3)} {data.get('dose_unit', '')} × {_fmt_number(entry.get('area_ha'), 4)} ha"
        )
    elif operation == 'coverage':
        value = f"{_fmt_number(data.get('percent'), 0)} % cubierto"
        detail = f"Necesario {_fmt_number(data.get('needed'))} {data.get('unit', '')} · disponible {_fmt_number(data.get('stock', entry.get('stock')))} {data.get('unit', '')}"
    elif operation == 'seed_population':
        value = f"{_fmt_number(data.get('amount'))} kg"
        detail = (
            f"{_fmt_number(data.get('rate'), 3)} kg/ha × {_fmt_number(entry.get('area_ha'), 4)} ha "
            '(por población objetivo)'
        )
    elif operation == 'fertilizer_nutrition':
        value = f"{_fmt_number(data.get('amount'))} {data.get('unit', 'kg')}"
        detail = f"{data.get('nutrient', '')}: {_fmt_number(data.get('nutrientNeedPerHa'))} kg/ha"
    elif operation == 'yield':
        value = f"{_fmt_number(data.get('production'))} ton"
        detail = f"{_fmt_number(data.get('rate'))} ton/ha × {_fmt_number(entry.get('area_ha'), 4)} ha"
    source = data.get('source') or data.get('nutrientSource') or ''
    kind = data.get('origin')
    if kind == 'adjusted' or (kind is None and data.get('adjusted')):
        origin = f"Ajustado manualmente (catálogo: {_fmt_number(data.get('catalog_dose'), 3)} {data.get('catalog_unit', '')}) · simulación"
    elif kind == 'catalog':
        origin = 'Dato de catálogo (sin respaldo completo)'
    elif kind == 'documented':
        origin = f'Documentado: {source}' if source else 'Documentado'
    elif data.get('simulation'):
        origin = 'Simulación (dato sin fuente verificada)'
        if data.get('declared_source'):
            origin += f" · fuente indicada por la persona: {data['declared_source']}"
    elif data.get('verified') or data.get('source_verified'):
        origin = f'Documentado: {source}' if source else 'Documentado'
    else:
        origin = source or 'Dato ingresado por el usuario'
    return {
        'date': timezone.localtime(calculation.creado),
        'farm': calculation.potrero.predio.nombre,
        'plot': calculation.potrero.nombre,
        'operation': calculation.get_operacion_display(),
        'crop': entry.get('crop', ''),
        'product': data.get('product', '') or '',
        'value': value.strip(),
        'detail': detail.strip(),
        'origin': origin,
    }


def _pdf_del_reporte(context, columns, rows, plots_all, farms, autor):
    """PDF del reporte con las mismas filas que la tabla y el CSV, más los filtros e indicadores."""
    filtros = context['report_filters']
    predio = next((f.nombre for f in farms if str(f.pk) == filtros['predio']), 'Todos')
    potrero = next((p.nombre for p in plots_all if str(p.pk) == filtros['potrero']), 'Todos')
    desde = date.fromisoformat(filtros['desde']).strftime('%d/%m/%Y') if filtros['desde'] else 'Sin límite'
    hasta = date.fromisoformat(filtros['hasta']).strftime('%d/%m/%Y') if filtros['hasta'] else 'Sin límite'
    datos_filtro = [('Predio', predio), ('Potrero', potrero), ('Desde', desde), ('Hasta', hasta)]
    if context['report_type'] == 'cuaderno':
        datos_filtro.append(
            ('Labores incluidas', 'Pendientes y realizadas' if filtros['incluir_pendientes'] else 'Solo realizadas')
        )
    indicadores = None
    kpis = context.get('summary_kpis')
    if kpis:
        indicadores = [
            ('Predios', kpis['predios']),
            ('Superficie total', f"{_fmt_number(kpis['superficie'], 4)} ha"),
            ('Potreros', kpis['potreros']),
            ('Potreros con cultivo', kpis['con_cultivo']),
            ('Labores pendientes', kpis['pendientes']),
            ('Labores vencidas', kpis['vencidas']),
            ('Labores completadas', kpis['completadas']),
        ]
    descripcion = next(help_text for key, _label, help_text in REPORT_TYPES if key == context['report_type'])
    return informe_reporte(
        context['report_title'],
        descripcion,
        columns,
        rows,
        datos_filtro,
        autor=autor,
        indicadores=indicadores,
    )


@login_required
def reportes(request):
    today = timezone.localdate()
    report_type = request.GET.get('tipo', 'resumen')
    if report_type not in {key for key, _label, _help in REPORT_TYPES}:
        report_type = 'resumen'
    farms = Predio.objects.filter(propietario=request.user).order_by('nombre')
    plots_all = (
        Potrero.objects.filter(predio__propietario=request.user)
        .select_related('predio')
        .order_by('predio__nombre', 'nombre')
    )
    filters = {
        'predio': request.GET.get('predio', ''),
        'potrero': request.GET.get('potrero', ''),
        'desde': request.GET.get('desde', ''),
        'hasta': request.GET.get('hasta', ''),
        'incluir_pendientes': request.GET.get('incluir_pendientes') == '1',
    }
    start = end = None
    try:
        start = date.fromisoformat(filters['desde']) if filters['desde'] else None
    except ValueError:
        filters['desde'] = ''
    try:
        end = date.fromisoformat(filters['hasta']) if filters['hasta'] else None
    except ValueError:
        filters['hasta'] = ''

    plots = plots_all.select_related('cultivo').prefetch_related('temporadas__cultivo')
    if filters['predio'].isdigit():
        plots = plots.filter(predio_id=filters['predio'])
    if filters['potrero'].isdigit():
        plots = plots.filter(pk=filters['potrero'])
    plot_ids = list(plots.values_list('pk', flat=True))
    selected_farms = (
        farms.filter(pk__in=plots.values('predio_id')) if (filters['predio'] or filters['potrero']) else farms
    )

    context = {
        'report_types': REPORT_TYPES,
        'report_type': report_type,
        'report_title': dict((key, label) for key, label, _help in REPORT_TYPES)[report_type],
        'report_filters': filters,
        'report_farms': farms,
        'report_plots': plots_all,
        'generated_at': timezone.localtime(),
        'owner_name': request.user.get_full_name() or request.user.username,
    }

    columns, rows = [], []
    if report_type == 'resumen':
        farm_rows = []
        for farm in selected_farms:
            farm_plots = [plot for plot in plots if plot.predio_id == farm.pk]
            used = sum((plot.superficie_ha for plot in farm_plots), Decimal('0'))
            plot_rows = []
            for plot in farm_plots:
                summary = plot_season_summary(plot)
                plot_rows.append(
                    {
                        'plot': plot,
                        'status': summary['status'],
                        'season': summary['season'],
                    }
                )
                rows.append(
                    [
                        farm.nombre,
                        plot.nombre,
                        _fmt_number(plot.superficie_ha, 4),
                        plot.cultivo.nombre if plot.cultivo_id else 'Sin cultivo',
                        plot.variedad,
                        plot.fecha_siembra.strftime('%d/%m/%Y') if plot.fecha_siembra else '',
                        plot.fecha_cosecha.strftime('%d/%m/%Y') if plot.fecha_cosecha else '',
                        summary['status'],
                    ]
                )
            farm_rows.append(
                {
                    'farm': farm,
                    'plots': plot_rows,
                    'used': used,
                    'free': max(Decimal('0'), farm.superficie_ha - used),
                    'use_pct': (used / farm.superficie_ha * 100) if farm.superficie_ha else 0,
                }
            )
        activities = ActividadAgricola.objects.filter(usuario=request.user, potrero_id__in=plot_ids)
        context.update(
            {
                'farm_rows': farm_rows,
                'summary_kpis': {
                    'predios': len(farm_rows),
                    'superficie': sum((row['farm'].superficie_ha for row in farm_rows), Decimal('0')),
                    'potreros': len(plot_ids),
                    'con_cultivo': plots.exclude(cultivo=None).count(),
                    'pendientes': activities.filter(estado=ActividadAgricola.Estado.PENDIENTE).count(),
                    'vencidas': activities.filter(
                        estado=ActividadAgricola.Estado.PENDIENTE, fecha_programada__lt=today
                    ).count(),
                    'completadas': activities.filter(estado=ActividadAgricola.Estado.COMPLETADA).count(),
                },
                'recent_calculations': [
                    describe_calculation(item)
                    for item in CalculoAgricola.objects.filter(
                        usuario=request.user,
                        potrero_id__in=plot_ids,
                    )
                    .select_related('potrero__predio')
                    .order_by('-creado', '-pk')[:5]
                ],
            }
        )
        columns = [
            'Predio',
            'Potrero',
            'Superficie (ha)',
            'Cultivo',
            'Variedad',
            'Siembra',
            'Cosecha prevista',
            'Estado',
        ]
    elif report_type == 'cuaderno':
        activities = ActividadAgricola.objects.filter(
            usuario=request.user,
            potrero_id__in=plot_ids,
        ).select_related('potrero__predio', 'insumo__tipo', 'temporada__cultivo', 'potrero__cultivo')
        if not filters['incluir_pendientes']:
            activities = activities.filter(estado=ActividadAgricola.Estado.COMPLETADA)
        else:
            activities = activities.exclude(estado=ActividadAgricola.Estado.CANCELADA)
        date_field = 'fecha_realizada' if not filters['incluir_pendientes'] else 'fecha_programada'
        if start:
            activities = activities.filter(**{f'{date_field}__gte': start})
        if end:
            activities = activities.filter(**{f'{date_field}__lte': end})
        activities = list(activities.order_by(date_field, 'pk'))
        context['field_log'] = activities
        context['field_log_phyto'] = sum(
            1 for item in activities if item.tipo == ActividadAgricola.Tipo.CONTROL_FITOSANITARIO
        )
        context['field_log_conflicts'] = sum(1 for item in activities if item.conflicto_carencia)
        columns = [
            'Fecha',
            'Predio',
            'Potrero',
            'Cultivo',
            'Labor',
            'Producto',
            'Objetivo',
            'Dosis',
            'Unidad',
            'Superficie (ha)',
            'Cantidad total',
            'Responsable',
            'Condiciones',
            'Fin de carencia',
            'Estado',
        ]
        for item in activities:
            when = item.fecha_realizada or item.fecha_programada
            crop = (
                item.temporada.cultivo.nombre
                if item.temporada_id
                else (item.potrero.cultivo.nombre if item.potrero.cultivo_id else '')
            )
            rows.append(
                [
                    when.strftime('%d/%m/%Y'),
                    item.potrero.predio.nombre,
                    item.potrero.nombre,
                    crop,
                    item.get_tipo_display(),
                    item.producto_nombre,
                    item.objetivo,
                    _fmt_number(item.dosis, 3) if item.dosis else '',
                    item.unidad_dosis,
                    _fmt_number(item.superficie_ha, 4) if item.superficie_ha else '',
                    f'{_fmt_number(item.cantidad_total)} {item.unidad_total}' if item.cantidad_total else '',
                    item.responsable,
                    item.condiciones_clima,
                    item.fecha_fin_carencia.strftime('%d/%m/%Y') if item.fecha_fin_carencia else '',
                    item.get_estado_display(),
                ]
            )
    elif report_type == 'cultivos':
        seasons = TemporadaCultivo.objects.filter(potrero_id__in=plot_ids).select_related('potrero__predio', 'cultivo')
        if start:
            seasons = seasons.filter(
                Q(fecha_siembra__gte=start) | Q(fecha_siembra__isnull=True, creado__date__gte=start)
            )
        if end:
            seasons = seasons.filter(Q(fecha_siembra__lte=end) | Q(fecha_siembra__isnull=True, creado__date__lte=end))
        seasons = list(seasons.order_by('potrero__predio__nombre', 'potrero__nombre', '-creado'))
        for season in seasons:
            season.duration = (
                (season.fecha_cosecha - season.fecha_siembra).days
                if season.fecha_siembra and season.fecha_cosecha
                else None
            )
        context['seasons'] = seasons
        columns = [
            'Predio',
            'Potrero',
            'Temporada',
            'Cultivo',
            'Variedad',
            'Siembra',
            'Cosecha',
            'Días',
            'Estado',
            'Observaciones',
        ]
        for season in seasons:
            rows.append(
                [
                    season.potrero.predio.nombre,
                    season.potrero.nombre,
                    season.temporada_agricola,
                    season.cultivo.nombre,
                    season.variedad,
                    season.fecha_siembra.strftime('%d/%m/%Y') if season.fecha_siembra else '',
                    season.fecha_cosecha.strftime('%d/%m/%Y') if season.fecha_cosecha else '',
                    season.duration if season.duration is not None else '',
                    season.get_estado_display(),
                    season.observaciones,
                ]
            )
    else:
        calculations = (
            CalculoAgricola.objects.filter(
                usuario=request.user,
                potrero_id__in=plot_ids,
            )
            .select_related('potrero__predio')
            .order_by('-creado', '-pk')
        )
        if start:
            calculations = calculations.filter(creado__date__gte=start)
        if end:
            calculations = calculations.filter(creado__date__lte=end)
        described = [describe_calculation(item) for item in calculations[:300]]
        context['calculations'] = described
        columns = [
            'Fecha',
            'Predio',
            'Potrero',
            'Operación',
            'Cultivo',
            'Producto',
            'Resultado',
            'Detalle',
            'Origen del dato',
        ]
        for item in described:
            rows.append(
                [
                    item['date'].strftime('%d/%m/%Y %H:%M'),
                    item['farm'],
                    item['plot'],
                    item['operation'],
                    item['crop'],
                    item['product'],
                    item['value'],
                    item['detail'],
                    item['origin'],
                ]
            )

    if request.GET.get('formato') == 'pdf':
        return respuesta_pdf(
            _pdf_del_reporte(context, columns, rows, plots_all, farms, request.user.get_full_name()),
            f'agroasesor-{report_type}-{today:%Y%m%d}.pdf',
        )

    if request.GET.get('formato') == 'csv':
        import csv

        from django.http import HttpResponse

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="agroasesor-{report_type}-{today:%Y%m%d}.csv"'
        response.write('﻿')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(columns)
        writer.writerows(rows)
        return response

    # Compatibilidad: historial de cálculos del usuario para la exportación anterior.
    calculations = (
        CalculoAgricola.objects.filter(
            EN_POTREROS_VIGENTES,
            usuario=request.user,
            potrero__predio__propietario=request.user,
        )
        .select_related('potrero', 'potrero__predio')
        .order_by('-creado', '-pk')[:100]
    )
    context['report_records'] = [
        {
            'id': calculation.pk,
            'operation': calculation.operacion,
            'operationLabel': calculation.get_operacion_display(),
            'category': calculation.entrada.get('calculation_type', calculation.operacion),
            'created': timezone.localtime(calculation.creado).strftime('%d/%m/%Y %H:%M'),
            'plot': calculation.potrero.nombre,
            'farm': calculation.potrero.predio.nombre,
            'input': calculation.entrada,
            'result': calculation.resultado,
        }
        for calculation in calculations
    ]
    query = request.GET.copy()
    query['formato'] = 'csv'
    context['csv_query'] = query.urlencode()
    query['formato'] = 'pdf'
    context['pdf_query'] = query.urlencode()
    context['row_count'] = len(rows)
    return render_page(request, 'reportes.html', 'reportes', context)
