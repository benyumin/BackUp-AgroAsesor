"""Catálogo agrícola (productos e insumos) y búsqueda general.
"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.text import slugify

from ..catalog import (
    CATEGORY_LABELS,
    catalog_payload,
    category_key,
)
from ..models import (
    Cultivo,
    Insumo,
    ProblemaAgricola,
)
from ..pdf import respuesta_pdf
from ..pdf.informes import informe_producto
from ..search import run_search
from .base import render_page
from .contexto_predios import farm_payload


@login_required
def busqueda(request):
    """Buscar: una sola búsqueda en el servidor (también atiende al buscador de la barra superior).

    Reutiliza el catálogo y los predios del usuario que la página ya necesita (``catalog_payload`` y
    ``farm_payload``), así que no suma consultas y nunca muestra predios de otras cuentas.
    """
    catalog = catalog_payload()
    farm_data = farm_payload(request.user)
    context = run_search(catalog, farm_data, request.GET)
    context.update(catalog=catalog, farm_data=farm_data)
    return render_page(request, 'busqueda.html', 'busqueda', context)


# ---------------------------------------------------------------------------
# Consultar productos: catálogo navegable y ficha técnica imprimible
# ---------------------------------------------------------------------------
PRODUCT_STATUS_LABELS = dict(Insumo.ESTADO_VERIFICACION)


ACTIVITY_TYPE_BY_CATEGORY = {
    'seed': 'siembra',
    'fertilizer': 'fertilizacion',
    'herbicide': 'control_fitosanitario',
    'fungicide': 'control_fitosanitario',
    'insecticide': 'control_fitosanitario',
    'other': 'fertilizacion',
}


def _product_queryset():
    return (
        Insumo.objects.select_related('tipo', 'cultivo', 'zona').prefetch_related('problemas').order_by('nombre', 'pk')
    )


def _decorate_product(product):
    product.category = category_key(product.tipo.nombre)
    product.category_label = CATEGORY_LABELS[product.category]
    product.status_label = PRODUCT_STATUS_LABELS.get(product.estado_verificacion, 'Información no verificada')
    product.is_phyto = product.es_fitosanitario
    product.is_current = product.vigente_en
    product.calculable = (
        product.is_current
        and product.estado_verificacion not in {'restricted', 'cancelled'}
        and (product.uso_verificado if product.is_phyto else product.dosis_con_fuente)
    )
    return product


@login_required
def productos(request):
    products = [_decorate_product(item) for item in _product_queryset()]
    counts = {key: 0 for key in CATEGORY_LABELS}
    for item in products:
        counts[item.category] += 1
    filters = {
        'q': request.GET.get('q', '').strip(),
        'categoria': request.GET.get('categoria', ''),
        'cultivo': request.GET.get('cultivo', ''),
        'estado': request.GET.get('estado', ''),
        'problema': request.GET.get('problema', ''),
        'solo_calculables': request.GET.get('solo_calculables') == '1',
        'orden': request.GET.get('orden', 'nombre'),
    }
    result = products
    if filters['q']:
        needle = filters['q'].casefold()
        result = [
            item
            for item in result
            if any(
                needle in (value or '').casefold()
                for value in (
                    item.nombre,
                    item.fabricante,
                    item.ingrediente_activo,
                    item.composicion_nutricional,
                    item.variedad_semilla,
                    item.numero_autorizacion_sag,
                    item.descripcion,
                )
            )
        ]
    if filters['categoria'] in CATEGORY_LABELS:
        result = [item for item in result if item.category == filters['categoria']]
    if filters['cultivo'].isdigit():
        result = [item for item in result if item.cultivo_id is None or str(item.cultivo_id) == filters['cultivo']]
    if filters['estado'] in PRODUCT_STATUS_LABELS:
        result = [item for item in result if item.estado_verificacion == filters['estado']]
    if filters['problema'].isdigit():
        result = [
            item for item in result if any(str(problem.pk) == filters['problema'] for problem in item.problemas.all())
        ]
    if filters['solo_calculables']:
        result = [item for item in result if item.calculable]
    if filters['orden'] == 'categoria':
        result.sort(key=lambda item: (item.category_label, item.nombre.casefold()))
    elif filters['orden'] == 'verificacion':
        result.sort(key=lambda item: (item.estado_verificacion != 'verified', item.nombre.casefold()))
    page = Paginator(result, 18).get_page(request.GET.get('pagina'))
    query = request.GET.copy()
    query.pop('pagina', None)
    return render_page(
        request,
        'productos.html',
        'productos',
        {
            'products_page': page,
            'product_total': len(products),
            'product_count': len(result),
            'product_counts': [(key, CATEGORY_LABELS[key], counts[key]) for key in CATEGORY_LABELS],
            'product_filters': filters,
            'product_query': query.urlencode(),
            'product_crops': Cultivo.objects.filter(activo=True).order_by('nombre'),
            'product_problems': ProblemaAgricola.objects.filter(activo=True).order_by('nombre'),
            'product_statuses': Insumo.ESTADO_VERIFICACION,
            'category_labels': CATEGORY_LABELS,
        },
    )


def _datos_faltantes(product):
    """Datos que le faltan al producto para considerarse verificado (se muestran en la ficha y en el PDF)."""
    missing = []
    if product.estado_verificacion != 'verified':
        missing.append('verificación del producto con fuente, URL y fecha')
    if not product.dosis_con_fuente:
        missing.append('dosis por hectárea verificada con fuente')
    if product.is_phyto:
        if not product.numero_autorizacion_sag:
            missing.append('número de autorización SAG')
        if not product.etiqueta_url:
            missing.append('enlace a la etiqueta autorizada')
        if not product.cultivos_autorizados:
            missing.append('cultivos autorizados en etiqueta')
        if not product.objetivos_control:
            missing.append('objetivos de control')
    return missing


@login_required
def producto_pdf(request, pk):
    """Ficha técnica del producto en PDF."""
    product = _decorate_product(get_object_or_404(_product_queryset(), pk=pk))
    contenido = informe_producto(
        product, _datos_faltantes(product), autor=request.user.get_full_name()
    )
    return respuesta_pdf(contenido, f'agroasesor-ficha-{slugify(product.nombre)}.pdf')


@login_required
def producto_detail(request, pk):
    product = _decorate_product(get_object_or_404(_product_queryset(), pk=pk))
    related = [
        _decorate_product(item)
        for item in _product_queryset()
        .exclude(pk=product.pk)
        .filter(Q(cultivo=product.cultivo) if product.cultivo_id else Q(tipo=product.tipo))[:6]
    ]
    missing = _datos_faltantes(product)
    params = {'insumo': product.pk}
    if product.cultivo_id:
        params['crop'] = product.cultivo_id
    plan_params = {'tipo': ACTIVITY_TYPE_BY_CATEGORY[product.category], 'insumo': product.pk}
    if product.calculable and product.dosis_hectarea:
        plan_params.update({'dosis': product.dosis_hectarea, 'unidad': product.unidad_dosis})
    return render_page(
        request,
        'producto_detail.html',
        'productos',
        {
            'product': product,
            'related_products': related,
            'missing_data': missing,
            'calc_url': reverse('decisiones')
            + '?'
            + urlencode(
                {
                    'herramienta': 'llevar',
                    'tipo': 'semillas' if product.category == 'seed' else 'insumos',
                    **params,
                }
            ),
            'availability_url': reverse('decisiones') + '?' + urlencode({'herramienta': 'alcanza', **params}),
            'plan_url': reverse('actividad_create') + '?' + urlencode(plan_params),
        },
    )
