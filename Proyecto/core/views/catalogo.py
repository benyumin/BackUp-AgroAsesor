"""Catálogo agrícola (productos e insumos) y búsqueda general.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Case, Count, Q, Value, When
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from ..calculos import productos_vigentes
from ..catalog import (
    CATEGORY_LABELS,
    catalog_payload,
    category_key,
    importados_de_lista_sag,
    insumo_payload,
)
from ..models import (
    Cultivo,
    Insumo,
    RegistroListaSAG,
    TipoInsumo,
)
from ..sag import URL_LISTA_SAG
from ..pdf import respuesta_pdf
from ..pdf.informes import informe_producto
from ..search import run_search
from .base import render_page
from .contexto_predios import farm_payload


SEARCH_SAG_LIMIT = 15


def _sag_matches(query):
    """Productos importados del SAG que coinciden con lo buscado (no vienen en ``catalog_payload``). Una consulta."""
    words = ' '.join(str(query).split())[:80]
    if len(words) < 2:
        return []
    matches = (
        Insumo.objects.filter(importados_de_lista_sag())
        .filter(
            Q(nombre__icontains=words)
            | Q(fabricante__icontains=words)
            | Q(composicion_nutricional__icontains=words)
            | Q(ingrediente_activo__icontains=words)
            | Q(numero_autorizacion_sag=words)
        )
        .select_related('tipo', 'cultivo', 'zona', 'registro_sag')
        .order_by(Lower('nombre'), 'pk')[:SEARCH_SAG_LIMIT]
    )
    return [insumo_payload(item) for item in matches]


@login_required
def busqueda(request):
    """Buscar: una sola búsqueda en el servidor (también atiende al buscador de la barra superior).

    Reutiliza el catálogo y los predios del usuario que la página ya necesita (``catalog_payload`` y
    ``farm_payload``), así que no suma consultas y nunca muestra predios de otras cuentas.
    """
    catalog = catalog_payload()
    catalog['insumos'] = catalog['insumos'] + _sag_matches(request.GET.get('q', ''))
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
    'biostimulant': 'fertilizacion',
    'herbicide': 'control_fitosanitario',
    'fungicide': 'control_fitosanitario',
    'insecticide': 'control_fitosanitario',
    'other': 'fertilizacion',
}


SAG_FILTERS = {
    'incluidos': 'Incluidos en la lista SAG orgánica',
    'no_incluidos': 'Sin registro en la lista SAG',
}
SAG_CLASSIFICATIONS = dict(RegistroListaSAG.Clasificacion.choices)
PRODUCTS_PER_PAGE = 18


def _product_queryset():
    return Insumo.objects.select_related('tipo', 'cultivo', 'zona', 'registro_sag').order_by('nombre', 'pk')


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
    try:
        product.sag = product.registro_sag
    except ObjectDoesNotExist:
        product.sag = None
    # La lista SAG no trae N-P-K: en sus productos no se muestran filas vacías de N, P₂O₅ y K₂O.
    npk = (product.nitrogeno_pct, product.fosforo_p2o5_pct, product.potasio_k2o_pct)
    product.show_npk = product.category == 'fertilizer' and not (
        product.sag and all(value is None for value in npk)
    )
    return product


def _types_by_category():
    """Nombres de TipoInsumo agrupados por clave de categoría (tabla maestra pequeña: una consulta)."""
    grouped = {key: [] for key in CATEGORY_LABELS}
    for name in TipoInsumo.objects.values_list('nombre', flat=True):
        grouped[category_key(name)].append(name)
    return grouped


def _calculable_candidates():
    """Condición en la base de datos que cumple todo producto «calculable» (dosis con fuente y vigente).

    Es un superconjunto: el uso verificado de un fitosanitario (cultivo dentro de la lista autorizada) se revisa
    después en Python, solo sobre estos pocos productos.
    """
    today = timezone.localdate()
    return (
        Q(vigente=True, estado_verificacion='verified', dosis_verificada=True, dosis_hectarea__gt=0)
        & ~Q(dosis_fuente='')
        & ~Q(fuente_url='')
        & Q(fecha_verificacion__isnull=False)
        & (Q(vigencia_desde__isnull=True) | Q(vigencia_desde__lte=today))
        & (Q(vigencia_hasta__isnull=True) | Q(vigencia_hasta__gte=today))
    )


@login_required
def productos(request):
    """Catálogo paginado. Filtra, ordena y cuenta en la base de datos: con la lista SAG son cientos de productos y
    solo se cargan y decoran los de la página visible."""
    filters = {
        'q': request.GET.get('q', '').strip(),
        'categoria': request.GET.get('categoria', ''),
        'cultivo': request.GET.get('cultivo', ''),
        'estado': request.GET.get('estado', ''),
        'sag': request.GET.get('sag', ''),
        'funcion_sag': request.GET.get('funcion_sag', ''),
        'solo_calculables': request.GET.get('solo_calculables') == '1',
        'orden': request.GET.get('orden', 'nombre'),
    }
    types = _types_by_category()
    counts = {key: 0 for key in CATEGORY_LABELS}
    for name, total in Insumo.objects.values_list('tipo__nombre').annotate(total=Count('pk')).order_by():
        counts[category_key(name)] += total
    product_total = sum(counts.values())

    result = Insumo.objects.all()
    if filters['q']:
        needle = filters['q']
        result = result.filter(
            Q(nombre__icontains=needle)
            | Q(fabricante__icontains=needle)
            | Q(ingrediente_activo__icontains=needle)
            | Q(composicion_nutricional__icontains=needle)
            | Q(variedad_semilla__icontains=needle)
            | Q(numero_autorizacion_sag__icontains=needle)
            | Q(descripcion__icontains=needle)
        )
    if filters['categoria'] in CATEGORY_LABELS:
        result = result.filter(tipo__nombre__in=types[filters['categoria']])
    if filters['cultivo'].isdigit():
        result = result.filter(Q(cultivo__isnull=True) | Q(cultivo_id=filters['cultivo']))
    if filters['estado'] in PRODUCT_STATUS_LABELS:
        result = result.filter(estado_verificacion=filters['estado'])
    if filters['sag'] == 'incluidos':
        result = result.filter(registro_sag__en_lista_vigente=True)
    elif filters['sag'] == 'no_incluidos':
        result = result.filter(registro_sag__isnull=True)
    if filters['funcion_sag'] in SAG_CLASSIFICATIONS:
        result = result.filter(registro_sag__clasificacion=filters['funcion_sag'])
    if filters['orden'] == 'categoria':
        label_of = [When(tipo__nombre__in=names, then=Value(CATEGORY_LABELS[key])) for key, names in types.items() if names]
        result = result.annotate(category_label_sort=Case(*label_of, default=Value(''))).order_by(
            'category_label_sort', Lower('nombre'), 'pk'
        )
    elif filters['orden'] == 'verificacion':
        result = result.annotate(
            verified_last=Case(When(estado_verificacion='verified', then=Value(0)), default=Value(1))
        ).order_by('verified_last', Lower('nombre'), 'pk')
    else:
        result = result.order_by(Lower('nombre'), 'pk')
    result = result.select_related('tipo', 'cultivo', 'zona', 'registro_sag')
    if filters['solo_calculables']:
        # Pocos productos cumplen la condición; el uso verificado de fitosanitarios se confirma aquí.
        result = [
            item for item in map(_decorate_product, result.filter(_calculable_candidates())) if item.calculable
        ]
    page = Paginator(result, PRODUCTS_PER_PAGE).get_page(request.GET.get('pagina'))
    if not filters['solo_calculables']:
        page.object_list = [_decorate_product(item) for item in page.object_list]
    query = request.GET.copy()
    query.pop('pagina', None)
    return render_page(
        request,
        'productos.html',
        'productos',
        {
            'products_page': page,
            'product_total': product_total,
            'product_count': page.paginator.count,
            'product_counts': [(key, CATEGORY_LABELS[key], counts[key]) for key in CATEGORY_LABELS],
            'product_filters': filters,
            'product_query': query.urlencode(),
            'product_crops': Cultivo.objects.filter(activo=True).order_by('nombre'),
            'product_statuses': Insumo.ESTADO_VERIFICACION,
            'product_sag_filters': SAG_FILTERS.items(),
            'product_sag_classifications': SAG_CLASSIFICATIONS.items(),
            'sag_source_url': URL_LISTA_SAG,
            'category_labels': CATEGORY_LABELS,
        },
    )


SEARCH_LIMIT = 20


@login_required
def productos_buscar(request):
    """Búsqueda bajo demanda para las calculadoras (JSON): productos de la lista SAG que no van en ``agro-catalog``.

    ``?q=`` (mínimo 2 letras) busca por nombre, fabricante o sustancia entre los que figuran en la lista vigente;
    ``?id=`` trae uno (enlaces ``?insumo=``), aunque sea un registro histórico.
    Nunca devuelve semillas ni fitosanitarios: la lista SAG solo tiene fertilizantes, bioestimulantes y afines, y
    las semillas siguen saliendo de su propio catálogo. Máximo 20 resultados por consulta.
    """
    products = (
        productos_vigentes()
        .filter(importados_de_lista_sag())
        .exclude(estado_verificacion__in=['restricted', 'cancelled'])
        .exclude(tipo__nombre__iexact='semilla')
        .select_related('tipo', 'cultivo', 'zona', 'registro_sag')
    )
    product_id = request.GET.get('id', '')
    needle = ' '.join(request.GET.get('q', '').split())[:80]
    if product_id:
        if not product_id.isdigit():
            return JsonResponse({'results': [], 'total': 0})
        products = products.filter(pk=product_id)
    elif len(needle) >= 2:
        # Al buscar solo se ofrecen los que figuran en la lista vigente. Un registro histórico (ya no figura) se
        # puede abrir por ``?id=`` desde su ficha y llega con ``sag.listed = false`` para mostrarse como tal.
        products = products.filter(registro_sag__en_lista_vigente=True).filter(
            Q(nombre__icontains=needle) | Q(fabricante__icontains=needle) | Q(composicion_nutricional__icontains=needle)
        )
    else:
        return JsonResponse({'results': [], 'total': 0})
    categoria = request.GET.get('categoria', '')
    if categoria in CATEGORY_LABELS:
        products = products.filter(tipo__nombre__in=_types_by_category()[categoria])
    products = [item for item in products.order_by(Lower('nombre'), 'pk')[: SEARCH_LIMIT + 1] if not item.es_fitosanitario]
    return JsonResponse(
        {
            'results': [insumo_payload(item) for item in products[:SEARCH_LIMIT]],
            'more': len(products) > SEARCH_LIMIT,
        }
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
    # Sin dosis documentada igual se puede calcular escribiendo la dosis propia (queda marcado como simulación);
    # los fitosanitarios no, porque solo se calculan con la dosis de su etiqueta verificada.
    manual_calc = (
        not product.calculable
        and not product.is_phyto
        and product.is_current
        and product.estado_verificacion not in {'restricted', 'cancelled'}
    )
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
            'manual_calc': manual_calc,
            'sag_classifications': SAG_CLASSIFICATIONS,
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
