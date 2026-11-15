"""Piezas comunes a todas las pantallas: menú lateral, ruta guiada del inicio y armado de la página.

``render_page`` es el único lugar que arma el contexto común (menú, usuario, datos del mapa, ruta guiada) y
renderiza una plantilla; todas las vistas que muestran una página completa terminan llamándolo.
"""

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers

from ..ayudas import claves_ocultas
from ..catalog import (
    catalog_payload,
    crop_picker_data,
)
from ..models import (
    ActividadAgricola,
    Agricultor,
    CalculoAgricola,
    Potrero,
    Predio,
)
from ..seasons import (
    planning_state,
)
from .contexto_predios import EN_POTREROS_VIGENTES, decision_url, farm_payload, plan_form_url


def build_menu(request, current):
    """Menú lateral agrupado por propósito: mi campo, trabajo del campo, herramientas e informes.

    Cada pantalla aparece una sola vez. Se quitaron tres entradas que repetían otra: «Mapa» (es la misma página
    «Mis predios»), «Toma de decisiones» (es la misma página de las calculadoras) y «Buscar» (ya está el buscador
    de la barra superior). Las pantallas y sus direcciones siguen existiendo.
    """
    tool = request.GET.get('herramienta', 'llevar') if current == 'decisiones' else ''
    kind = request.GET.get('tipo', '')
    calculators = [
        {
            'label': 'Semillas',
            'icon': 'wheat',
            'href': reverse('decisiones') + '?herramienta=llevar&tipo=semillas',
            'active': current == 'decisiones' and tool == 'llevar' and kind != 'insumos',
        },
        {
            'label': 'Fertilizantes',
            'icon': 'flask-conical',
            'href': reverse('decisiones') + '?herramienta=llevar&tipo=insumos',
            'active': current == 'decisiones' and tool == 'llevar' and kind == 'insumos',
        },
        {
            'label': '¿Me alcanza?',
            'icon': 'scale',
            'href': reverse('decisiones') + '?herramienta=alcanza',
            'active': current == 'decisiones' and tool == 'alcanza',
        },
    ]
    in_calculators = current == 'decisiones'
    items = [
        {'section': 'Mi terreno'},
        {'label': 'Inicio', 'icon': 'house', 'href': reverse('dashboard'), 'active': current == 'dashboard'},
        {'label': 'Mis predios', 'icon': 'map-pinned', 'href': reverse('predios'), 'active': current == 'predios'},
        {'section': 'Trabajo del campo'},
        {
            'label': 'Actividades',
            'icon': 'clipboard-list',
            'href': reverse('actividad_list'),
            'active': current == 'actividades',
        },
        {
            'label': 'Calendario',
            'icon': 'calendar-days',
            'href': reverse('calendario'),
            'active': current == 'calendario',
        },
        {'section': 'Herramientas'},
        {
            'label': 'Calculadoras',
            'icon': 'calculator',
            'children': calculators,
            'open': in_calculators,
            'active': False,  # se marca la calculadora abierta, no el grupo
        },
        {
            'label': 'Catálogo agrícola',
            'icon': 'library',
            'href': reverse('productos'),
            'active': current == 'productos',
        },
        {'section': 'Informes'},
        {'label': 'Reportes', 'icon': 'file-text', 'href': reverse('reportes'), 'active': current == 'reportes'},
    ]
    if request.user.is_staff:
        items.append(
            {
                'label': 'Administración',
                'icon': 'shield-check',
                'href': reverse('admin_panel'),
                'active': current == 'admin_panel',
            }
        )
    return items


# El Centro de decisiones y las calculadoras no llevan la ruta de seis pasos: arrancan directo en la herramienta.
JOURNEY_PAGES = {'dashboard', 'predios', 'calendario', 'reportes'}


JOURNEY_HERE = {
    'predios': {'predio', 'potreros', 'cultivo'},
    'calendario': {'siembra'},
    'reportes': {'reporte'},
}


JOURNEY_NOTES = {
    'calendario': 'Después de elegir cultivo, completa la fecha de siembra del potrero.',
    'reportes': 'Los cálculos del Centro de decisiones quedan guardados y aparecen aquí.',
}


def journey_payload(user, current):
    """Ruta de trabajo del usuario: qué ya hizo y cuál es el siguiente paso (solo lectura, sin datos nuevos)."""
    farms = list(Predio.objects.filter(propietario=user).order_by('pk'))
    plots = list(
        Potrero.objects.filter(predio__propietario=user)
        .select_related('cultivo', 'predio')
        .prefetch_related('temporadas')
        .order_by('pk')
    )
    states = {plot.pk: planning_state(plot) for plot in plots}
    has_calculation = CalculoAgricola.objects.filter(usuario=user).exists()
    farm_ids_with_plots = {plot.predio_id for plot in plots}
    farm_without_plots = next((farm for farm in farms if farm.pk not in farm_ids_with_plots), None)
    plot_without_crop = next((plot for plot in plots if not states[plot.pk]['has_crop']), None)
    plot_without_sowing = next(
        (plot for plot in plots if states[plot.pk]['has_crop'] and not states[plot.pk]['steps'][1]), None
    )
    plot_with_crop = next((plot for plot in plots if states[plot.pk]['has_crop']), None)

    def action(label, url, primary=True, post=False):
        return {'label': label, 'url': url, 'primary': primary, 'post': post}

    # Estado 5: con cultivo y siembra planificada ya se pueden usar las herramientas de cálculo.
    tools_actions = [
        action('Calcular semillas', decision_url('semillas', plot_with_crop)),
        action('Calcular insumos', decision_url('insumos', plot_with_crop), primary=False),
        action('Ver si me alcanza', decision_url('alcanza', plot_with_crop), primary=False),
        action('Consultar ficha de producto', reverse('productos'), primary=False),
    ]
    split_url = farm_without_plots and reverse('potrero_create', kwargs={'predio_pk': farm_without_plots.pk})
    potrero_actions = []
    if farm_without_plots:
        if farm_without_plots.geojson:
            potrero_actions.append(
                action(
                    'Utilizar todo el predio',
                    reverse('predio_usar_completo', kwargs={'pk': farm_without_plots.pk}),
                    post=True,
                )
            )
        potrero_actions.append(action('Dividir en potreros', split_url, primary=not farm_without_plots.geojson))
    steps = [
        {
            'key': 'predio',
            'label': 'Predio',
            'done': bool(farms),
            'url': reverse('predios'),
            'next': {
                'status': '',
                'title': 'Comienza registrando tu predio',
                'text': 'Dibuja su perímetro en el mapa; después defines cómo trabajarlo.',
                'actions': [action('Registrar predio', reverse('predio_create'))],
            },
        },
        {
            'key': 'potreros',
            'label': 'Potreros',
            'done': bool(plots) and not farm_without_plots,
            'url': reverse('predios'),
            'next': farm_without_plots
            and {
                'status': 'Tu predio ya está registrado',
                'title': '¿Cómo quieres trabajar este terreno?',
                'text': f'«{farm_without_plots.nombre}»: los cálculos y la siembra se hacen por potrero: usa todo el predio como uno solo o divídelo en sectores.',
                'actions': potrero_actions,
            },
        },
        {
            'key': 'cultivo',
            'label': 'Cultivo',
            'done': bool(plots) and not plot_without_crop,
            'url': reverse('predios'),
            'next': plot_without_crop
            and {
                'status': 'Potrero listo',
                'title': 'Selecciona qué quieres plantar',
                'text': f'«{plot_without_crop.nombre}»: con el cultivo podrás planificar la siembra y usar las herramientas de cálculo.',
                'actions': [
                    action(
                        'Asignar cultivo',
                        reverse(
                            'potrero_edit',
                            kwargs={'predio_pk': plot_without_crop.predio_id, 'pk': plot_without_crop.pk},
                        ),
                    )
                ],
            },
        },
        {
            'key': 'siembra',
            'label': 'Siembra',
            'done': bool(plot_with_crop) and not plot_without_sowing,
            'url': reverse('calendario'),
            'next': plot_without_sowing
            and {
                'status': 'Cultivo seleccionado',
                'title': 'Planifica la siembra',
                'text': f'{plot_without_sowing.cultivo.nombre} en «{plot_without_sowing.nombre}»: falta la fecha de siembra para dejar el potrero planificado.',
                'actions': [
                    action(
                        'Completar planificación',
                        plan_form_url(plot_without_sowing, states[plot_without_sowing.pk]),
                    )
                ],
            },
        },
        {
            'key': 'decisiones',
            'label': 'Decisiones',
            'done': has_calculation,
            'url': reverse('decisiones'),
            'next': {
                'status': 'Ya puedes utilizar las herramientas agrícolas' if plot_with_crop else '',
                'title': 'Calcula cuánto llevar o si te alcanza',
                'text': (
                    'Elige una herramienta; el potrero y el cultivo ya van cargados.'
                    if plot_with_crop
                    else 'Semillas, fertilizantes u otros insumos para tu potrero.'
                ),
                'actions': (
                    tools_actions
                    if plot_with_crop
                    else [action('Ir al Centro de decisiones', decision_url('semillas'))]
                ),
            },
        },
        {
            'key': 'reporte',
            'label': 'Reporte',
            'done': False,
            'url': reverse('reportes'),
            'next': {
                'status': '',
                'title': 'Revisa tus reportes y el historial',
                'text': 'Resumen del predio, cuaderno de campo y cálculos guardados.',
                'actions': [action('Ver reportes', reverse('reportes'))],
            },
        },
    ]
    # Un paso está hecho cuando no queda nada pendiente en él (todos los predios tienen potreros, todos los potreros
    # tienen cultivo y fecha de siembra). El paso actual es el primero sin completar con una acción concreta por hacer.
    current_key = None
    for step in steps:
        if step['key'] == 'reporte' or (not step['done'] and step['next']):
            current_key = step['key']
            break
    here = JOURNEY_HERE.get(current, set())
    items, current_step = [], None
    for step in steps:
        state = 'current' if step['key'] == current_key else ('done' if step['done'] else 'todo')
        items.append(
            {
                'key': step['key'],
                'label': step['label'],
                'url': step['url'],
                'state': state,
                'here': step['key'] in here,
            }
        )
        if step['key'] == current_key:
            current_step = dict(
                step['next'], cta=step['next']['actions'][0]['label'], url=step['next']['actions'][0]['url']
            )
    return {
        'steps': items,
        'next': current_step,
        'note': JOURNEY_NOTES.get(current, ''),
        'done_count': sum(1 for step in steps if step['done']),
        'total': len(steps) - 1,
    }


def render_page(request, template, current, extra=None):
    """Renderiza ``template`` con el contexto común de AgroAsesor.

    ``current`` es la clave de la pantalla activa en el menú (por ejemplo ``'predios'``) y ``extra`` el contexto
    propio de la vista. Sin sesión redirige al inicio de sesión; el panel de administración exige ser personal.
    """
    if not request.user.is_authenticated:
        return redirect('login')
    if current == 'admin_panel' and not request.user.is_staff:
        raise PermissionDenied
    extra = extra or {}
    name = request.user.get_full_name() or request.user.username
    context = {
        'user': {
            'name': name,
            'email': request.user.email,
            'role': 'Administrador' if request.user.is_staff else 'Agricultor',
        },
        'first_name': name.split()[0],
        'initials': ''.join(p[0] for p in name.split()[:2]).upper(),
        'menu': build_menu(request, current),
        'current': current,
        'query': request.GET.get('q', ''),
        'catalog': extra.get('catalog') or catalog_payload(),
        'farm_data': extra.get('farm_data') or farm_payload(request.user),
        'ayudas_ocultas': claves_ocultas(request.user),
        'pending_activities': ActividadAgricola.objects.filter(
            EN_POTREROS_VIGENTES,
            usuario=request.user,
            estado=ActividadAgricola.Estado.PENDIENTE,
        )
        .select_related('potrero', 'potrero__predio')
        .order_by('fecha_programada', 'pk')[:5],
        'phase_a': current
        in {
            'dashboard',
            'predios',
            'calendario',
            'admin_panel',
            'actividades',
            'decisiones',
            'productos',
            'reportes',
            'busqueda',
            'perfil',
        },
        # Cuentas creadas antes de pedir teléfono y comuna: se invita a completar el perfil (sin bloquear nada).
        'perfil_incompleto': (
            current != 'perfil'
            and not request.user.is_staff
            and not Agricultor.objects.filter(user=request.user).exists()
        ),
    }
    if current in JOURNEY_PAGES:
        context['journey'] = journey_payload(request.user, current)
    if current == 'decisiones':
        # Cultivos frecuentes y categorías para elegir cultivo en el Centro de decisiones.
        context['crop_picker'] = crop_picker_data(request.user)
    context.update(extra)
    response = render(request, template, context)
    add_never_cache_headers(response)
    return response
