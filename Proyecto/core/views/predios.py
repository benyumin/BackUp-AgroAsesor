"""Predios, potreros y temporadas de cultivo.

* ``predios`` y ``predio_detail``: listado con mapa y ficha de un predio.
* ``predio_form`` y ``potrero_form``: alta y edición (con dibujo del contorno en el mapa).
* ``temporada_form``: planificación de la temporada de un potrero (cultivo, variedad y fechas).
* ``potrero_api`` y ``potrero_assign_crop``: pequeña API JSON que usa el mapa para leer y asignar cultivos.

Las reglas de temporadas viven en ``core.seasons`` y la geometría en ``core.geometry``.
"""

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_POST

from ..forms import (
    PotreroForm,
    PredioForm,
    TemporadaCultivoForm,
)
from ..geometry import coordinates_from_geojson
from ..models import (
    Cultivo,
    Potrero,
    Predio,
    PredioHistorial,
    TemporadaCultivo,
)
from ..seasons import (
    assign_crop,
    planning_state,
    record_form_crop_change,
    save_season_form,
    season_form_initial,
    update_current_dates,
    vigente_season,
)
from .base import render_page
from .contexto_predios import POTREROS_VIGENTES, farm_cards, farm_payload, map_config


@login_required
def predios(request):
    predios_list = (
        Predio.objects.filter(propietario=request.user)
        .annotate(
            potreros_count=Count('potreros', filter=POTREROS_VIGENTES),
        )
        .prefetch_related('potreros__cultivo', 'potreros__temporadas__cultivo')
    )
    selected_predio_id = request.GET.get('predio', '')
    if selected_predio_id:
        selected_predio = get_object_or_404(
            Predio,
            pk=selected_predio_id,
            propietario=request.user,
        )
    else:
        selected_predio = None
    selected_potrero_id = request.GET.get('potrero', '').strip()
    if selected_potrero_id:
        selected_potrero = get_object_or_404(
            Potrero.objects.select_related('predio'),
            pk=selected_potrero_id if selected_potrero_id.isdigit() else 0,
            predio__propietario=request.user,
        )
        selected_predio = selected_potrero.predio
        selected_potrero_id = str(selected_potrero.pk)

    potrero_queryset = Potrero.objects.filter(
        predio__propietario=request.user,
    ).select_related('predio', 'cultivo')
    search_term = request.GET.get('q', '').strip()
    filter_predio = request.GET.get('filtro_predio', '').strip()
    filter_cultivo = request.GET.get('cultivo', '').strip()
    sort_by = request.GET.get('orden', '-creado')
    if search_term:
        potrero_queryset = potrero_queryset.filter(nombre__icontains=search_term)
    if filter_predio:
        potrero_queryset = potrero_queryset.filter(predio_id=filter_predio)
    if filter_cultivo:
        potrero_queryset = potrero_queryset.filter(cultivo_id=filter_cultivo)
    ordering = {
        'creado': 'creado',
        '-creado': '-creado',
        'actualizado': 'actualizado',
        '-actualizado': '-actualizado',
        'nombre': 'nombre',
        '-nombre': '-nombre',
    }
    potrero_queryset = potrero_queryset.order_by(ordering.get(sort_by, '-creado'), 'pk')
    history_page = Paginator(potrero_queryset, 25).get_page(request.GET.get('pagina'))
    crop_options = (
        Cultivo.objects.filter(
            potreros__predio__propietario=request.user,
            potreros__archivado_en__isnull=True,
        )
        .distinct()
        .order_by('nombre')
    )
    return render_page(
        request,
        'predios.html',
        'predios',
        {
            'predios_list': predios_list,
            'farm_cards': farm_cards(request.user, predios_list),
            'selected_predio': selected_predio,
            'selected_predio_id': str(selected_predio.pk) if selected_predio else '',
            'selected_potrero_id': selected_potrero_id,
            'potrero_history_page': history_page,
            'crop_options': crop_options,
            'terrain_filters': {
                'q': search_term,
                'predio': filter_predio,
                'cultivo': filter_cultivo,
                'orden': sort_by,
            },
            'maps_config': map_config(),
            'archived_count': Predio.todos.filter(propietario=request.user, archivado_en__isnull=False).count()
            + Potrero.todos.filter(
                predio__propietario=request.user, archivado_en__isnull=False, predio__archivado_en__isnull=True
            ).count(),
        },
    )


@login_required
def predio_detail(request, pk):
    predio = get_object_or_404(
        Predio.objects.prefetch_related(
            'potreros__cultivo',
            'potreros__temporadas__cultivo',
            'historial__usuario',
        ),
        pk=pk,
        propietario=request.user,
    )
    potreros = predio.potreros.all()
    selected_potrero_id = request.GET.get('potrero', '').strip()
    selected_potrero = None
    historial = predio.historial.select_related('usuario', 'potrero')
    if selected_potrero_id:
        selected_potrero = get_object_or_404(
            potreros,
            pk=selected_potrero_id,
        )
        historial = historial.filter(potrero=selected_potrero)
    superficie_utilizada = potreros.aggregate(total=Sum('superficie_ha'))['total'] or Decimal('0')
    # El asistente «¿Cómo deseas organizar tu predio?» se muestra mientras el predio no tenga potreros (recién
    # registrado o con la decisión pendiente) y desaparece en cuanto existe el primero.
    needs_setup = not potreros
    can_use_whole = bool(predio.geojson) and needs_setup
    farm_card = farm_cards(request.user, [predio])[0]
    return render_page(
        request,
        'predio_detail.html',
        'predios',
        {
            'journey': None,  # la ficha ya trae su propio «Siguiente paso» por predio
            'farm_card': farm_card,
            'next_step': None if needs_setup else farm_card['ui']['next_step'],
            'needs_setup': needs_setup,
            'can_use_whole': can_use_whole,
            'predio': predio,
            'potreros': potreros,
            'historial': historial,
            'selected_potrero': selected_potrero,
            'superficie_utilizada': superficie_utilizada,
            'superficie_restante': max(Decimal('0'), predio.superficie_ha - superficie_utilizada),
            'cultivos': Cultivo.objects.filter(potreros__predio=predio, potreros__archivado_en__isnull=True).distinct(),
            'maps_config': map_config(),
        },
    )


def _without_geometry(farm_data):
    """Copia del resumen de predios sin trazados ni coordenadas.

    El formulario de predio no necesita (ni debe recibir) las geometrías de otros predios o potreros:
    la del predio en edición llega por el propio formulario.
    """
    return {
        **farm_data,
        'predios': [{**farm, 'geojson': None, 'lat': None, 'lng': None} for farm in farm_data['predios']],
        'plots': [
            {**plot, 'geojson': None, 'coordinates': None, 'lat': None, 'lng': None} for plot in farm_data['plots']
        ],
    }


# Ventana en la que un segundo envío idéntico se considera el mismo registro enviado dos veces (doble clic, toque
# repetido, botón «Atrás» + reenviar). Pasado ese tiempo se trata como un registro nuevo.
RESUBMIT_WINDOW = timedelta(minutes=2)


def _same_json(left, right):
    """Compara dos geometrías ya guardadas o recibidas, sin importar el orden de las claves."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _recent_predio_duplicate(user, cleaned):
    """Predio que esta misma persona acaba de registrar con los mismos datos y el mismo trazado."""
    candidates = Predio.objects.filter(
        propietario=user,
        nombre=cleaned.get('nombre'),
        superficie_ha=cleaned.get('superficie_ha'),
        creado__gte=timezone.now() - RESUBMIT_WINDOW,
    ).order_by('-creado', '-pk')
    for candidate in candidates:
        if _same_json(candidate.geojson, cleaned.get('geojson')):
            return candidate
    return None


@login_required
@transaction.atomic
def predio_form(request, pk=None):
    instance = get_object_or_404(Predio, pk=pk, propietario=request.user) if pk else Predio(propietario=request.user)
    original = None
    if instance.pk:
        original = {
            'nombre': instance.nombre,
            'descripcion': instance.descripcion,
            'ubicacion': instance.ubicacion,
            'observaciones': instance.observaciones,
            'zona_id': instance.zona_id,
            'zona': instance.zona.nombre if instance.zona_id else 'Sin zona',
            'superficie_ha': instance.superficie_ha,
            'geojson': instance.geojson,
        }
    form = PredioForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        geometry = form.cleaned_data.get('geojson')
        if not original and not geometry:
            form.add_error('geojson', 'Dibuja y acepta el perímetro para registrar el predio.')
        elif not original and not form.cleaned_data.get('perimetro_aceptado'):
            form.add_error('perimetro_aceptado', 'Acepta el perímetro antes de registrar el predio.')
        elif original and geometry != original['geojson'] and not form.cleaned_data.get('perimetro_aceptado'):
            form.add_error('perimetro_aceptado', 'Acepta el nuevo perímetro antes de guardar.')
        if form.errors:
            pass
        else:
            if original is None:
                # Se serializan los registros de la misma persona: dos envíos simultáneos esperan su turno y el
                # segundo reconoce que el primero ya creó el predio, en vez de crear otro y repetir el aviso.
                get_user_model().objects.select_for_update().get(pk=request.user.pk)
                repeated = _recent_predio_duplicate(request.user, form.cleaned_data)
                if repeated is not None:
                    return redirect(f"{reverse('predio_detail', args=[repeated.pk])}?nuevo=1")
            predio = form.save(commit=False)
            predio.propietario = request.user
            predio.save()
            if original is None:
                PredioHistorial.objects.create(
                    predio=predio,
                    usuario=request.user,
                    accion=PredioHistorial.Accion.CREADO,
                    resumen=f'Predio creado con una superficie de {predio.superficie_ha} ha.',
                    superficie_nueva_ha=predio.superficie_ha,
                )
            else:
                geometry_changed = predio.geojson != original['geojson']
                area_changed = predio.superficie_ha != original['superficie_ha']
                metadata_changed = any(
                    getattr(predio, field) != original[field]
                    for field in ('nombre', 'descripcion', 'ubicacion', 'observaciones', 'zona_id')
                )
                metadata_labels = {
                    'nombre': 'Nombre',
                    'descripcion': 'Descripción',
                    'ubicacion': 'Ubicación',
                    'observaciones': 'Observaciones',
                    'zona_id': 'Zona',
                }
                metadata_changes = [
                    f'{metadata_labels[field]}: '
                    f'{original["zona"] if field == "zona_id" else (original[field] or "Sin dato")} → '
                    f'{(predio.zona.nombre if predio.zona_id else "Sin zona") if field == "zona_id" else (getattr(predio, field) or "Sin dato")}'
                    for field in metadata_labels
                    if getattr(predio, field) != original[field]
                ]
                if geometry_changed:
                    PredioHistorial.objects.create(
                        predio=predio,
                        usuario=request.user,
                        accion=PredioHistorial.Accion.PERIMETRO,
                        resumen=(
                            f'Perímetro actualizado; superficie: {original["superficie_ha"]} ha '
                            f'→ {predio.superficie_ha} ha.'
                        ),
                        superficie_anterior_ha=original['superficie_ha'],
                        superficie_nueva_ha=predio.superficie_ha,
                    )
                elif area_changed:
                    PredioHistorial.objects.create(
                        predio=predio,
                        usuario=request.user,
                        accion=PredioHistorial.Accion.SUPERFICIE,
                        resumen=(
                            f'Superficie actualizada de {original["superficie_ha"]} ha ' f'a {predio.superficie_ha} ha.'
                        ),
                        superficie_anterior_ha=original['superficie_ha'],
                        superficie_nueva_ha=predio.superficie_ha,
                    )
                if metadata_changed:
                    PredioHistorial.objects.create(
                        predio=predio,
                        usuario=request.user,
                        accion=PredioHistorial.Accion.ACTUALIZADO,
                        resumen='Se actualizaron los datos del predio: ' + '; '.join(metadata_changes) + '.',
                        superficie_anterior_ha=original['superficie_ha'] if area_changed else None,
                        superficie_nueva_ha=predio.superficie_ha if area_changed else None,
                    )
            if original is None:
                # Al registrar no se manda ningún aviso: la ficha del predio nuevo ya es la confirmación y trae
                # la pregunta siguiente («¿Cómo deseas organizar tu predio?»).
                return redirect(f"{reverse('predio_detail', args=[predio.pk])}?nuevo=1")
            messages.success(request, 'El predio quedó guardado correctamente.')
            return redirect('predio_detail', pk=predio.pk)
    return render_page(
        request,
        'terreno_form.html',
        'predios',
        {
            'journey': None,  # en un formulario se trabaja un solo paso: sin la ruta general
            'form': form,
            'heading': 'Editar predio' if pk else 'Registrar predio',
            'is_predio': True,
            'maps_config': map_config(),
            'farm_data': _without_geometry(farm_payload(request.user)),
            'registration_open': bool(request.method == 'POST' and form.errors),
        },
    )


@login_required
@require_POST
@transaction.atomic
def predio_usar_completo(request, pk):
    """Crea un único potrero con la misma geometría del predio, sin pedir dibujar otra vez."""
    predio = get_object_or_404(Predio.objects.select_for_update(), pk=pk, propietario=request.user)
    if predio.potreros.exists():
        # Segundo envío del mismo botón (doble clic): el primero ya creó el potrero, se continúa hacia él sin
        # repetir ningún aviso. Solo si el predio ya tenía potreros de antes se explica por qué no se creó otro.
        reciente = (
            predio.potreros.filter(creado__gte=timezone.now() - RESUBMIT_WINDOW).order_by('-creado', '-pk').first()
        )
        if reciente is not None and predio.potreros.count() == 1:
            return redirect('potrero_edit', predio_pk=predio.pk, pk=reciente.pk)
        messages.info(request, 'Este predio ya tiene potreros. Puedes agregar más desde esta ficha.')
        return redirect('predio_detail', pk=predio.pk)
    if not predio.geojson:
        messages.error(
            request, 'Este predio no tiene un perímetro dibujado. Usa «Dividir mi predio» para registrar sus potreros.'
        )
        return redirect('predio_detail', pk=predio.pk)
    name = ' '.join((request.POST.get('nombre') or '').split())[:150] or 'Potrero principal'
    potrero = Potrero(
        predio=predio,
        nombre=name,
        superficie_ha=predio.superficie_ha,
        latitud=predio.latitud,
        longitud=predio.longitud,
        perimetro=[list(point) for point in coordinates_from_geojson(predio.geojson)],
    )
    try:
        potrero.full_clean()
    except ValidationError as exc:
        messages.error(request, 'No se pudo crear el potrero: ' + ' '.join(exc.messages))
        return redirect('predio_detail', pk=predio.pk)
    potrero.save()
    PredioHistorial.objects.create(
        predio=predio,
        potrero=potrero,
        usuario=request.user,
        accion=PredioHistorial.Accion.POTRERO_CREADO,
        resumen=f'Se creó el potrero «{potrero.nombre}» usando todo el predio ({potrero.superficie_ha} ha).',
        superficie_nueva_ha=potrero.superficie_ha,
    )
    messages.success(request, f'Listo: «{potrero.nombre}» usa todo tu predio. Ahora indica qué cultivas aquí.')
    return redirect('potrero_edit', predio_pk=predio.pk, pk=potrero.pk)


def _recent_potrero_duplicate(predio, data):
    """Potrero de este predio recién creado con el mismo nombre y el mismo trazado (o la misma superficie)."""
    nombre = ' '.join((data.get('nombre') or '').split())
    if not nombre:
        return None
    try:
        perimetro = json.loads(data.get('perimetro') or '[]')
    except ValueError:
        perimetro = None
    try:
        superficie = Decimal(str(data.get('superficie_ha') or '').replace(',', '.'))
    except InvalidOperation:
        superficie = None
    candidates = predio.potreros.filter(
        nombre__iexact=nombre, creado__gte=timezone.now() - RESUBMIT_WINDOW
    ).order_by('-creado', '-pk')
    for candidate in candidates:
        if perimetro:
            if _same_json(candidate.perimetro, perimetro):
                return candidate
        elif not candidate.perimetro and superficie is not None and candidate.superficie_ha == superficie:
            return candidate
    return None


@login_required
@transaction.atomic
def potrero_form(request, predio_pk, pk=None):
    predio = get_object_or_404(Predio.objects.select_for_update(), pk=predio_pk, propietario=request.user)
    instance = get_object_or_404(Potrero, pk=pk, predio=predio) if pk else Potrero(predio=predio)
    used_area_query = predio.potreros.all()
    if instance.pk:
        used_area_query = used_area_query.exclude(pk=instance.pk)
    used_area = used_area_query.aggregate(total=Sum('superficie_ha'))['total'] or Decimal('0')
    available_area = max(Decimal('0'), predio.superficie_ha - used_area)
    original = None
    if instance.pk:
        original = {
            'nombre': instance.nombre,
            'superficie_ha': instance.superficie_ha,
            'geojson': instance.geojson,
            'cultivo': instance.cultivo.nombre if instance.cultivo_id else 'Sin cultivo',
            'variedad': instance.variedad,
            'fecha_siembra': instance.fecha_siembra,
            'fecha_cosecha': instance.fecha_cosecha,
            'latitud': instance.latitud,
            'longitud': instance.longitud,
        }
    if request.method == 'POST' and not instance.pk:
        repeated = _recent_potrero_duplicate(predio, request.POST)
        if repeated is not None:
            # El mismo formulario llegó dos veces (doble clic): el primer envío ya creó el potrero. No se crea otro
            # ni se repite el aviso; se continúa a la ficha del predio.
            return redirect('predio_detail', pk=predio.pk)
    # Este formulario solo trata el potrero (nombre, cultivo opcional, superficie y ubicación). La variedad y las
    # fechas se registran en la planificación de la temporada; aquí no se muestran ni se modifican.
    form = PotreroForm(request.POST or None, instance=instance)
    min_area = Decimal('0.0001')
    # Sin hectáreas libres no se pone «max»: un máximo menor que el mínimo hace que el navegador rechace todo en silencio.
    sin_espacio = available_area < min_area
    if not sin_espacio:
        form.fields['superficie_ha'].widget.attrs['max'] = format(available_area, 'f')
    form.fields['superficie_ha'].help_text = (
        f'Superficie disponible en este predio: {available_area} ha. '
        'Si dibujas el perímetro, la superficie se calculará automáticamente.'
    )
    previous_crop = (
        {
            'cultivo_id': instance.cultivo_id,
            'cultivo': instance.cultivo if instance.cultivo_id else None,
            'variedad': instance.variedad,
            'fecha_siembra': instance.fecha_siembra,
            'fecha_cosecha': instance.fecha_cosecha,
        }
        if instance.pk
        else None
    )
    if request.method == 'POST' and form.is_valid():
        potrero = form.save()
        if previous_crop and potrero.cultivo_id and potrero.cultivo_id != previous_crop['cultivo_id']:
            # Cultivo distinto: la variedad y las fechas guardadas eran del cultivo anterior (quedan en su temporada).
            # La temporada nueva nace sin ellas y se completan en «Completar planificación».
            potrero.variedad, potrero.fecha_siembra, potrero.fecha_cosecha = '', None, None
            potrero.save(update_fields=['variedad', 'fecha_siembra', 'fecha_cosecha', 'actualizado'])
        # Cambio de cultivo: nace una temporada nueva (planificada, por completar). Con el mismo cultivo no se toca
        # la temporada: sus datos solo se editan en el formulario de planificación, así no hay dos versiones.
        record_form_crop_change(potrero, previous_crop)
        if original is None:
            PredioHistorial.objects.create(
                predio=predio,
                potrero=potrero,
                usuario=request.user,
                accion=PredioHistorial.Accion.POTRERO_CREADO,
                resumen=f'Se creó el potrero «{potrero.nombre}» con {potrero.superficie_ha} ha.',
                superficie_nueva_ha=potrero.superficie_ha,
            )
        else:
            current = {
                'nombre': potrero.nombre,
                'superficie_ha': potrero.superficie_ha,
                'geojson': potrero.geojson,
                'cultivo': potrero.cultivo.nombre if potrero.cultivo_id else 'Sin cultivo',
                'variedad': potrero.variedad,
                'fecha_siembra': potrero.fecha_siembra,
                'fecha_cosecha': potrero.fecha_cosecha,
                'latitud': potrero.latitud,
                'longitud': potrero.longitud,
            }
            labels = {
                'nombre': 'Nombre',
                'superficie_ha': 'Superficie (ha)',
                'cultivo': 'Cultivo',
                'variedad': 'Variedad',
                'fecha_siembra': 'Fecha de siembra',
                'fecha_cosecha': 'Fecha de cosecha',
                'latitud': 'Latitud',
                'longitud': 'Longitud',
            }
            changes = [
                f'{labels[field]}: {original[field] or "Sin dato"} → {current[field] or "Sin dato"}'
                for field in labels
                if original[field] != current[field]
            ]
            geometry_changed = original['geojson'] != current['geojson']
            if geometry_changed:
                changes.append('Perímetro actualizado')
            if changes:
                PredioHistorial.objects.create(
                    predio=predio,
                    potrero=potrero,
                    usuario=request.user,
                    accion=PredioHistorial.Accion.POTRERO_ACTUALIZADO,
                    resumen=f'Se modificó el potrero «{original["nombre"]}»: ' + '; '.join(changes) + '.',
                    superficie_anterior_ha=original['superficie_ha'],
                    superficie_nueva_ha=current['superficie_ha'],
                )
        messages.success(
            request,
            (
                ('Potrero creado.' if original is None else 'Cambios del potrero guardados.')
                + (
                    ' Completa la planificación del cultivo cuando quieras.'
                    if potrero.cultivo_id and not planning_state(potrero)['complete']
                    else ''
                )
            ),
        )
        return redirect('predio_detail', pk=predio.pk)
    return render_page(
        request,
        'terreno_form.html',
        'predios',
        {
            'journey': None,  # en un formulario se trabaja un solo paso: sin la ruta general
            'form': form,
            'heading': 'Editar potrero' if pk else 'Registrar potrero',
            'predio': predio,
            'is_potrero': True,
            'maps_config': map_config(),
            'potrero_superficie_disponible': available_area,
            'potrero_sin_espacio': sin_espacio and not instance.pk,
        },
    )


@login_required
@transaction.atomic
def temporada_form(request, predio_pk, potrero_pk, pk=None):
    # Solo se bloquean filas al GUARDAR (POST): abrir el formulario (GET) es una lectura y no necesita bloqueo.
    # El bloqueo se limita a la fila propia con of=('self',): «cultivo» es opcional (LEFT JOIN) y PostgreSQL
    # rechaza un FOR UPDATE que alcance el lado opcional de la unión
    # («FOR UPDATE cannot be applied to the nullable side of an outer join»).
    saving = request.method == 'POST'

    def rows(model):
        return model.objects.select_for_update(of=('self',)) if saving else model.objects

    predio = get_object_or_404(rows(Predio), pk=predio_pk, propietario=request.user)
    potrero = get_object_or_404(rows(Potrero).select_related('cultivo'), pk=potrero_pk, predio=predio)
    instance = (
        get_object_or_404(rows(TemporadaCultivo), pk=pk, potrero=potrero)
        if pk
        else TemporadaCultivo(potrero=potrero)
    )
    # Donde la temporada aún no tiene un dato y el potrero sí (potreros antiguos), el formulario lo propone.
    form = TemporadaCultivoForm(
        request.POST or None, instance=instance, initial=season_form_initial(potrero, instance if pk else None)
    )
    if request.method == 'POST' and form.is_valid():
        result = save_season_form(potrero, request.user, form.instance)
        text = 'La temporada quedó guardada. Las temporadas anteriores se conservan.'
        if result['current']:
            saved_plot = Potrero.objects.prefetch_related('temporadas').get(pk=potrero.pk)
            if planning_state(saved_plot)['complete']:
                text += ' La planificación quedó completa: ya puedes calcular semillas.'
        messages.success(request, text)
        return redirect('predio_detail', pk=predio.pk)
    latest = vigente_season(potrero)
    return render_page(
        request,
        'temporada_form.html',
        'predios',
        {
            'journey': None,
            'form': form,
            'predio': predio,
            'potrero': potrero,
            'heading': _season_heading(potrero, instance if pk else None, latest),
            # Editar una temporada anterior no cambia los datos vigentes del potrero: se avisa en el formulario.
            'is_past_season': bool(pk and latest is not None and latest.pk != instance.pk),
        },
    )


def _season_heading(potrero, season, latest):
    """Título del formulario según lo que la persona viene a hacer (no siempre «editar»)."""
    if season is None:
        return 'Planificar nueva temporada' if latest is not None else 'Completar planificación'
    if latest is not None and latest.pk != season.pk:
        return 'Corregir temporada anterior'
    return 'Editar planificación' if planning_state(potrero)['complete'] else 'Completar planificación'


def _json_body(request):
    if request.content_type != 'application/json':
        raise ValueError('Envía los datos como JSON.')
    if len(request.body) > 16384:
        raise ValueError('La solicitud supera el tamaño permitido.')
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        raise ValueError('El cuerpo JSON no se pudo interpretar.')
    if not isinstance(payload, dict):
        raise ValueError('Los datos deben ser un objeto JSON.')
    return payload


def _optional_date(value, label):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f'{label} no es una fecha válida.')


def _validation_messages(exc):
    if hasattr(exc, 'message_dict'):
        return ' '.join(message for messages_list in exc.message_dict.values() for message in messages_list)
    return ' '.join(exc.messages)


def _plot_response(plot):
    plot = (
        Potrero.objects.select_related('predio__zona', 'cultivo')
        .prefetch_related(
            'temporadas__cultivo',
        )
        .get(pk=plot.pk)
    )
    data = next(item for item in farm_payload(plot.predio.propietario)['plots'] if item['id'] == str(plot.pk))
    data['history'] = [
        {
            'created': timezone.localtime(item.creado).strftime('%d/%m/%Y %H:%M'),
            'action': item.get_accion_display(),
            'summary': item.resumen,
        }
        for item in plot.historial.order_by('-creado', '-pk')[:20]
    ]
    return data


@login_required
def potrero_api(request, pk):
    plot = get_object_or_404(Potrero, pk=pk, predio__propietario=request.user)
    response = JsonResponse({'ok': True, 'plot': _plot_response(plot)})
    add_never_cache_headers(response)
    return response


@login_required
@require_POST
def potrero_assign_crop(request, pk):
    plot = get_object_or_404(
        Potrero.objects.select_related('predio', 'cultivo'),
        pk=pk,
        predio__propietario=request.user,
    )
    try:
        payload = _json_body(request)
        crop_id = payload.get('crop_id')
        if not str(crop_id or '').isdigit():
            raise ValueError('Selecciona un cultivo del catálogo.')
        crop = Cultivo.objects.filter(pk=crop_id, activo=True).first()
        if crop is None:
            raise ValueError('El cultivo seleccionado no existe o no está activo en el catálogo.')
        variety = str(payload.get('variety') or '').strip()
        if len(variety) > 150:
            raise ValueError('La variedad supera los 150 caracteres.')
        notes = str(payload.get('notes') or '').strip()
        if len(notes) > 2000:
            raise ValueError('Las observaciones superan los 2000 caracteres.')
        status = payload.get('status') or TemporadaCultivo.Estado.PLANIFICADA
        if status not in TemporadaCultivo.Estado.values:
            raise ValueError('Selecciona un estado válido.')
        sowing = _optional_date(payload.get('sowing_date'), 'La fecha de siembra')
        harvest = _optional_date(payload.get('harvest_date'), 'La fecha prevista de cosecha')
        season_label = str(payload.get('season') or '').strip()
        mode = payload.get('mode') or 'new'
        if mode not in {'new', 'update_dates'}:
            raise ValueError('La acción solicitada no está disponible.')
        if mode == 'update_dates':
            season_id = payload.get('season_id')
            season = (
                TemporadaCultivo.objects.filter(pk=season_id, potrero=plot).select_related('cultivo').first()
                if str(season_id or '').isdigit()
                else None
            )
            if season is None:
                raise ValueError('La temporada seleccionada no pertenece a este potrero.')
            if season.cultivo_id != crop.pk:
                raise ValueError(
                    'Para cambiar de cultivo registra una temporada nueva; '
                    'la temporada existente conserva su cultivo original.'
                )
            update_current_dates(plot, request.user, season, sowing_date=sowing, harvest_date=harvest, status=status)
            message = 'Fechas registradas en la temporada existente. El historial conserva el cambio.'
        else:
            assign_crop(
                plot,
                request.user,
                crop=crop,
                variety=variety,
                season_label=season_label,
                sowing_date=sowing,
                harvest_date=harvest,
                status=status,
                notes=notes,
                close_previous=bool(payload.get('close_previous', True)),
            )
            message = 'Cultivo asignado. Las temporadas anteriores se conservan en el historial.'
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': _validation_messages(exc)}, status=400)
    return JsonResponse({'ok': True, 'message': message, 'plot': _plot_response(plot)})


def mapa_prueba(request):
    """Laboratorio temporal de la migración a Google Maps (fase 0): solo administradores, sin Leaflet."""
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.is_staff:
        raise PermissionDenied
    return render_page(request, 'mapa_prueba.html', 'mapa_prueba', {'maps_config': map_config()})
