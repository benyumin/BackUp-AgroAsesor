import unicodedata

from django.core.exceptions import ObjectDoesNotExist

from django.db.models import Q

from .models import (
    Cultivo,
    Insumo,
    TipoInsumo,
    Zona,
    ReferenciaAgronomica,
    Potrero,
    TemporadaCultivo,
    VariedadSAG,
)

# Cultivos de referencia para la agricultura de la zona central de Chile, agrupados para que el
# selector sea fácil de recorrer. Son solo nombres: no incluyen rendimientos, densidades ni dosis,
# que deben salir de fuentes verificadas cargadas por el administrador.
CROP_GROUPS = [
    ('Cereales y granos', ['Maíz', 'Trigo', 'Avena', 'Cebada', 'Arroz']),
    ('Leguminosas', ['Poroto', 'Lenteja', 'Garbanzo', 'Arveja', 'Haba']),
    (
        'Hortalizas',
        [
            'Tomate',
            'Cebolla',
            'Lechuga',
            'Zapallo',
            'Zanahoria',
            'Ajo',
            'Pimentón',
            'Repollo',
            'Sandía',
            'Melón',
            'Alcachofa',
            'Acelga',
            'Espinaca',
            'Brócoli',
            'Coliflor',
            'Cilantro',
            'Pepino',
            'Zapallo italiano',
        ],
    ),
    ('Tubérculos y raíces', ['Papa', 'Remolacha']),
    ('Forrajes y praderas', ['Alfalfa', 'Trébol', 'Ballica']),
    (
        'Frutales',
        [
            'Vid',
            'Palto',
            'Cerezo',
            'Manzano',
            'Nogal',
            'Arándano',
            'Olivo',
            'Limonero',
            'Naranjo',
            'Kiwi',
            'Durazno',
            'Ciruelo',
            'Damasco',
            'Almendro',
            'Mandarino',
            'Frutilla',
        ],
    ),
    ('Cultivos industriales', ['Raps', 'Maravilla']),
]
OTHER_CROPS_GROUP = 'Otros cultivos'

# Clave visual y nombre corto de cada grupo para los filtros del selector de cultivos (solo presentación).
CROP_GROUP_META = {
    'Cereales y granos': ('cereales', 'Cereales'),
    'Leguminosas': ('leguminosas', 'Leguminosas'),
    'Hortalizas': ('hortalizas', 'Hortalizas'),
    'Tubérculos y raíces': ('tuberculos', 'Tubérculos'),
    'Forrajes y praderas': ('forrajes', 'Forrajeros'),
    'Frutales': ('frutales', 'Frutales'),
    'Cultivos industriales': ('industriales', 'Industriales'),
    OTHER_CROPS_GROUP: ('otros', 'Otros'),
}
# Orden en que se sugieren cultivos cuando la persona aún no tiene historial. Solo se usan los que existen en el catálogo.
SUGGESTED_CROPS = ['Maíz', 'Trigo', 'Papa', 'Poroto', 'Tomate']


def _plain(text):
    return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().casefold().strip()


_CROP_GROUP_BY_NAME = {_plain(name): group for group, names in CROP_GROUPS for name in names}


def crop_group_name(name):
    """Grupo de un cultivo por su nombre ("Maíz grano" cae en el mismo grupo que "Maíz")."""
    key = _plain(name)
    if key in _CROP_GROUP_BY_NAME:
        return _CROP_GROUP_BY_NAME[key]
    first_word = key.split()[0] if key else ''
    return _CROP_GROUP_BY_NAME.get(first_word, OTHER_CROPS_GROUP)


def grouped_crop_choices(crops, empty_label):
    """Opciones agrupadas (optgroup) para un <select> de cultivos."""
    buckets = {}
    for crop in sorted(crops, key=lambda item: _plain(item.nombre)):
        buckets.setdefault(crop_group_name(crop.nombre), []).append((crop.pk, crop.nombre))
    choices = [('', empty_label)]
    for group in [name for name, _ in CROP_GROUPS] + [OTHER_CROPS_GROUP]:
        if group in buckets:
            choices.append((group, buckets[group]))
    return choices


def crop_picker_data(user):
    """Cultivos frecuentes y categorías para elegir cultivo en el Centro de decisiones (sin cifras inventadas).

    - frequent: cultivos que la persona ya usó en sus potreros; si aún no hay historial, algunos
      cultivos del catálogo marcados como «sugeridos» (frequentBasis lo distingue).
    - groups: clave y nombre corto de cada categoría para los filtros.
    """
    crops = list(Cultivo.objects.filter(activo=True))
    by_plain = {_plain(crop.nombre): crop.pk for crop in crops}

    usage = {}
    for crop_id, created in TemporadaCultivo.objects.filter(
        potrero__predio__propietario=user,
        cultivo__activo=True,
    ).values_list('cultivo_id', 'creado'):
        count, last = usage.get(crop_id, (0, 0))
        usage[crop_id] = (count + 1, max(last, created.timestamp()))
    for crop_id in Potrero.objects.filter(
        predio__propietario=user,
        cultivo__isnull=False,
        cultivo__activo=True,
    ).values_list('cultivo_id', flat=True):
        usage.setdefault(crop_id, (1, 0))
    names = {crop.pk: crop.nombre for crop in crops}
    used = sorted(usage, key=lambda pk: (-usage[pk][0], -usage[pk][1], _plain(names.get(pk, ''))))[:5]
    if used:
        frequent, basis = [str(pk) for pk in used], 'used'
    else:
        frequent = [str(by_plain[_plain(name)]) for name in SUGGESTED_CROPS if _plain(name) in by_plain]
        basis = 'suggested' if frequent else ''

    return {
        'frequent': frequent,
        'frequentBasis': basis,
        'groups': [
            {'name': name, 'key': CROP_GROUP_META[name][0], 'short': CROP_GROUP_META[name][1]}
            for name in [group for group, _ in CROP_GROUPS] + [OTHER_CROPS_GROUP]
        ],
    }


def ensure_base_crops():
    """Crea los cultivos base que falten (sin tocar los existentes). Devuelve cuántos creó."""
    existing = {_plain(name) for name in Cultivo.objects.values_list('nombre', flat=True)}
    created = 0
    for group, names in CROP_GROUPS:
        for name in names:
            if _plain(name) in existing:
                continue
            Cultivo.objects.create(nombre=name, descripcion=group, activo=True)
            created += 1
    return created


DEFAULT_ZONE_NAME = 'Melipilla'
DEFAULT_ZONE_DESCRIPTION = 'Comuna de Melipilla, Región Metropolitana.'


def default_zone():
    """Zona activa por defecto (Melipilla), si ya existe en el catálogo. No escribe en la base de datos."""
    zones = list(Zona.objects.filter(activo=True, nombre__icontains='melipilla').order_by('nombre'))
    exact = [zone for zone in zones if _plain(zone.nombre) == _plain(DEFAULT_ZONE_NAME)]
    return (exact or zones or [None])[0]


def ensure_default_zone():
    """Asegura la zona «Melipilla» (la zona de trabajo inicial del proyecto). Devuelve (zona, creada).

    Es una etiqueta geográfica, no un dato agronómico: no trae ventanas de siembra, rendimientos ni dosis.
    Si el administrador la desactivó a propósito, se respeta y no se vuelve a crear.
    """
    zone = default_zone()
    if zone:
        return zone, False
    if Zona.objects.filter(nombre__iexact=DEFAULT_ZONE_NAME).exists():
        return None, False
    return Zona.objects.create(nombre=DEFAULT_ZONE_NAME, descripcion=DEFAULT_ZONE_DESCRIPTION, activo=True), True


def _num(value):
    if value is None:
        return None
    return float(value)


CATEGORY_KEYS = {
    'semilla': 'seed',
    'semillas': 'seed',
    'fertilizante': 'fertilizer',
    'fertilizantes': 'fertilizer',
    'fertilizer': 'fertilizer',
    'enmienda': 'fertilizer',
    'bioestimulante': 'biostimulant',
    'bioestimulantes': 'biostimulant',
    'herbicida': 'herbicide',
    'herbicidas': 'herbicide',
    'fungicida': 'fungicide',
    'fungicidas': 'fungicide',
    'insecticida': 'insecticide',
    'insecticidas': 'insecticide',
    'acaricida': 'insecticide',
    'plaguicida': 'insecticide',
}
CATEGORY_LABELS = {
    'seed': 'Semillas',
    'fertilizer': 'Fertilizantes',
    'biostimulant': 'Bioestimulantes',
    'herbicide': 'Herbicidas',
    'fungicide': 'Fungicidas',
    'insecticide': 'Insecticidas y acaricidas',
    'other': 'Otros insumos agrícolas',
}


def category_key(type_name):
    return CATEGORY_KEYS.get((type_name or '').strip().casefold(), 'other')


def crop_varieties():
    varieties = {}
    for crop_id, variety in (
        ReferenciaAgronomica.objects.exclude(
            estado=ReferenciaAgronomica.Estado.RETIRADA,
        )
        .exclude(variedad='')
        .values_list('cultivo_id', 'variedad')
    ):
        varieties.setdefault(str(crop_id), set()).add(variety)
    for crop_id, variety in (
        Insumo.objects.exclude(cultivo=None)
        .exclude(
            variedad_semilla='',
        )
        .values_list('cultivo_id', 'variedad_semilla')
    ):
        varieties.setdefault(str(crop_id), set()).add(variety)
    for crop_id, variety in VariedadSAG.objects.filter(en_registro_vigente=True).values_list('cultivo_id', 'variedad'):
        varieties.setdefault(str(crop_id), set()).add(variety)
    return {key: sorted(values, key=str.casefold) for key, values in varieties.items()}


def sag_payload(insumo):
    """Procedencia en la lista SAG orgánica (``None`` si el insumo no viene de esa lista)."""
    try:
        registro = insumo.registro_sag
    except ObjectDoesNotExist:
        return None
    return {
        'listed': registro.en_lista_vigente,
        'function': registro.funcion,
        'classification': registro.clasificacion,
        'classificationLabel': registro.get_clasificacion_display(),
        'authorizedAt': registro.fecha_autorizacion.isoformat() if registro.fecha_autorizacion else '',
        'listDate': registro.fecha_lista.isoformat(),
        'notes': registro.observaciones,
        'url': registro.url_fuente,
        'imported': registro.insumo_creado_por_importacion,
    }


def insumo_payload(insumo):
    """Datos de un insumo para las calculadoras y el buscador (misma forma en ``agro-catalog`` y en la búsqueda
    bajo demanda de ``/productos/buscar/``). Espera ``select_related('tipo', 'cultivo', 'zona', 'registro_sag')``.
    """
    return {
        'id': str(insumo.pk),
        'name': insumo.nombre,
        'active': insumo.vigente_en,
        'usable': insumo.vigente_en and insumo.estado_verificacion not in {'restricted', 'cancelled'},
        'category': category_key(insumo.tipo.nombre),
        'categoryLabel': CATEGORY_LABELS[category_key(insumo.tipo.nombre)],
        'cropId': str(insumo.cultivo_id) if insumo.cultivo_id else '',
        'zoneId': str(insumo.zona_id) if insumo.zona_id else '',
        'applicationMode': insumo.modo_aplicacion,
        'crop': insumo.cultivo.nombre if insumo.cultivo else '',
        'type': insumo.tipo.nombre,
        'dose': _num(insumo.dosis_hectarea),
        'unit': insumo.unidad_dosis,
        'maxDose': _num(insumo.dosis_maxima_hectarea),
        'maxDoseUnit': insumo.unidad_dosis_maxima,
        'validFrom': insumo.vigencia_desde.isoformat() if insumo.vigencia_desde else '',
        'validUntil': insumo.vigencia_hasta.isoformat() if insumo.vigencia_hasta else '',
        'zone': insumo.zona.nombre if insumo.zona else '',
        'description': insumo.descripcion,
        'manufacturer': insumo.fabricante,
        'verificationStatus': insumo.estado_verificacion,
        'verified': insumo.estado_verificacion == 'verified',
        'sourceName': insumo.fuente_nombre,
        'sourceUrl': insumo.fuente_url,
        'verifiedAt': insumo.fecha_verificacion.isoformat() if insumo.fecha_verificacion else '',
        'doseVerified': insumo.dosis_con_fuente,
        'doseSource': insumo.dosis_fuente,
        'labelUrl': insumo.etiqueta_url,
        'safetySheetUrl': insumo.hoja_seguridad_url,
        'activeIngredient': insumo.ingrediente_activo,
        'concentration': insumo.concentracion,
        'formulation': insumo.formulacion,
        'sagRegistration': insumo.numero_autorizacion_sag,
        'authorizedCrops': insumo.cultivos_autorizados,
        'controlTargets': insumo.objetivos_control,
        'restrictions': insumo.restricciones,
        'preharvestInterval': insumo.periodo_carencia,
        'reentryInterval': insumo.tiempo_reingreso,
        'environmentalPrecautions': insumo.precauciones_ambientales,
        'pollinatorWarning': insumo.advertencia_polinizadores,
        'packageAmount': _num(insumo.presentacion_cantidad),
        'packageUnit': insumo.presentacion_unidad,
        'nutrientComposition': insumo.composicion_nutricional,
        'nitrogenPct': _num(insumo.nitrogeno_pct),
        'phosphorusP2o5Pct': _num(insumo.fosforo_p2o5_pct),
        'potassiumK2oPct': _num(insumo.potasio_k2o_pct),
        'seedVariety': insumo.variedad_semilla,
        'seedType': insumo.tipo_semilla,
        'targetPlantsM2': _num(insumo.plantas_objetivo_m2),
        'thousandSeedWeightG': _num(insumo.peso_mil_semillas_g),
        'germinationPct': _num(insumo.germinacion_pct),
        'purityPct': _num(insumo.pureza_pct),
        'verifiedUse': insumo.uso_verificado,
        'sag': sag_payload(insumo),
    }


def importados_de_lista_sag():
    """Condición de los insumos creados por una importación del SAG (lista orgánica o planilla de plaguicidas), no
    curados por el administrador. Son cientos: no van en cada página, se buscan bajo demanda."""
    return Q(registro_sag__insumo_creado_por_importacion=True) | Q(
        registro_plaguicida__insumo_creado_por_importacion=True
    )


def insumos_del_catalogo():
    """Insumos curados: todos menos los creados por la importación de la lista SAG."""
    return Insumo.objects.exclude(importados_de_lista_sag())


def catalog_payload():
    cultivos = list(Cultivo.objects.order_by('nombre'))
    # Los ~750 productos importados de la lista SAG no van en cada página: las calculadoras los buscan bajo demanda.
    insumos = list(
        insumos_del_catalogo()
        .select_related('tipo', 'cultivo', 'zona', 'registro_sag')
        .order_by('nombre')
    )
    zonas = list(Zona.objects.order_by('nombre'))
    regional_yields = [
        reference
        for reference in ReferenciaAgronomica.objects.filter(
            estado=ReferenciaAgronomica.Estado.VERIFICADA,
            cultivo__activo=True,
            zona__activo=True,
        )
        .select_related('cultivo', 'zona')
        .order_by(
            'cultivo__nombre',
            'zona__nombre',
            'variedad',
        )
        if reference.tiene_fuente_verificada and reference.rendimiento_ton_ha is not None
    ]

    categories = [category_key(insumo.tipo.nombre) for insumo in insumos]
    return {
        'fromDb': True,
        'stats': {
            'crops': sum(1 for cultivo in cultivos if cultivo.activo),
            'zones': sum(1 for zona in zonas if zona.activo),
            'seeds': categories.count('seed'),
            'cropsWithDensity': sum(
                1 for cultivo in cultivos if cultivo.activo and cultivo.densidad_siembra is not None
            ),
            'fertilizers': categories.count('fertilizer'),
            'otherInputs': len(categories) - categories.count('seed') - categories.count('fertilizer'),
            'products': len(insumos),
            'inventoryTracked': False,
        },
        'categoryLabels': CATEGORY_LABELS,
        'varieties': crop_varieties(),
        'zonas': [
            {'id': str(zona.pk), 'name': zona.nombre, 'description': zona.descripcion, 'active': zona.activo}
            for zona in zonas
        ],
        'cultivos': [
            {
                'id': str(cultivo.pk),
                'name': cultivo.nombre,
                'group': crop_group_name(cultivo.nombre),
                'description': cultivo.descripcion,
                'yield': _num(cultivo.rendimiento_ton_ha),
                'density': _num(cultivo.densidad_siembra),
                'unit': cultivo.unidad_siembra or 'kg',
                'densitySource': cultivo.densidad_fuente,
                'densityUrl': cultivo.densidad_url,
                'densityVerifiedAt': cultivo.densidad_verificada.isoformat() if cultivo.densidad_verificada else '',
                'yieldSource': cultivo.rendimiento_fuente,
                'yieldUrl': cultivo.rendimiento_url,
                'yieldVerifiedAt': cultivo.rendimiento_verificado.isoformat() if cultivo.rendimiento_verificado else '',
                'active': cultivo.activo,
            }
            for cultivo in cultivos
        ],
        'insumos': [insumo_payload(insumo) for insumo in insumos],
        'semillas': [
            {
                'id': str(cultivo.pk),
                'name': cultivo.nombre,
                'crop': cultivo.nombre,
                'density': _num(cultivo.densidad_siembra),
                'unit': cultivo.unidad_siembra or 'kg',
                'densitySource': cultivo.densidad_fuente,
                'densityUrl': cultivo.densidad_url,
                'densityVerifiedAt': cultivo.densidad_verificada.isoformat() if cultivo.densidad_verificada else '',
            }
            for cultivo in cultivos
            if cultivo.activo and cultivo.densidad_siembra is not None
        ],
        'yields': {
            cultivo.nombre: _num(cultivo.rendimiento_ton_ha)
            for cultivo in cultivos
            if cultivo.rendimiento_ton_ha is not None
        },
        'yieldReferences': {
            cultivo.nombre: {
                'rate': _num(cultivo.rendimiento_ton_ha),
                'source': cultivo.rendimiento_fuente,
                'url': cultivo.rendimiento_url,
                'verifiedAt': cultivo.rendimiento_verificado.isoformat() if cultivo.rendimiento_verificado else '',
            }
            for cultivo in cultivos
            if cultivo.rendimiento_ton_ha is not None
        },
        'regionalYieldReferences': [
            {
                'cropId': str(reference.cultivo_id),
                'crop': reference.cultivo.nombre,
                'zoneId': str(reference.zona_id),
                'zone': reference.zona.nombre,
                'variety': reference.variedad,
                'rate': _num(reference.rendimiento_ton_ha),
                'source': reference.institucion_fuente,
                'document': reference.documento_fuente,
                'url': reference.url_fuente,
                'verifiedAt': reference.fecha_consulta_fuente.isoformat(),
            }
            for reference in regional_yields
        ],
    }


def empty_catalog_payload():
    """Misma forma que ``catalog_payload()`` con el catálogo vacío, sin consultar la base de datos.

    La usan las pantallas que no leen ``agro-catalog`` (ver ``CATALOG_TEMPLATES`` en ``views/base.py``): la etiqueta
    sigue en la página con las mismas claves, pero no se repiten las consultas del catálogo en cada clic.
    """
    return {
        'fromDb': True,
        'stats': {
            'crops': 0,
            'zones': 0,
            'seeds': 0,
            'cropsWithDensity': 0,
            'fertilizers': 0,
            'otherInputs': 0,
            'products': 0,
            'inventoryTracked': False,
        },
        'categoryLabels': dict(CATEGORY_LABELS),
        'varieties': {},
        'zonas': [],
        'cultivos': [],
        'insumos': [],
        'semillas': [],
        'yields': {},
        'yieldReferences': {},
        'regionalYieldReferences': [],
    }


CATEGORIAS_INSUMO = [
    'Fertilizante',
    'Bioestimulante',
    'Enmienda',
    'Herbicida',
    'Fungicida',
    'Insecticida',
    'Acaricida',
    'Otros',
    'Semilla',
]


def ensure_insumo_categories():
    """Carga las categorías base de insumo (tabla maestra TipoInsumo). Se puede repetir sin duplicar."""

    created = 0
    for name in CATEGORIAS_INSUMO:
        _obj, was_created = TipoInsumo.objects.update_or_create(nombre=name)
        created += int(was_created)
    return created
