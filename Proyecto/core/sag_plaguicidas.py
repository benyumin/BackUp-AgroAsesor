"""Insecticidas, herbicidas y fungicidas autorizados por el SAG (planillas XLSX oficiales).

Usa dos archivos del SAG porque ninguno trae todo:

- «Planilla general de plaguicidas vigentes» (la más reciente): dice QUÉ productos están vigentes, con N° SAG,
  nombre, titular, sustancias activas, concentración y años de autorización. No trae la aptitud.
- «Planilla resumida de plaguicidas autorizados»: trae la APTITUD (insecticida, herbicida…) y la formulación.

Se cruzan por N° SAG. Solo se importan los vigentes cuya aptitud incluye insecticida, herbicida o fungicida; los
vigentes que no aparecen en la planilla resumida (autorizaciones más nuevas) se omiten y se informan.

Reglas (las mismas que la lista SAG orgánica, ver ``core/sag.py``):

- Ninguna planilla trae cultivos, dosis ni carencia: el insumo queda «Información no verificada» y sin dosis.
- Si el catálogo ya tenía un insumo con el mismo N° de autorización SAG, se vincula sin modificarlo.
- Un producto que deja de estar vigente no se borra: su registro queda ``en_planilla_vigente = False`` y, si lo creó
  la importación, el insumo queda no vigente.
- Al reimportar solo se actualizan los campos que siguen con el valor que puso la importación anterior.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from .models import Insumo, RegistroPlaguicidaSAG, TipoInsumo
from .sag import ErrorListaSAG, leer_hojas, limpiar, normalizar

URL_PLAGUICIDAS_SAG = (
    'https://www.sag.gob.cl/ambitos-de-accion/'
    'listado-de-plaguicidas-autorizados-prohibidos-restringidos-y-cancelados-etiquetas-y-hds'
)
NOMBRE_PLANILLA = 'SAG · Planilla general de plaguicidas vigentes'

# Encabezados normalizados → campo. «N° SAG» queda como «n sag» y «Nº SAG» como «no sag».
COLUMNAS_VIGENTES = {
    'n sag': 'numero_sag',
    'no sag': 'numero_sag',
    'nombre comercial': 'nombre_comercial',
    'titular autorizacion': 'titular',
    'sustancias activas': 'sustancias_activas',
    'concentracion': 'concentracion',
    'ano primera autorizacion': 'anio_primera_autorizacion',
    'ano vencimiento': 'anio_vencimiento',
}
COLUMNAS_RESUMEN = {
    'n sag': 'numero_sag',
    'no sag': 'numero_sag',
    'aptitud': 'aptitud',
    'formulacion (codigo)': 'formulacion',
}

# Partes de la aptitud (separadas por «-») que se importan → categoría del catálogo (TipoInsumo).
TIPO_POR_APTITUD = {
    'insecticida': 'Insecticida',
    'insecticida biologico': 'Insecticida',
    'herbicida': 'Herbicida',
    'fungicida': 'Fungicida',
    'fungicida biologico': 'Fungicida',
}


def numero_sag(valor):
    texto = limpiar(valor)
    return texto[:-2] if re.fullmatch(r'\d+\.0', texto) else texto


def _anio(valor):
    texto = numero_sag(valor)
    return int(texto) if re.fullmatch(r'\d{4}', texto) else None


def tipo_de_aptitud(aptitud):
    """Categoría del catálogo según la aptitud declarada (la primera que se importa), o ``None``."""
    for parte in aptitud.split('-'):
        tipo = TIPO_POR_APTITUD.get(normalizar(parte))
        if tipo:
            return tipo
    return None


def fecha_de_nombre(nombre_archivo):
    """Fecha de la planilla a partir del nombre del archivo del SAG, p. ej. «… vigentes (26-10-26).xlsx»."""
    coincidencia = re.search(r'\((\d{1,2})-(\d{1,2})-(\d{2}|\d{4})\)', nombre_archivo)
    if not coincidencia:
        return None
    dia, mes, anio = (int(parte) for parte in coincidencia.groups())
    try:
        return date(anio + 2000 if anio < 100 else anio, mes, dia)
    except ValueError:
        return None


def _tabla(ruta, columnas, obligatorias):
    """Primera hoja del archivo como diccionarios, ubicando la fila de encabezados por su contenido."""
    filas = next(iter(leer_hojas(ruta).values()), [])
    for posicion, (_, fila) in enumerate(filas):
        textos = [normalizar(valor) for valor in fila]
        if 'nombre comercial' in textos or 'aptitud' in textos:
            indices = {i: columnas[t] for i, t in enumerate(textos) if t in columnas}
            faltantes = obligatorias - set(indices.values())
            if faltantes:
                raise ErrorListaSAG(f'Faltan columnas en {ruta.name}: ' + ', '.join(sorted(faltantes)) + '.')
            return [
                {campo: (fila[i] if i < len(fila) else '') for i, campo in indices.items()}
                for _, fila in filas[posicion + 1 :]
            ]
    raise ErrorListaSAG(f'No se encontró la fila de encabezados en {ruta.name}.')


@dataclass
class PlanillaPlaguicidas:
    fecha: date
    registros: list  # solo insecticidas, herbicidas y fungicidas vigentes, ya con aptitud y formulación
    vigentes: int
    sin_aptitud: list  # (N° SAG, nombre) de vigentes que no están en la planilla resumida
    otras_aptitudes: int


def leer_planillas(ruta_vigentes, ruta_resumen, fecha=None):
    fecha = fecha or fecha_de_nombre(ruta_vigentes.name)
    if fecha is None:
        raise ErrorListaSAG('No se pudo leer la fecha de la planilla en el nombre del archivo; indícala con --fecha.')
    vigentes = _tabla(ruta_vigentes, COLUMNAS_VIGENTES, set(COLUMNAS_VIGENTES.values()))
    resumen = {
        numero_sag(fila['numero_sag']): fila
        for fila in _tabla(ruta_resumen, COLUMNAS_RESUMEN, {'numero_sag', 'aptitud'})
    }
    registros, sin_aptitud, otras = [], [], 0
    for fila in vigentes:
        numero, nombre = numero_sag(fila['numero_sag']), limpiar(fila['nombre_comercial'])
        if not numero or not nombre:
            continue
        datos_resumen = resumen.get(numero)
        if datos_resumen is None:
            sin_aptitud.append((numero, nombre))
            continue
        aptitud = limpiar(datos_resumen['aptitud'])
        if tipo_de_aptitud(aptitud) is None:
            otras += 1
            continue
        registros.append(
            {
                'numero_sag': numero,
                'nombre_comercial': nombre,
                'titular': limpiar(fila['titular']),
                'sustancias_activas': limpiar(fila['sustancias_activas']),
                'concentracion': limpiar(fila['concentracion']),
                'aptitud': aptitud,
                'formulacion': limpiar(datos_resumen.get('formulacion')),
                'anio_primera_autorizacion': _anio(fila['anio_primera_autorizacion']),
                'anio_vencimiento': _anio(fila['anio_vencimiento']),
            }
        )
    total = sum(1 for fila in vigentes if numero_sag(fila['numero_sag']) and limpiar(fila['nombre_comercial']))
    return PlanillaPlaguicidas(fecha, registros, total, sin_aptitud, otras)


def _corto(texto, largo):
    """Recorta al largo del campo marcando el corte (el texto completo queda en el registro SAG)."""
    return texto if len(texto) <= largo else texto[: largo - 1] + '…'


def campos_insumo(registro, fecha):
    """Datos del ``Insumo`` que salen de la planilla (nunca dosis, cultivos ni verificación)."""
    descripcion = f'Aptitud declarada por el SAG: {registro["aptitud"]}.'
    if registro['anio_primera_autorizacion'] or registro['anio_vencimiento']:
        descripcion += (
            f'\nAutorizado desde {registro["anio_primera_autorizacion"] or "año no indicado"}; '
            f'vencimiento de la autorización: {registro["anio_vencimiento"] or "no indicado"}.'
        )
    descripcion += '\nLa planilla del SAG no informa cultivos, dosis ni carencia: revisa la etiqueta autorizada.'
    return {
        'nombre': _corto(registro['nombre_comercial'], 150),
        'fabricante': _corto(registro['titular'], 150),
        'ingrediente_activo': _corto(registro['sustancias_activas'], 255),
        'concentracion': _corto(registro['concentracion'], 100),
        'formulacion': _corto(registro['formulacion'], 100),
        'numero_autorizacion_sag': registro['numero_sag'][:100],
        'descripcion': descripcion,
        'fuente_nombre': f'{NOMBRE_PLANILLA} ({fecha:%d-%m-%Y})'[:255],
        'fuente_url': URL_PLAGUICIDAS_SAG,
    }


CAMPOS_REGISTRO = (
    'nombre_comercial',
    'titular',
    'sustancias_activas',
    'concentracion',
    'aptitud',
    'formulacion',
    'anio_primera_autorizacion',
    'anio_vencimiento',
)


@dataclass
class ResultadoPlaguicidas:
    creados: int = 0
    vinculados_a_existentes: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    retirados: int = 0
    por_tipo: dict = field(default_factory=dict)


def _validar(insumo):
    # Sin las claves foráneas ni la unicidad: evita una consulta por producto (el tipo ya viene de la base).
    insumo.full_clean(exclude=['tipo', 'cultivo', 'zona'], validate_unique=False, validate_constraints=False)


def importar_planillas(planilla, archivo):
    """Guarda la planilla en la base de datos. Debe llamarse dentro de una transacción (lo hace el comando)."""
    resultado = ResultadoPlaguicidas()
    tipos = {nombre: TipoInsumo.objects.get_or_create(nombre=nombre)[0] for nombre in set(TIPO_POR_APTITUD.values())}
    existentes = {r.numero_sag: r for r in RegistroPlaguicidaSAG.objects.select_related('insumo', 'insumo__tipo')}
    curados = {}
    for insumo in Insumo.objects.filter(registro_plaguicida__isnull=True).exclude(numero_autorizacion_sag=''):
        curados.setdefault(numero_sag(insumo.numero_autorizacion_sag), insumo)

    nuevos = []
    vistos = set()
    for fila in planilla.registros:
        if fila['numero_sag'] in vistos:
            continue
        vistos.add(fila['numero_sag'])
        tipo = tipos[tipo_de_aptitud(fila['aptitud'])]
        resultado.por_tipo[tipo.nombre] = resultado.por_tipo.get(tipo.nombre, 0) + 1
        datos_registro = {
            **fila,
            'fecha_planilla': planilla.fecha,
            'archivo_fuente': archivo[:255],
            'url_fuente': URL_PLAGUICIDAS_SAG,
            'en_planilla_vigente': True,
        }
        nuevos_campos = campos_insumo(fila, planilla.fecha)
        registro = existentes.get(fila['numero_sag'])
        if registro is None:
            insumo = curados.pop(fila['numero_sag'], None)
            creado = insumo is None
            if creado:
                insumo = Insumo(tipo=tipo, estado_verificacion='unverified', vigente=True, **nuevos_campos)
                _validar(insumo)
                resultado.creados += 1
            else:
                resultado.vinculados_a_existentes += 1
            nuevos.append((insumo, datos_registro, creado))
            continue

        anteriores = campos_insumo({**fila, **{c: getattr(registro, c) for c in CAMPOS_REGISTRO}}, registro.fecha_planilla)
        cambios_registro = [campo for campo, valor in datos_registro.items() if getattr(registro, campo) != valor]
        cambios_insumo = []
        if registro.insumo_creado_por_importacion:
            insumo = registro.insumo
            for campo, valor in nuevos_campos.items():
                if getattr(insumo, campo) == anteriores[campo] and getattr(insumo, campo) != valor:
                    setattr(insumo, campo, valor)
                    cambios_insumo.append(campo)
            if not insumo.vigente:  # vuelve a figurar como vigente
                insumo.vigente = True
                cambios_insumo.append('vigente')
            # La categoría se corrige solo si sigue siendo la que puso la importación anterior.
            tipo_anterior = tipos.get(tipo_de_aptitud(registro.aptitud))
            if tipo_anterior and insumo.tipo_id == tipo_anterior.pk and insumo.tipo_id != tipo.pk:
                insumo.tipo = tipo
                cambios_insumo.append('tipo')
            if cambios_insumo:
                _validar(insumo)
                insumo.save(update_fields=cambios_insumo)
        if cambios_registro:
            for campo in cambios_registro:
                setattr(registro, campo, datos_registro[campo])
            registro.save()
        if cambios_registro or cambios_insumo:
            resultado.actualizados += 1
        else:
            resultado.sin_cambios += 1

    # Los nuevos se guardan en lotes: contra una base remota, uno por uno toma minutos.
    Insumo.objects.bulk_create([insumo for insumo, _, creado in nuevos if creado], batch_size=200)
    RegistroPlaguicidaSAG.objects.bulk_create(
        [
            RegistroPlaguicidaSAG(insumo=insumo, insumo_creado_por_importacion=creado, **datos)
            for insumo, datos, creado in nuevos
        ],
        batch_size=200,
    )

    retirados = RegistroPlaguicidaSAG.objects.filter(en_planilla_vigente=True).exclude(numero_sag__in=vistos)
    Insumo.objects.filter(
        registro_plaguicida__in=retirados.filter(insumo_creado_por_importacion=True), vigente=True
    ).update(vigente=False)
    resultado.retirados = retirados.update(en_planilla_vigente=False)
    return resultado
