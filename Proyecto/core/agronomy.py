"""Referencias agronómicas: ventanas de siembra y cosecha y evaluación de fechas propuestas.

Nada se inventa: solo se usan referencias verificadas con fuente; si falta información, el resultado lo dice.
"""

from datetime import date

from django.db import models

from .models import ReferenciaAgronomica

# Días de tolerancia alrededor de una ventana de siembra o cosecha para calificar una fecha como «cercana».
MARGEN_VENTANA_DIAS = 15

ESTADOS_EVALUACION = {
    'within': 'Dentro de ventana referencial',
    'near': 'Cercana al límite de la ventana',
    'outside': 'Fuera de ventana referencial',
    'insufficient': 'Información insuficiente',
}


def find_reference(crop_id, zone_id, variety, year=None):
    """Busca la referencia verificada más específica.

    Prioridad: variedad exacta antes que referencia general y, dentro de cada
    una, el año indicado antes que la ventana recurrente (sin año). Nunca usa
    una referencia de otro año específico.
    """
    references = ReferenciaAgronomica.objects.filter(
        cultivo_id=crop_id,
        zona_id=zone_id,
        estado=ReferenciaAgronomica.Estado.VERIFICADA,
    ).select_related('cultivo', 'zona')
    if year is not None:
        references = references.filter(models.Q(anio_referencia=year) | models.Q(anio_referencia__isnull=True))
    else:
        references = references.filter(anio_referencia__isnull=True)
    ordered = references.order_by(models.F('anio_referencia').desc(nulls_last=True), 'pk')
    if variety:
        reference = ordered.filter(variedad__iexact=variety).first()
        if reference:
            return reference
    return ordered.filter(variedad='').first()


def classify_calendar_window(proposal, reference, prefix, margin_days):
    start_month = getattr(reference, f'{prefix}_inicio_mes')
    start_day = getattr(reference, f'{prefix}_inicio_dia')
    end_month = getattr(reference, f'{prefix}_fin_mes')
    end_day = getattr(reference, f'{prefix}_fin_dia')
    if not all((start_month, start_day, end_month, end_day)):
        return 'insufficient', None

    intervals = []
    for start_year in range(proposal.year - 2, proposal.year + 2):
        try:
            start = date(start_year, start_month, start_day)
            end_year = start_year + ((end_month, end_day) < (start_month, start_day))
            end = date(end_year, end_month, end_day)
        except ValueError:
            continue
        intervals.append((start, end))

    if not intervals:
        return 'insufficient', None
    for start, end in intervals:
        if start <= proposal <= end:
            return 'within', 0

    distance = min(min(abs((proposal - start).days), abs((proposal - end).days)) for start, end in intervals)
    if distance <= margin_days:
        return 'near', distance
    return 'outside', distance


def evaluate_sowing_date(proposal, reference, margin_days):
    if reference is None or not reference.tiene_fuente_verificada:
        return {
            'status': 'insufficient',
            'status_label': ESTADOS_EVALUACION['insufficient'],
            'explanation': 'No hay una referencia verificada para este cultivo, zona y variedad.',
        }

    status, distance = classify_calendar_window(
        proposal,
        reference,
        'siembra',
        margin_days,
    )
    explanations = {
        'within': 'La fecha está dentro de la ventana publicada por la fuente.',
        'near': (f'La fecha está a {distance} día(s) del límite de la ventana ' 'y dentro del margen de referencia.'),
        'outside': 'La fecha está fuera de la ventana y del margen de referencia.',
        'insufficient': 'La fuente no registra una ventana completa de siembra.',
    }
    return {
        'status': status,
        'status_label': ESTADOS_EVALUACION[status],
        'explanation': explanations[status],
        'distance_days': distance,
    }


def evaluate_harvest_date(sowing_date, harvest_date, reference, margin_days):
    elapsed_days = (harvest_date - sowing_date).days
    if reference is None or not reference.tiene_fuente_verificada:
        return {
            'status': 'insufficient',
            'status_label': ESTADOS_EVALUACION['insufficient'],
            'explanation': (
                'No hay una referencia verificada para este cultivo, zona y variedad. '
                'No es posible evaluar la fecha de forma confiable.'
            ),
            'elapsed_days': elapsed_days,
            'expected_cycle_days': None,
            'cycle_difference_days': None,
        }

    seasonal_status, distance = classify_calendar_window(
        harvest_date,
        reference,
        'cosecha',
        margin_days,
    )
    if reference.duracion_ciclo_dias is None:
        if seasonal_status == 'insufficient':
            return {
                'status': 'insufficient',
                'status_label': ESTADOS_EVALUACION['insufficient'],
                'explanation': (
                    'La fuente no registra ventana de cosecha ni duración del ciclo; '
                    'no es posible evaluar la fecha de forma confiable.'
                ),
                'elapsed_days': elapsed_days,
                'expected_cycle_days': None,
                'cycle_difference_days': None,
            }
        explanation = {
            'within': 'La fecha cae dentro de la ventana estacional de cosecha publicada.',
            'near': (
                f'La fecha está a {distance} día(s) del límite de la ventana estacional '
                'y dentro del margen de referencia.'
            ),
            'outside': 'La fecha está fuera de la ventana estacional de cosecha y del margen de referencia.',
        }[seasonal_status]
        return {
            'status': seasonal_status,
            'status_label': ESTADOS_EVALUACION[seasonal_status],
            'explanation': explanation
            + (
                ' La fuente no documenta la duración del ciclo, por lo que no se comparó ' 'con los días transcurridos.'
            ),
            'distance_days': distance,
            'elapsed_days': elapsed_days,
            'expected_cycle_days': None,
            'cycle_difference_days': None,
        }
    cycle_difference = elapsed_days - reference.duracion_ciclo_dias

    if seasonal_status == 'insufficient':
        status = 'insufficient'
        explanation = 'La fuente no registra una ventana completa de cosecha.'
    elif abs(cycle_difference) > margin_days:
        status = 'outside'
        explanation = (
            'La duración prevista difiere más que el margen configurable del ciclo '
            'documentado. La madurez real no puede determinarse solo por calendario.'
        )
    else:
        status = seasonal_status
        explanation = {
            'within': 'La fecha y la duración prevista concuerdan con las referencias disponibles.',
            'near': (
                f'La fecha está a {distance} día(s) del límite de la ventana ' 'y dentro del margen de referencia.'
            ),
            'outside': 'La fecha está fuera de la ventana y del margen de referencia.',
        }[seasonal_status]

    return {
        'status': status,
        'status_label': ESTADOS_EVALUACION[status],
        'explanation': explanation,
        'distance_days': distance,
        'elapsed_days': elapsed_days,
        'expected_cycle_days': reference.duracion_ciclo_dias,
        'cycle_difference_days': cycle_difference,
    }
