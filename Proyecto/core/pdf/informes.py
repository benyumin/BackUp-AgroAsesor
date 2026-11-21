"""Contenido de cada informe PDF de AgroAsesor.

Cada función recibe datos reales ya guardados (un ``CalculoAgricola``, una ``ReferenciaAgronomica``, un ``Insumo``
o las filas de un reporte) y devuelve los ``bytes`` del PDF. No calcula nada nuevo: muestra lo que se guardó.
No se inventan fuentes ni recomendaciones: si un dato no tiene fuente verificada, el informe lo dice.
"""

from decimal import Decimal, InvalidOperation

from .base import (
    ESTILOS,
    LINEA,
    VERDE,
    VERDE_SUAVE,
    InformePDF,
    Paragraph,
    Table,
    TableStyle,
    fecha,
    numero,
    texto,
)

MESES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
MESES_LARGOS = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]


def _decimal(valor):
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _cantidad(valor):
    cifra = _decimal(valor)
    return numero(valor, 3 if cifra is not None and abs(cifra) < 1 else 2)


# ---------------------------------------------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------------------------------------------

TITULOS_CALCULO = {
    'semillas': ('Informe de cálculo de semillas', 'Semilla necesaria'),
    'insumos': ('Informe de cálculo de insumos', 'Insumo necesario'),
    'proteccion': ('Informe de cálculo de producto fitosanitario', 'Producto necesario'),
}


def _procedencia(r):
    """Estado del dato y explicación de dónde sale la dosis, según el origen guardado con el cálculo."""
    origen = r.get('origin') or ('documented' if r.get('verified') else 'simulation')
    if origen == 'documented':
        fuente = r.get('source') or 'catálogo de AgroAsesor'
        verificada = f" (verificada el {fecha(_iso(r.get('source_date')))})" if r.get('source_date') else ''
        return (
            'ok',
            'Cálculo respaldado por información verificada',
            f'La dosis coincide con un valor documentado del catálogo. Fuente: {fuente}{verificada}.',
        )
    if origen == 'catalog':
        return (
            'aviso',
            'Dato de catálogo sin respaldo completo',
            'La dosis coincide con el catálogo, pero ese valor no tiene fuente, enlace y fecha de verificación '
            'completos. Úsalo como referencia, no como recomendación técnica.',
        )
    if origen == 'adjusted':
        return (
            'aviso',
            'Simulación: dosis ajustada por la persona',
            f"El catálogo indica {numero(r.get('catalog_dose'), 3)} {r.get('catalog_unit', '')}; el cálculo usó "
            f"{numero(r.get('dose'), 3)} {r.get('dose_unit', '')}, ingresado por la persona. "
            'No es una recomendación técnica.',
        )
    return (
        'aviso',
        'Simulación con datos ingresados por la persona',
        'El catálogo no tiene una dosis documentada para este cálculo; la dosis la ingresó la persona. '
        'El resultado es una simulación y no una recomendación agronómica.',
    )


def _iso(valor):
    from datetime import date

    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _encabezado_calculo(calculo, titulo):
    potrero = calculo.potrero
    return InformePDF(
        titulo,
        f'{potrero.nombre} · {potrero.predio.nombre}',
        referencia=f'Cálculo N.º {calculo.pk} · registrado el {fecha(calculo.creado, True)}',
        autor=calculo.usuario.get_full_name(),
    )


def _terreno(informe, calculo, superficie, cultivo):
    potrero = calculo.potrero
    registrada = potrero.superficie_ha
    uso = f'{numero(superficie, 4)} ha'
    if registrada and superficie is not None and _decimal(superficie) != registrada:
        uso += f' de {numero(registrada, 4)} ha registradas'
    informe.seccion(
        'Información del terreno',
        informe.datos(
            [
                ('Predio', potrero.predio.nombre),
                ('Potrero', potrero.nombre),
                ('Cultivo seleccionado', cultivo or 'Sin cultivo'),
                ('Superficie utilizada', uso),
            ]
        ),
        numero_seccion=1,
    )


def _limitaciones(texto_limite):
    return ('Limitaciones del cálculo', texto_limite, 'neutro')


def _cultivo_distinto(calculo, cultivo):
    actual = calculo.potrero.cultivo.nombre if calculo.potrero.cultivo_id else ''
    if cultivo and actual and actual != cultivo:
        return (
            'Cultivo distinto al registrado',
            f'Al generar este informe, el potrero tiene registrado «{actual}»; el cálculo se hizo para «{cultivo}».',
            'aviso',
        )
    return None


def informe_calculo(calculo):
    """PDF de un cálculo guardado (cualquier operación de la calculadora)."""
    r, e = calculo.resultado or {}, calculo.entrada or {}
    operacion = calculo.operacion
    superficie = e.get('area_ha') if e.get('area_ha') not in (None, '') else r.get('area')
    cultivo = r.get('crop') or e.get('crop') or (calculo.potrero.cultivo.nombre if calculo.potrero.cultivo_id else '')
    if operacion == 'dose':
        return _informe_dosis(calculo, r, e, superficie, cultivo)
    if operacion == 'coverage':
        return _informe_cobertura(calculo, r, e, superficie, cultivo)
    if operacion == 'seed_population':
        return _informe_poblacion(calculo, r, e, superficie, cultivo)
    if operacion == 'fertilizer_nutrition':
        return _informe_nutriente(calculo, r, e, superficie, cultivo)
    return _informe_generico(calculo, r, e, superficie, cultivo)


def _informe_dosis(calculo, r, e, superficie, cultivo):
    tipo = e.get('calculation_type') or 'insumos'
    titulo, rotulo = TITULOS_CALCULO.get(tipo, TITULOS_CALCULO['insumos'])
    informe = _encabezado_calculo(calculo, titulo)
    tono, estado, procedencia = _procedencia(r)
    informe.estado(estado, tono)
    _terreno(informe, calculo, superficie, cultivo)

    dose_unit = r.get('dose_unit') or e.get('unit') or ''
    fuente = r.get('source') if r.get('verified') else 'Sin fuente técnica verificada'
    if r.get('origin') == 'catalog':
        fuente = 'Catálogo de AgroAsesor, sin respaldo completo'
    parametros = [
        ('Dosis por hectárea', numero(r.get('dose'), 3)),
        ('Unidad de medida', dose_unit),
        ('Tipo de cálculo', 'Cantidad por superficie'),
        ('Fuente de información', fuente),
    ]
    if r.get('product'):
        parametros.append(('Producto del catálogo', r['product']))
    if tipo == 'proteccion' and e.get('target'):
        parametros.append(('Objetivo de control', e['target']))
    if r.get('declared_source'):
        parametros.append(('Fuente indicada por la persona', r['declared_source']))
    informe.seccion('Parámetros del cálculo', informe.datos(parametros), numero_seccion=2)

    extra = []
    paquete = r.get('package')
    if paquete:
        extra.append(
            f"Equivale a {paquete.get('count')} envase(s) de {numero(paquete.get('size'), 3)} "
            f"{paquete.get('unit', '')} según la presentación registrada del producto."
        )
    saco = r.get('user_bag')
    if saco:
        linea = (
            f"Con sacos de {numero(saco.get('size'), 2)} kg (indicados por la persona): {saco.get('count')} saco(s); "
            f"sobran {numero(saco.get('leftover'), 2)} kg."
        )
        if saco.get('total_cost_clp') is not None:
            linea += f" Costo estimado: ${numero(saco['total_cost_clp'], 0)}."
        extra.append(linea)
    informe.seccion(
        'Resultado principal',
        informe.resultado(
            rotulo,
            _cantidad(r.get('amount')),
            r.get('unit', ''),
            f'para {numero(superficie, 4)} ha de {cultivo or "cultivo no indicado"} en {calculo.potrero.nombre}',
            tono='ok' if tono == 'ok' else 'aviso',
            extra=extra,
        ),
        numero_seccion=3,
    )
    informe.seccion(
        'Detalle de la operación',
        informe.formula(
            'Cantidad total = superficie × dosis por hectárea',
            [
                f"{numero(superficie, 4)} ha × {numero(r.get('dose'), 3)} {texto(dose_unit)} = "
                f"<b>{_cantidad(r.get('amount'))} {texto(r.get('unit', ''))}</b>"
            ],
        ),
        numero_seccion=4,
    )
    observaciones = [(f'Estado de verificación: {estado}', procedencia, tono)]
    if r.get('above_max'):
        observaciones.append(
            (
                'Dosis sobre el máximo documentado',
                f"La dosis usada supera el máximo registrado para el producto: {numero(r.get('max_dose'), 3)} "
                f"{r.get('max_unit', '')}. Revisa la etiqueta o la fuente técnica antes de usarla.",
                'alerta',
            )
        )
    observaciones.append(_cultivo_distinto(calculo, cultivo))
    if tipo == 'semillas':
        limite = (
            'La cantidad es superficie × dosis por hectárea. No ajusta por germinación, pureza ni pérdidas de '
            'siembra; para eso existe el cálculo avanzado por población.'
        )
    elif tipo == 'proteccion':
        limite = (
            'La cantidad es superficie × dosis de etiqueta. Respeta la etiqueta autorizada: dosis, período de '
            'carencia, tiempo de reingreso y elementos de protección personal.'
        )
    else:
        limite = 'La cantidad es superficie × dosis por hectárea. No incluye pérdidas de aplicación.'
    limite += ' La superficie y la dosis se usan con todos sus decimales; solo el resultado se redondea.'
    observaciones.append(_limitaciones(limite))
    informe.seccion(
        'Observaciones técnicas', informe.observaciones([o for o in observaciones if o]), numero_seccion=5
    )
    return informe.generar()


def _informe_cobertura(calculo, r, e, superficie, cultivo):
    informe = _encabezado_calculo(calculo, 'Informe de disponibilidad de insumo')
    tono, estado, procedencia = _procedencia(r)
    informe.estado(estado, tono)
    _terreno(informe, calculo, superficie, cultivo)
    unidad = r.get('unit', '')
    informe.seccion(
        'Parámetros del cálculo',
        informe.datos(
            [
                ('Dosis por hectárea', numero(r.get('dose'), 3)),
                ('Unidad de medida', r.get('dose_unit', '')),
                ('Cantidad disponible', f"{_cantidad(r.get('stock'))} {unidad}"),
                ('Tipo de cálculo', 'Cobertura de la superficie con lo disponible'),
                ('Producto del catálogo', r.get('product') or None),
                ('Fuente de información', r.get('source') if r.get('verified') else 'Sin fuente técnica verificada'),
            ]
        ),
        numero_seccion=2,
    )
    falta = _decimal(r.get('missingQty')) or Decimal(0)
    detalle = (
        f"Alcanza para toda la superficie; sobran {_cantidad(r.get('surplus'))} {unidad}."
        if falta <= 0
        else f"Faltan {_cantidad(r.get('missingQty'))} {unidad} para cubrir {numero(r.get('missingHa'), 4)} ha."
    )
    informe.seccion(
        'Resultado principal',
        informe.resultado(
            'Superficie cubierta',
            f"{numero(r.get('percent'), 0)} %",
            '',
            f"{numero(r.get('coveredHa'), 4)} de {numero(superficie, 4)} ha. {detalle}",
            tono='ok' if falta <= 0 else 'aviso',
        ),
        numero_seccion=3,
    )
    informe.seccion(
        'Detalle de la operación',
        informe.formula(
            'Necesario = superficie × dosis · Superficie cubierta = mínimo entre la superficie y disponible ÷ dosis',
            [
                f"Necesario: {numero(superficie, 4)} ha × {numero(r.get('dose'), 3)} {texto(r.get('dose_unit', ''))} = "
                f"<b>{_cantidad(r.get('needed'))} {texto(unidad)}</b>",
                f"Cubierta: {_cantidad(r.get('stock'))} {texto(unidad)} ÷ {numero(r.get('dose'), 3)} "
                f"{texto(r.get('dose_unit', ''))} = <b>{numero(r.get('coveredHa'), 4)} ha</b> (como máximo la superficie)",
            ],
        ),
        numero_seccion=4,
    )
    informe.seccion(
        'Observaciones técnicas',
        informe.observaciones(
            [
                o
                for o in (
                    (f'Estado de verificación: {estado}', procedencia, tono),
                    _cultivo_distinto(calculo, cultivo),
                    _limitaciones('Supone una aplicación pareja con la dosis indicada en toda la superficie.'),
                )
                if o
            ]
        ),
        numero_seccion=5,
    )
    return informe.generar()


def _informe_poblacion(calculo, r, e, superficie, cultivo):
    informe = _encabezado_calculo(calculo, 'Informe de cálculo avanzado de semillas')
    informe.estado(
        'Cálculo con parámetros declarados por la persona',
        'aviso',
        f"Fuente declarada de los parámetros: {r.get('source') or e.get('source') or 'no indicada'}.",
    )
    _terreno(informe, calculo, superficie, cultivo)
    informe.seccion(
        'Parámetros del cálculo',
        informe.datos(
            [
                ('Plantas objetivo', f"{numero(e.get('plants_m2'), 2)} plantas/m²"),
                ('Peso de mil semillas', f"{numero(e.get('thousand_seed_weight_g'), 2)} g"),
                ('Germinación', f"{numero(e.get('germination_pct'), 2)} %"),
                ('Pureza', f"{numero(e.get('purity_pct'), 2)} %"),
                ('Tipo de cálculo', 'Dosis por población objetivo'),
                ('Fuente de información', r.get('source') or e.get('source')),
            ]
        ),
        numero_seccion=2,
    )
    informe.seccion(
        'Resultado principal',
        informe.resultado(
            'Semilla necesaria',
            _cantidad(r.get('amount')),
            r.get('unit', 'kg'),
            f"Dosis calculada: {numero(r.get('rate'), 3)} kg/ha para {numero(superficie, 4)} ha de "
            f"{cultivo or 'cultivo no indicado'}.",
            tono='aviso',
        ),
        numero_seccion=3,
    )
    germ = _decimal(e.get('germination_pct'))
    pureza = _decimal(e.get('purity_pct'))
    informe.seccion(
        'Detalle de la operación',
        informe.formula(
            'Dosis (kg/ha) = plantas/m² × peso de mil semillas (g) ÷ (100 × germinación × pureza)',
            [
                f"{numero(e.get('plants_m2'), 2)} × {numero(e.get('thousand_seed_weight_g'), 2)} ÷ (100 × "
                f"{numero(germ / 100 if germ else None, 4)} × {numero(pureza / 100 if pureza else None, 4)}) = "
                f"<b>{numero(r.get('rate'), 3)} kg/ha</b>",
                f"{numero(superficie, 4)} ha × {numero(r.get('rate'), 3)} kg/ha = "
                f"<b>{_cantidad(r.get('amount'))} {texto(r.get('unit', 'kg'))}</b>",
            ],
        ),
        numero_seccion=4,
    )
    informe.seccion(
        'Observaciones técnicas',
        informe.observaciones(
            [
                o
                for o in (
                    (
                        'Estado de verificación',
                        'Los parámetros los ingresó la persona con la fuente indicada; AgroAsesor no los verificó.',
                        'aviso',
                    ),
                    _cultivo_distinto(calculo, cultivo),
                    _limitaciones(
                        'No considera pérdidas de establecimiento en campo salvo que se incluyan en la población '
                        'objetivo.'
                    ),
                )
                if o
            ]
        ),
        numero_seccion=5,
    )
    return informe.generar()


def _informe_nutriente(calculo, r, e, superficie, cultivo):
    informe = _encabezado_calculo(calculo, 'Informe de fertilización por nutriente')
    informe.estado(
        'Composición del fertilizante verificada',
        'ok',
        f"Fuente del producto: {r.get('productSource') or '—'}. Necesidad del nutriente según: "
        f"{r.get('nutrientSource') or '—'}.",
    )
    _terreno(informe, calculo, superficie, cultivo)
    nutriente = r.get('nutrient', '')
    informe.seccion(
        'Parámetros del cálculo',
        informe.datos(
            [
                ('Fertilizante', r.get('product')),
                ('Nutriente', nutriente),
                ('Necesidad del nutriente', f"{numero(r.get('nutrientNeedPerHa'), 3)} kg/ha"),
                ('Riqueza del fertilizante', f"{numero(r.get('nutrientPct'), 2)} %"),
                ('Tipo de cálculo', 'Dosis de producto según necesidad de nutriente'),
                ('Fuente de la necesidad', r.get('nutrientSource')),
            ]
        ),
        numero_seccion=2,
    )
    informe.seccion(
        'Resultado principal',
        informe.resultado(
            'Fertilizante necesario',
            _cantidad(r.get('amount')),
            r.get('unit', 'kg'),
            f"Aporta {_cantidad(r.get('nutrientApplied'))} kg de {nutriente} en {numero(superficie, 4)} ha.",
        ),
        numero_seccion=3,
    )
    informe.seccion(
        'Detalle de la operación',
        informe.formula(
            'Producto por hectárea = necesidad ÷ (riqueza ÷ 100) · Total = superficie × producto por hectárea',
            [
                f"{numero(r.get('nutrientNeedPerHa'), 3)} kg/ha ÷ ({numero(r.get('nutrientPct'), 2)} ÷ 100) = "
                f"<b>{numero(r.get('dose'), 3)} kg de producto/ha</b>",
                f"{numero(superficie, 4)} ha × {numero(r.get('dose'), 3)} kg/ha = "
                f"<b>{_cantidad(r.get('amount'))} {texto(r.get('unit', 'kg'))}</b>",
            ],
        ),
        numero_seccion=4,
    )
    informe.seccion(
        'Observaciones técnicas',
        informe.observaciones(
            [
                o
                for o in (
                    ('Estado de verificación', 'Composición del producto verificada con fuente en el catálogo.', 'ok'),
                    _cultivo_distinto(calculo, cultivo),
                    _limitaciones(
                        'La necesidad del nutriente debe provenir de un análisis de suelo o de una recomendación '
                        'técnica.'
                    ),
                )
                if o
            ]
        ),
        numero_seccion=5,
    )
    return informe.generar()


def _informe_generico(calculo, r, e, superficie, cultivo):
    """Operaciones antiguas (por ejemplo, rendimiento): se muestran los datos guardados tal cual."""
    informe = _encabezado_calculo(calculo, f'Informe de cálculo · {calculo.get_operacion_display()}')
    _terreno(informe, calculo, superficie, cultivo)
    filas = [(str(k), str(v)) for k, v in r.items() if isinstance(v, (str, int, float))]
    informe.seccion('Datos guardados', informe.datos(filas), numero_seccion=2)
    return informe.generar()


# ---------------------------------------------------------------------------------------------------------------
# Calendario de siembra
# ---------------------------------------------------------------------------------------------------------------


def _rango(mes_inicio, dia_inicio, mes_fin, dia_fin):
    if not mes_inicio or not mes_fin:
        return 'Sin ventana registrada'

    def punto(mes, dia):
        return f'{dia} de {MESES_LARGOS[mes - 1]}' if dia else MESES_LARGOS[mes - 1]

    return f'{punto(mes_inicio, dia_inicio)} – {punto(mes_fin, dia_fin)}'.capitalize()


def _meses(inicio, fin):
    if not inicio or not fin:
        return set()
    if inicio <= fin:
        return set(range(inicio, fin + 1))
    return set(range(inicio, 13)) | set(range(1, fin + 1))


def _barra_meses(informe, referencia):
    """Tabla de 12 meses con las ventanas de siembra (verde) y cosecha (verde claro)."""
    siembra = _meses(referencia.siembra_inicio_mes, referencia.siembra_fin_mes)
    cosecha = _meses(referencia.cosecha_inicio_mes, referencia.cosecha_fin_mes)
    ancho_rotulo = 22 * 2.834645669
    ancho_mes = (informe.ancho_util - ancho_rotulo) / 12
    filas = [
        [''] + [Paragraph(f'<para alignment="center">{m}</para>', ESTILOS['etiqueta']) for m in MESES],
        [Paragraph('<b>Siembra</b>', ESTILOS['celda'])] + [''] * 12,
        [Paragraph('<b>Cosecha</b>', ESTILOS['celda'])] + [''] * 12,
    ]
    tabla = Table(filas, colWidths=[ancho_rotulo] + [ancho_mes] * 12, rowHeights=[14, 16, 16])
    estilo = [
        ('GRID', (1, 1), (-1, -1), 0.5, LINEA),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]
    estilo += [('BACKGROUND', (m, 1), (m, 1), VERDE) for m in siembra]
    estilo += [('BACKGROUND', (m, 2), (m, 2), VERDE_SUAVE) for m in cosecha]
    estilo += [('LINEBELOW', (m, 2), (m, 2), 2, VERDE) for m in cosecha]
    tabla.setStyle(TableStyle(estilo))
    return tabla


def informe_calendario(referencia, anio, autor=''):
    """PDF de una referencia del calendario de siembra (cultivo + zona)."""
    verificada = referencia.tiene_fuente_verificada
    informe = InformePDF(
        f'Calendario de siembra · {referencia.cultivo.nombre}',
        f'{referencia.zona.nombre} · temporada {anio}',
        referencia=f'Referencia agronómica N.º {referencia.pk}',
        autor=autor,
    )
    if verificada:
        informe.estado('Referencia verificada con fuente', 'ok')
    else:
        informe.estado(
            'Referencia de demostración, sin fuente verificada',
            'aviso',
            'Las fechas y textos de esta referencia no tienen una fuente oficial registrada. Úsalos solo como guía.',
        )
    informe.seccion(
        'Cultivo y zona',
        informe.datos(
            [
                ('Cultivo', referencia.cultivo.nombre),
                ('Variedad', referencia.variedad or 'Referencia general'),
                ('Zona', referencia.zona.nombre),
                ('Año de la referencia', referencia.anio_referencia or 'General (todos los años)'),
                ('Nombre científico', referencia.nombre_cientifico or None),
                ('Familia botánica', referencia.familia_botanica or None),
                ('Tipo de cultivo', referencia.tipo_cultivo or None),
                ('Profundidad de siembra', referencia.profundidad_siembra or None),
            ]
        ),
        numero_seccion=1,
    )
    informe.seccion(
        'Ventanas de siembra y cosecha',
        _barra_meses(informe, referencia),
        informe.datos(
            [
                (
                    'Siembra',
                    _rango(
                        referencia.siembra_inicio_mes,
                        referencia.siembra_inicio_dia,
                        referencia.siembra_fin_mes,
                        referencia.siembra_fin_dia,
                    ),
                ),
                (
                    'Cosecha',
                    _rango(
                        referencia.cosecha_inicio_mes,
                        referencia.cosecha_inicio_dia,
                        referencia.cosecha_fin_mes,
                        referencia.cosecha_fin_dia,
                    ),
                ),
                (
                    'Duración del ciclo',
                    f'{referencia.duracion_ciclo_dias} días' if referencia.duracion_ciclo_dias else None,
                ),
                (
                    'Rendimiento de referencia',
                    f'{numero(referencia.rendimiento_ton_ha, 2)} ton/ha'
                    if referencia.rendimiento_ton_ha is not None
                    else None,
                ),
            ]
        ),
        numero_seccion=2,
    )
    agronomia = [
        ('Condiciones de siembra', referencia.condicion_siembra),
        ('Requerimientos', referencia.requerimientos),
        ('Etapas fenológicas', referencia.etapas_fenologicas),
        ('Condiciones de crecimiento', referencia.condiciones_crecimiento),
        ('Manejo general', referencia.manejo_general),
        ('Indicadores de madurez', referencia.indicadores_madurez),
        ('Consideraciones de cosecha', referencia.consideraciones_cosecha),
        ('Plagas', referencia.plagas),
        ('Enfermedades', referencia.enfermedades),
        ('Malezas', referencia.malezas),
        ('Manejo integrado', referencia.manejo_integrado),
    ]
    agronomia = [(t, v) for t, v in agronomia if v]
    if agronomia:
        informe.seccion(
            'Información agronómica',
            *[
                bloque
                for titulo, valor in agronomia
                for bloque in (Paragraph(texto(titulo), ESTILOS['subseccion']), informe.parrafo(valor))
            ],
            numero_seccion=3,
            dividir=True,
        )
    fuente = (
        [
            ('Institución', referencia.institucion_fuente),
            ('Documento', referencia.documento_fuente),
            ('Enlace', referencia.url_fuente),
            ('Fecha de consulta', fecha(referencia.fecha_consulta_fuente)),
        ]
        if verificada
        else [('Fuente', 'Sin fuente verificada registrada')]
    )
    informe.seccion('Fuente de la información', informe.datos(fuente, columnas=1), numero_seccion=4 if agronomia else 3)
    return informe.generar()


# ---------------------------------------------------------------------------------------------------------------
# Ficha de producto
# ---------------------------------------------------------------------------------------------------------------


def _dosis(valor, unidad):
    return f'{numero(valor, 3)} {unidad}' if valor else None


def informe_producto(producto, faltantes, autor=''):
    """PDF de la ficha de un insumo del catálogo (misma información que la ficha web)."""
    informe = InformePDF(
        f'Ficha técnica · {producto.nombre}',
        f'{producto.category_label} · {producto.tipo.nombre}',
        referencia=f'Producto N.º {producto.pk} del catálogo de AgroAsesor',
        autor=autor,
    )
    if producto.estado_verificacion == 'verified' and producto.is_current:
        informe.estado(producto.status_label, 'ok')
    elif producto.estado_verificacion in {'restricted', 'cancelled'} or not producto.is_current:
        informe.estado(
            producto.status_label if producto.is_current else 'Producto fuera de vigencia',
            'alerta',
            'No se puede usar en los cálculos de AgroAsesor.',
        )
    else:
        informe.estado(producto.status_label, 'aviso')

    identificacion = [
        ('Nombre comercial', producto.nombre),
        ('Categoría', producto.tipo.nombre),
        ('Fabricante', producto.fabricante),
        ('Ingrediente activo / composición', producto.ingrediente_activo or producto.composicion_nutricional),
        ('Concentración', producto.concentracion),
        ('Formulación', producto.formulacion),
        (
            'Presentación',
            f'{numero(producto.presentacion_cantidad, 3)} {producto.presentacion_unidad}'
            if producto.presentacion_cantidad
            else '',
        ),
    ]
    if producto.is_phyto:
        identificacion.append(('Autorización SAG', producto.numero_autorizacion_sag or 'No registrada'))
    informe.seccion(
        'Identificación',
        informe.datos(identificacion),
        informe.parrafo(producto.descripcion) if producto.descripcion else None,
        numero_seccion=1,
    )
    uso = [
        ('Cultivo asociado', producto.cultivo.nombre if producto.cultivo_id else 'General'),
        ('Zona', producto.zona.nombre if producto.zona_id else 'Sin restricción registrada'),
    ]
    if producto.is_phyto:
        uso += [
            ('Cultivos autorizados', ', '.join(producto.cultivos_autorizados or []) or 'No registrados'),
            ('Objetivos de control', ', '.join(producto.objetivos_control or []) or 'No registrados'),
        ]
    informe.seccion('Uso documentado', informe.datos(uso), numero_seccion=2)
    dosis = [
        ('Dosis de etiqueta' if producto.is_phyto else 'Dosis', _dosis(producto.dosis_hectarea, producto.unidad_dosis)
         or '—'),
        ('Dosis máxima', _dosis(producto.dosis_maxima_hectarea, producto.unidad_dosis_maxima) or '—'),
        ('Fuente de la dosis', producto.dosis_fuente),
        ('Dosis con fuente verificada', 'Sí' if producto.dosis_con_fuente else 'No'),
    ]
    if producto.category == 'fertilizer':
        dosis += [
            ('Nitrógeno (N)', f'{numero(producto.nitrogeno_pct, 2)} %' if producto.nitrogeno_pct is not None else '—'),
            ('Fósforo (P2O5)', f'{numero(producto.fosforo_p2o5_pct, 2)} %' if producto.fosforo_p2o5_pct is not None else '—'),
            ('Potasio (K2O)', f'{numero(producto.potasio_k2o_pct, 2)} %' if producto.potasio_k2o_pct is not None else '—'),
        ]
    if producto.category == 'seed':
        dosis += [
            ('Variedad', producto.variedad_semilla),
            ('Tipo de semilla', producto.tipo_semilla),
            ('Plantas objetivo por m²', numero(producto.plantas_objetivo_m2, 2) if producto.plantas_objetivo_m2 else '—'),
            ('Peso de mil semillas', f'{numero(producto.peso_mil_semillas_g, 2)} g' if producto.peso_mil_semillas_g else '—'),
            ('Germinación', f'{numero(producto.germinacion_pct, 1)} %' if producto.germinacion_pct else '—'),
            ('Pureza', f'{numero(producto.pureza_pct, 1)} %' if producto.pureza_pct else '—'),
        ]
    informe.seccion(
        'Dosis y aplicación',
        informe.datos(dosis),
        Paragraph('<b>Modo de aplicación (etiqueta)</b>', ESTILOS['subseccion']),
        informe.parrafo(producto.modo_aplicacion or 'No registrado'),
        numero_seccion=3,
    )
    seccion = 4
    if producto.is_phyto or producto.restricciones or producto.precauciones_ambientales:
        informe.seccion(
            'Seguridad y medio ambiente',
            informe.datos(
                [
                    ('Período de carencia', producto.periodo_carencia or 'No registrado'),
                    ('Tiempo de reingreso', producto.tiempo_reingreso or 'No registrado'),
                    ('Restricciones', producto.restricciones or 'No registradas'),
                    ('Precauciones ambientales', producto.precauciones_ambientales or 'No registradas'),
                    ('Polinizadores', producto.advertencia_polinizadores or 'No registrado'),
                ],
                columnas=1,
            ),
            informe.parrafo(
                'Usa el equipo de protección indicado en la etiqueta, respeta carencia y reingreso, y realiza triple '
                'lavado de envases. Este resumen no reemplaza la etiqueta autorizada.',
                'chico',
            )
            if producto.is_phyto
            else None,
            numero_seccion=seccion,
        )
        seccion += 1
    informe.seccion(
        'Fuentes y vigencia',
        informe.datos(
            [
                ('Fuente técnica', producto.fuente_nombre or 'No registrada'),
                ('Enlace de la fuente', producto.fuente_url or None),
                ('Fecha de verificación', fecha(producto.fecha_verificacion)),
                (
                    'Vigencia',
                    f'{fecha(producto.vigencia_desde) if producto.vigencia_desde else "Sin inicio"} – '
                    f'{fecha(producto.vigencia_hasta) if producto.vigencia_hasta else "sin término"}',
                ),
                ('Etiqueta', producto.etiqueta_url or 'No vinculada'),
                ('Hoja de seguridad', producto.hoja_seguridad_url or 'No vinculada'),
            ],
            columnas=1,
        ),
        numero_seccion=seccion,
    )
    if faltantes:
        informe.seccion(
            'Datos pendientes de verificación',
            informe.observaciones(
                [('Falta registrar o verificar', '\n'.join(f'• {dato.capitalize()}' for dato in faltantes), 'aviso')]
            ),
            numero_seccion=seccion + 1,
        )
    return informe.generar()


# ---------------------------------------------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------------------------------------------


def informe_reporte(titulo, descripcion, columnas, filas, filtros, autor='', indicadores=None):
    """PDF de un reporte (resumen, cuaderno de campo, historial de cultivos o cálculos) con sus filtros."""
    informe = InformePDF(titulo, descripcion, horizontal=len(columnas) > 8, autor=autor,
                         referencia=f'{len(filas)} registro(s)')
    informe.seccion('Filtros aplicados', informe.datos(filtros, columnas=4 if len(columnas) > 8 else 2), numero_seccion=1)
    numero_seccion = 2
    if indicadores:
        informe.seccion('Indicadores', informe.datos(indicadores, columnas=4), numero_seccion=numero_seccion)
        numero_seccion += 1
    informe.seccion('Detalle', informe.tabla(columnas, filas), numero_seccion=numero_seccion, dividir=True)
    return informe.generar()


__all__ = ['informe_calculo', 'informe_calendario', 'informe_producto', 'informe_reporte']
