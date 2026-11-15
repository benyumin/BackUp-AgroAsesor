"""Cálculos agrícolas de la calculadora (semillas, insumos y «¿Me alcanza?»).

Este módulo es la «lógica de negocio» de la calculadora: recibe los datos que envía la pantalla, los valida y
devuelve el resultado. No sabe nada de HTTP ni de plantillas; la vista ``core.views.calculadora.calculate`` solo
lee el JSON, llama a :func:`ejecutar_calculo` y guarda el resultado en ``CalculoAgricola``.

Operaciones disponibles (campo ``operation`` del JSON):

``dose``                  cantidad = superficie (ha) × dosis por hectárea.
``coverage``              «¿Me alcanza?»: cuánta superficie cubre lo que la persona tiene.
``seed_population``       dosis de semilla a partir de población, peso de mil semillas, germinación y pureza.
``fertilizer_nutrition``  kilos de fertilizante para aportar una necesidad de nutriente (N, P₂O₅ o K₂O).

Precisión: todas las operaciones se hacen con ``Decimal`` sobre los valores tal como llegan (la superficie se
guarda con 4 decimales en el potrero). Solo se convierte a ``float`` al armar la respuesta JSON; el redondeo para
mostrar lo hace la pantalla.

Procedencia del dato (``origin``), para no presentar una simulación como recomendación:

``documented``  la dosis coincide con un valor del catálogo que tiene fuente verificada;
``catalog``     coincide con un valor del catálogo que no tiene respaldo completo;
``adjusted``    la persona cambió un valor que el catálogo sí tenía;
``simulation``  el catálogo no tenía valor y la persona lo ingresó a mano.
"""

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from .catalog import category_key
from .models import Cultivo, Insumo, Potrero

ORIGIN_LABELS = {
    'documented': 'Dato documentado',
    'catalog': 'Dato de catálogo (sin respaldo completo)',
    'adjusted': 'Ajustado manualmente',
    'simulation': 'Simulación',
}

TIPOS_DE_CALCULO = {'semillas', 'insumos', 'proteccion', 'coverage'}
# Qué categorías admite cada operación y cuál se usa si la pantalla no envía ninguna.
TIPOS_POR_OPERACION = {
    'dose': {'semillas', 'insumos', 'proteccion'},
    'coverage': {'coverage', 'proteccion'},
    'seed_population': {'semillas'},
    'fertilizer_nutrition': {'insumos'},
}
TIPO_POR_DEFECTO = {
    'dose': 'insumos',
    'coverage': 'coverage',
    'seed_population': 'semillas',
    'fertilizer_nutrition': 'insumos',
}
# Datos de entrada que se guardan junto al resultado (para reportes, PDF e historial).
CAMPOS_DE_ENTRADA = (
    'crop',
    'product_id',
    'target',
    'dose',
    'unit',
    'stock',
    'plants_m2',
    'thousand_seed_weight_g',
    'germination_pct',
    'purity_pct',
    'source',
    'dose_source',
    'nutrient',
    'need_per_ha',
    'need_source',
    'calculation_type',
    'bag_kg',
    'bag_price_clp',
)
MAX_TEXTO_FUENTE = 255


class ErrorCalculo(ValueError):
    """Dato inválido para calcular. El mensaje se muestra tal cual a la persona."""


@dataclass
class Calculo:
    """Resultado de :func:`ejecutar_calculo`, listo para guardarse en ``CalculoAgricola``."""

    potrero: Potrero
    operacion: str
    tipo: str
    resultado: dict
    entrada: dict


# ---------------------------------------------------------------------------------------------------------------
# Validaciones y consultas compartidas
# ---------------------------------------------------------------------------------------------------------------


def numero_positivo(valor, etiqueta, permitir_cero=False):
    """Convierte ``valor`` en ``Decimal`` y exige que sea mayor que 0 (o 0 o mayor con ``permitir_cero``).

    Rechaza textos, infinitos, NaN y números absurdamente grandes. ``etiqueta`` es el nombre del dato en el mensaje
    de error, por ejemplo «La superficie».
    """
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise ErrorCalculo(f'{etiqueta} debe ser un número válido.')
    if not numero.is_finite() or numero > Decimal('1e18') or numero < 0 or (not permitir_cero and numero == 0):
        condicion = '0 o mayor' if permitir_cero else 'mayor a 0'
        raise ErrorCalculo(f'{etiqueta} debe ser {condicion}.')
    return numero


def productos_vigentes(hoy=None):
    """Insumos marcados como vigentes y dentro de su período de vigencia (si lo tienen) a la fecha ``hoy``."""
    hoy = hoy or timezone.localdate()
    return Insumo.objects.filter(vigente=True).filter(
        Q(vigencia_desde__isnull=True) | Q(vigencia_desde__lte=hoy),
        Q(vigencia_hasta__isnull=True) | Q(vigencia_hasta__gte=hoy),
    )


def misma_unidad(a, b):
    """«kg/ha» y «kg» son la misma unidad para comparar (sin distinguir mayúsculas)."""
    return a.removesuffix('/ha').casefold() == b.removesuffix('/ha').casefold()


def formato_superficie(valor):
    """Superficie con hasta 4 decimales (la precisión con que se guarda), sin ceros sobrantes: «0,1496»."""
    texto = f'{Decimal(valor):,.4f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return texto.rstrip('0').rstrip(',')


def _potrero_del_usuario(usuario, plot_id):
    if not str(plot_id or '').isdigit():
        raise ErrorCalculo('Selecciona un potrero registrado.')
    potrero = (
        Potrero.objects.select_related('cultivo', 'predio__zona').filter(predio__propietario=usuario, pk=plot_id).first()
    )
    if potrero is None:
        raise ErrorCalculo('El potrero no existe o no pertenece a tu cuenta.')
    return potrero


def _texto_fuente(valor):
    return str(valor or '').strip()[:MAX_TEXTO_FUENTE]


# ---------------------------------------------------------------------------------------------------------------
# Fórmulas
# ---------------------------------------------------------------------------------------------------------------


def cantidad_por_dosis(superficie_ha, dosis_por_ha):
    """Cantidad total = superficie (ha) × dosis por hectárea. La unidad es la de la dosis sin «/ha»."""
    return superficie_ha * dosis_por_ha


def dosis_semilla_por_poblacion(plantas_m2, peso_mil_semillas_g, germinacion_pct, pureza_pct):
    """Kilos de semilla por hectárea para lograr una población objetivo.

    Derivación (sin factores adicionales de pérdida en campo):

    * plantas por hectárea           = plantas/m² × 10.000
    * semillas a sembrar por hectárea = plantas por hectárea ÷ (germinación × pureza), con ambos como fracción
    * kg por hectárea                = semillas × peso de mil semillas (g) ÷ 1.000 ÷ 1.000

    Simplificando: ``kg/ha = plantas/m² × PMS (g) ÷ (100 × germinación × pureza)``.
    Ejemplo: 300 plantas/m², PMS 45 g, 90 % y 98 % → 300 × 45 ÷ (100 × 0,90 × 0,98) = 153,06 kg/ha.
    """
    return plantas_m2 * peso_mil_semillas_g / (Decimal('100') * (germinacion_pct / 100) * (pureza_pct / 100))


def producto_por_nutriente(necesidad_kg_ha, porcentaje_nutriente):
    """Kilos de fertilizante por hectárea para aportar ``necesidad_kg_ha`` de un nutriente con ese % de riqueza."""
    return necesidad_kg_ha / (porcentaje_nutriente / 100)


# ---------------------------------------------------------------------------------------------------------------
# Cultivo, producto y procedencia de la dosis
# ---------------------------------------------------------------------------------------------------------------


def _resolver_cultivo(datos, potrero):
    """Cultivo enviado por la pantalla o, si no viene, el cultivo registrado en el potrero."""
    nombre = str(datos.get('crop') or '').strip()
    if not nombre and potrero.cultivo_id:
        nombre = potrero.cultivo.nombre
    cultivo = Cultivo.objects.filter(nombre=nombre, activo=True).first() if nombre else None
    if nombre and cultivo is None:
        raise ErrorCalculo('Selecciona un cultivo activo del catálogo.')
    return nombre, cultivo


def _resolver_producto(datos, cultivo, tipo):
    """Producto del catálogo (opcional). Comprueba que esté vigente y que corresponda al cultivo y al cálculo."""
    if not datos.get('product_id'):
        return None
    if not str(datos['product_id']).isdigit():
        raise ErrorCalculo('Selecciona un producto del catálogo.')
    producto = productos_vigentes().select_related('tipo', 'cultivo').filter(pk=datos['product_id']).first()
    if producto is None:
        raise ErrorCalculo('El producto no existe o no está vigente.')
    if producto.estado_verificacion in {'restricted', 'cancelled'}:
        raise ErrorCalculo('El producto está restringido o tiene su registro cancelado; no se puede calcular.')
    if producto.cultivo_id and (cultivo is None or producto.cultivo.nombre != cultivo.nombre):
        raise ErrorCalculo('El producto del catálogo no corresponde al cultivo seleccionado.')
    categoria = category_key(producto.tipo.nombre)
    if tipo == 'semillas' and categoria != 'seed':
        raise ErrorCalculo('Selecciona una semilla: el cálculo de semillas no admite otros insumos.')
    if tipo == 'insumos' and (categoria == 'seed' or producto.es_fitosanitario):
        raise ErrorCalculo(
            'Las semillas y los productos fitosanitarios tienen su propio cálculo; '
            'selecciona un fertilizante u otro insumo.'
        )
    return producto


def _dosis_y_unidad(datos, producto, cultivo, nombre_cultivo):
    """Dosis y unidad a usar. Un fitosanitario solo se calcula con la dosis de su etiqueta verificada."""
    if producto and producto.es_fitosanitario:
        if (
            not producto.uso_verificado
            or cultivo is None
            or nombre_cultivo != producto.cultivo.nombre
            or nombre_cultivo not in producto.cultivos_autorizados
            or datos.get('target') not in producto.objetivos_control
        ):
            raise ErrorCalculo('El producto fitosanitario no tiene un uso verificado para ese cultivo y objetivo.')
        dosis, unidad = producto.dosis_hectarea, producto.unidad_dosis
        if not unidad:
            raise ErrorCalculo('El producto fitosanitario no tiene una unidad de dosis verificada.')
    else:
        dosis = numero_positivo(datos.get('dose'), 'La dosis')
        unidad = str(datos.get('unit') or '').strip()
    if producto and producto.unidad_dosis and unidad and not misma_unidad(producto.unidad_dosis, unidad):
        raise ErrorCalculo('La unidad ingresada no coincide con la unidad registrada del producto.')
    return dosis, unidad


def referencia_del_catalogo(producto, cultivo, tipo):
    """Dosis de referencia que tiene el catálogo para este cálculo, o ``None`` si no hay.

    Primero la del producto elegido; si no hay producto y es un cálculo de semillas, la densidad del cultivo.
    Devuelve un diccionario con ``valor``, ``unidad``, ``respaldada`` (tiene fuente completa), ``fuente``, ``url`` y
    ``fecha``.
    """
    if producto and producto.dosis_hectarea is not None and producto.unidad_dosis:
        return {
            'valor': producto.dosis_hectarea,
            'unidad': producto.unidad_dosis,
            'respaldada': producto.dosis_con_fuente,
            'fuente': producto.dosis_fuente or producto.fuente_nombre,
            'url': producto.etiqueta_url or producto.fuente_url,
            'fecha': producto.fecha_verificacion.isoformat() if producto.fecha_verificacion else '',
        }
    if not producto and cultivo and tipo == 'semillas' and cultivo.densidad_siembra is not None:
        return {
            'valor': cultivo.densidad_siembra,
            'unidad': (cultivo.unidad_siembra or 'kg') + '/ha',
            'respaldada': bool(cultivo.densidad_fuente and cultivo.densidad_url and cultivo.densidad_verificada),
            'fuente': cultivo.densidad_fuente,
            'url': cultivo.densidad_url,
            'fecha': cultivo.densidad_verificada.isoformat() if cultivo.densidad_verificada else '',
        }
    return None


def clasificar_origen(dosis, unidad, referencia):
    """Origen de la dosis usada (``documented``, ``catalog``, ``adjusted`` o ``simulation``) y su fuente.

    Solo una dosis idéntica a una referencia con fuente verificada cuenta como «documentada». Cualquier otro caso es
    una simulación con datos de la persona, aunque el número coincida con el catálogo.
    """
    procedencia = {'verified': False, 'source': '', 'source_url': '', 'source_date': ''}
    if referencia is None:
        return 'simulation', procedencia
    if dosis != referencia['valor'] or not misma_unidad(unidad, referencia['unidad']):
        return 'adjusted', procedencia
    if not referencia['respaldada']:
        return 'catalog', procedencia
    procedencia.update(
        {
            'verified': True,
            'source': referencia['fuente'],
            'source_url': referencia['url'],
            'source_date': referencia['fecha'],
        }
    )
    return 'documented', procedencia


# ---------------------------------------------------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------------------------------------------------


def _calcular_dosis_o_cobertura(datos, operacion, tipo, potrero, superficie):
    nombre_cultivo, cultivo = _resolver_cultivo(datos, potrero)
    producto = _resolver_producto(datos, cultivo, tipo)
    dosis, unidad = _dosis_y_unidad(datos, producto, cultivo, nombre_cultivo)
    # Una dosis sobre el máximo documentado (con fuente) no se calcula: el resultado no sería válido.
    con_maximo = bool(producto and producto.dosis_maxima_hectarea is not None and producto.dosis_con_fuente)
    if con_maximo and dosis > producto.dosis_maxima_hectarea:
        maximo = format(producto.dosis_maxima_hectarea.normalize(), 'f').replace('.', ',')
        unidad_maxima = producto.unidad_dosis_maxima or producto.unidad_dosis
        raise ErrorCalculo(
            f'La dosis supera el máximo documentado para {producto.nombre} ({maximo} {unidad_maxima}). '
            'No es posible calcular con ese valor.'
        )
    referencia = referencia_del_catalogo(producto, cultivo, tipo)
    origen, procedencia = clasificar_origen(dosis, unidad, referencia)
    procedencia.update(
        {
            'origin': origen,
            'origin_label': ORIGIN_LABELS[origen],
            'adjusted': origen == 'adjusted',
            'catalog_dose': float(referencia['valor']) if origen == 'adjusted' else None,
            'catalog_unit': referencia['unidad'] if origen == 'adjusted' else '',
            # Se mantiene en el resultado para que los informes de cálculos antiguos sigan leyéndolo.
            'above_max': False,
            'max_dose': None,
            'max_unit': '',
            'simulation': origen != 'documented',
            'product': producto.nombre if producto else '',
            'crop': cultivo.nombre if cultivo else '',
            'plot': potrero.nombre,
            'area': float(superficie),
        }
    )
    # Fuente que la persona declara para una dosis ingresada a mano (por ejemplo, «recomendación de mi asesor»).
    # Se muestra y se guarda, pero no convierte la simulación en un dato verificado.
    fuente_declarada = _texto_fuente(datos.get('dose_source'))
    if fuente_declarada and origen != 'documented':
        procedencia['declared_source'] = fuente_declarada
    unidad_cantidad = unidad.replace('/ha', '')

    if operacion == 'coverage':
        disponible = numero_positivo(datos.get('stock'), 'La cantidad disponible', permitir_cero=True)
        necesaria = cantidad_por_dosis(superficie, dosis)
        superficie_cubierta = min(superficie, disponible / dosis)
        return {
            'dose': float(dosis),
            'dose_unit': unidad,
            'needed': float(necesaria),
            'coveredHa': float(superficie_cubierta),
            'percent': float(superficie_cubierta / superficie * 100),
            'missingHa': float(max(Decimal('0'), superficie - superficie_cubierta)),
            'missingQty': float(max(Decimal('0'), necesaria - disponible)),
            'surplus': float(max(Decimal('0'), disponible - necesaria)),
            'stock': float(disponible),
            'unit': unidad_cantidad,
            'inventory': 'simulation',
            **procedencia,
        }

    cantidad = cantidad_por_dosis(superficie, dosis)
    resultado = {
        'amount': float(cantidad),
        'unit': unidad_cantidad,
        'dose': float(dosis),
        'dose_unit': unidad,
        **procedencia,
    }
    if (
        producto
        and producto.presentacion_cantidad
        and producto.presentacion_unidad
        and misma_unidad(producto.presentacion_unidad, unidad)
    ):
        resultado['package'] = {
            'size': float(producto.presentacion_cantidad),
            'unit': producto.presentacion_unidad,
            'count': math.ceil(cantidad / producto.presentacion_cantidad),
        }
    if datos.get('bag_kg') not in (None, ''):
        resultado['user_bag'] = _sacos_de_la_persona(datos, cantidad, unidad_cantidad)
    return resultado


def _sacos_de_la_persona(datos, cantidad, unidad):
    """Sacos que la persona elige o escribe (no es un producto del catálogo): cuántos, sobrante y costo."""
    if unidad.casefold() != 'kg':
        raise ErrorCalculo('Los sacos solo se calculan cuando la dosis está en kg/ha.')
    saco = numero_positivo(datos.get('bag_kg'), 'El tamaño del saco')
    if saco > 1000:
        raise ErrorCalculo('El tamaño del saco no puede superar 1.000 kg.')
    sacos = math.ceil(cantidad / saco)
    resultado = {
        'size': float(saco),
        'unit': 'kg',
        'count': sacos,
        'leftover': float(saco * sacos - cantidad),
        'source': 'ingresado por la persona',
    }
    if datos.get('bag_price_clp') not in (None, ''):
        precio = numero_positivo(datos.get('bag_price_clp'), 'El precio por saco', permitir_cero=True)
        resultado['price_clp'] = float(precio)
        resultado['total_cost_clp'] = float(precio * sacos)
    return resultado


def _calcular_por_poblacion(datos, superficie):
    plantas = numero_positivo(datos.get('plants_m2'), 'Las plantas objetivo por m²')
    peso = numero_positivo(datos.get('thousand_seed_weight_g'), 'El peso de mil semillas')
    germinacion = numero_positivo(datos.get('germination_pct'), 'La germinación')
    pureza = numero_positivo(datos.get('purity_pct'), 'La pureza')
    if germinacion > 100 or pureza > 100:
        raise ErrorCalculo('Germinación y pureza deben ser porcentajes de hasta 100.')
    fuente = str(datos.get('source') or '').strip()
    if not fuente:
        raise ErrorCalculo('Indica la fuente de los parámetros de semilla.')
    kg_por_ha = dosis_semilla_por_poblacion(plantas, peso, germinacion, pureza)
    return {
        'rate': float(kg_por_ha),
        'amount': float(kg_por_ha * superficie),
        'unit': 'kg',
        'dose_unit': 'kg/ha',
        'area': float(superficie),
        'source': fuente,
    }


def _calcular_por_nutriente(datos, superficie):
    product_id = datos.get('product_id')
    if not str(product_id or '').isdigit():
        raise ErrorCalculo('Selecciona un fertilizante del catálogo.')
    producto = productos_vigentes().select_related('tipo', 'cultivo').filter(pk=product_id).first()
    if (
        producto is None
        or producto.tipo.nombre.casefold() not in {'fertilizante', 'fertilizer'}
        or producto.estado_verificacion != 'verified'
        or not producto.fuente_nombre
        or not producto.fuente_url
        or not producto.fecha_verificacion
        or not producto.composicion_nutricional
        or producto.unidad_dosis != 'kg/ha'
    ):
        raise ErrorCalculo('La composición o unidad del fertilizante no está verificada con una fuente.')
    if producto.cultivo_id and datos.get('crop') != producto.cultivo.nombre:
        raise ErrorCalculo('El fertilizante no corresponde al cultivo seleccionado.')
    porcentajes = {
        'N': producto.nitrogeno_pct,
        'P2O5': producto.fosforo_p2o5_pct,
        'K2O': producto.potasio_k2o_pct,
    }
    nutriente = datos.get('nutrient')
    if not isinstance(nutriente, str):
        raise ErrorCalculo('Selecciona un nutriente válido.')
    porcentaje = porcentajes.get(nutriente)
    if porcentaje is None or porcentaje <= 0:
        raise ErrorCalculo('El fertilizante no tiene composición verificada para este nutriente.')
    necesidad = numero_positivo(datos.get('need_per_ha'), 'La necesidad nutricional')
    fuente_necesidad = str(datos.get('need_source') or '').strip()
    if not fuente_necesidad:
        raise ErrorCalculo('Indica la fuente documentada de la necesidad nutricional.')
    producto_por_ha = producto_por_nutriente(necesidad, porcentaje)
    cantidad = superficie * producto_por_ha
    return {
        'amount': float(cantidad),
        'unit': producto.unidad_dosis.replace('/ha', '') or 'kg',
        'dose': float(producto_por_ha),
        'dose_unit': 'kg de producto/ha',
        'nutrient': nutriente,
        'nutrientNeedPerHa': float(necesidad),
        'nutrientPct': float(porcentaje),
        'nutrientApplied': float(cantidad * (porcentaje / 100)),
        'nutrientSource': fuente_necesidad,
        'product': producto.nombre,
        'productSource': producto.fuente_nombre,
    }


# ---------------------------------------------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------------------------------------------


def ejecutar_calculo(usuario, datos):
    """Valida ``datos`` (el JSON de la pantalla) y calcula. Lanza :class:`ErrorCalculo` si algo no es válido.

    Pasos: 1) tipo de cálculo y categoría válidos; 2) superficie > 0; 3) el potrero es de ``usuario`` y la
    superficie no supera la registrada; 4) cálculo según la operación; 5) la categoría corresponde a la operación.
    Devuelve un :class:`Calculo` con el resultado y los datos de entrada que se guardan.
    """
    if not isinstance(datos, dict):
        raise ErrorCalculo('Los datos del cálculo deben ser un objeto JSON.')
    operacion = datos.get('operation')
    if not isinstance(operacion, str):
        raise ErrorCalculo('El tipo de cálculo no está disponible.')
    tipo = datos.get('calculation_type')
    if tipo is not None and (not isinstance(tipo, str) or tipo not in TIPOS_DE_CALCULO):
        raise ErrorCalculo('La categoría del cálculo no está disponible.')
    superficie = numero_positivo(datos.get('area'), 'La superficie')
    potrero = _potrero_del_usuario(usuario, datos.get('plot_id'))
    if superficie > potrero.superficie_ha:
        raise ErrorCalculo(f'La superficie no puede superar {formato_superficie(potrero.superficie_ha)} ha registradas.')

    if operacion in {'dose', 'coverage'}:
        resultado = _calcular_dosis_o_cobertura(datos, operacion, tipo, potrero, superficie)
    elif operacion == 'seed_population':
        resultado = _calcular_por_poblacion(datos, superficie)
    elif operacion == 'fertilizer_nutrition':
        resultado = _calcular_por_nutriente(datos, superficie)
    else:
        raise ErrorCalculo('El tipo de cálculo no está disponible.')

    tipo = tipo or TIPO_POR_DEFECTO[operacion]
    if tipo not in TIPOS_POR_OPERACION[operacion]:
        raise ErrorCalculo('La categoría no corresponde al tipo de cálculo.')

    entrada = {'operation': operacion, 'area_ha': str(superficie), 'plot_id': potrero.pk}
    entrada.update(
        {
            clave: str(valor)
            for clave in CAMPOS_DE_ENTRADA
            for valor in [datos.get(clave)]
            if isinstance(valor, (str, int, float, bool))
        }
    )
    return Calculo(potrero=potrero, operacion=operacion, tipo=tipo, resultado=resultado, entrada=entrada)
