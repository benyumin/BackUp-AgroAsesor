"""Lista SAG de fertilizantes y bioestimulantes autorizados para agricultura orgánica.

Lee el XLSX oficial (hoja «Vigentes») sin dependencias nuevas: un XLSX es un ZIP con XML, así que basta con
``zipfile`` y ``xml.etree`` de la biblioteca estándar. La importación la hace el comando ``importar_lista_sag``;
ninguna pantalla consulta al SAG en línea.

Reglas de la importación:

- Cada fila se identifica por su huella (nombre comercial + fabricante + sustancia activa normalizados), no por
  el «N°» del archivo, que es solo la posición. Repetir la importación no duplica registros.
- Se guardan las columnas tal como vienen en ``RegistroListaSAG`` (procedencia) y se crea un ``Insumo`` con los
  datos que la ficha muestra. La lista no trae dosis: el insumo queda «Información no verificada», sin dosis.
- Si el catálogo ya tenía un insumo con el mismo nombre y fabricante, se vincula a ese insumo sin modificarlo.
- Un producto que deja de aparecer en una lista nueva no se borra: queda con ``en_lista_vigente = False``.
- Al reimportar solo se actualizan los campos del insumo que siguen con el valor que puso la importación
  anterior; lo que el administrador haya editado (y cualquier dosis) se respeta.
"""

import hashlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from xml.etree import ElementTree

from .models import Insumo, RegistroListaSAG, TipoInsumo

URL_LISTA_SAG = (
    'https://www.sag.gob.cl/content/lista-de-fertilizantes-y-bioestimulantes-autorizados-para-agricultura-organica'
)
NOMBRE_LISTA_SAG = 'SAG · Lista de fertilizantes y bioestimulantes autorizados para agricultura orgánica'
HOJA_REGISTROS = 'Vigentes'
# Encabezados de la hoja «Vigentes» (normalizados) → campo de RegistroListaSAG.
COLUMNAS = {
    'n': 'numero_lista',
    'nombre comercial': 'nombre_comercial',
    'sustancia activa': 'sustancia_activa',
    'fabricante/ distribuidor': 'fabricante',
    'funcion': 'funcion',
    'fecha de autorizacion': 'fecha_autorizacion',
    'observaciones': 'observaciones',
}
COLUMNAS_OBLIGATORIAS = {'nombre_comercial', 'sustancia_activa', 'fabricante', 'funcion'}

# Categoría del catálogo (TipoInsumo) que recibe cada clasificación. Las mixtas quedan en «Fertilizante» y la
# función declarada completa se muestra igual en la ficha y en el filtro «Función según SAG».
TIPO_POR_CLASIFICACION = {
    RegistroListaSAG.Clasificacion.FERTILIZANTE: 'Fertilizante',
    RegistroListaSAG.Clasificacion.FERTILIZANTE_BIOESTIMULANTE: 'Fertilizante',
    RegistroListaSAG.Clasificacion.BIOFERTILIZANTE: 'Fertilizante',
    RegistroListaSAG.Clasificacion.BIOESTIMULANTE: 'Bioestimulante',
    RegistroListaSAG.Clasificacion.ENMIENDA: 'Enmienda',
    RegistroListaSAG.Clasificacion.OTRO: 'Otros',
}

_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_NS_REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_EXCEL_EPOCH = date(1899, 12, 30)


class ErrorListaSAG(ValueError):
    """El archivo no tiene la forma esperada de la lista SAG."""


def normalizar(texto):
    """Texto sin tildes, en minúsculas y con los espacios colapsados (para comparar y armar la huella)."""
    plano = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    return ' '.join(plano.casefold().split())


def limpiar(texto):
    """Texto tal como viene, solo con los espacios colapsados (el archivo trae dobles espacios y saltos)."""
    return ' '.join(str(texto or '').split())


def huella(nombre, fabricante, sustancia):
    base = '|'.join(normalizar(valor) for valor in (nombre, fabricante, sustancia))
    return hashlib.sha256(base.encode()).hexdigest()


def fecha_excel(valor):
    """Fecha de una celda de Excel (número de serie o texto dd-mm-aaaa). ``None`` si no hay o no se entiende."""
    texto = str(valor or '').strip()
    if not texto:
        return None
    try:
        serie = float(texto)
    except ValueError:
        coincidencia = re.fullmatch(r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})', texto)
        if not coincidencia:
            return None
        dia, mes, anio = (int(parte) for parte in coincidencia.groups())
        try:
            return date(anio, mes, dia)
        except ValueError:
            return None
    if not 1 <= serie < 2958466:
        return None
    return _EXCEL_EPOCH + timedelta(days=int(serie))


def clasificar(funcion):
    """Clasificación a partir de la función declarada por el SAG (texto libre, con variantes y erratas).

    Es una agrupación para filtrar, no un dato nuevo: la función original se guarda y se muestra siempre.
    """
    texto = normalizar(funcion)
    bio = bool(re.search(r'bi?oe?stimul|biestimul', texto))
    biofertilizante = 'biofertiliz' in texto or 'inoculante' in texto
    fertilizante = bool(re.search(r'(?<!bio)fe?r?t?i?lizante|micronutri|corrector de carencia', texto))
    enmienda = bool(re.search(r'enmienda|acondicionador|mejorador|mejoramiento de suelo|sustrato', texto))
    Clasificacion = RegistroListaSAG.Clasificacion
    if biofertilizante:
        return Clasificacion.BIOFERTILIZANTE
    if fertilizante and bio:
        return Clasificacion.FERTILIZANTE_BIOESTIMULANTE
    if bio:
        return Clasificacion.BIOESTIMULANTE
    if fertilizante:
        return Clasificacion.FERTILIZANTE
    if enmienda:
        return Clasificacion.ENMIENDA
    return Clasificacion.OTRO


# ---------------------------------------------------------------------------------------------------------------
# Lectura del XLSX
# ---------------------------------------------------------------------------------------------------------------


def _indice_columna(referencia):
    letras = re.match(r'[A-Z]+', referencia).group()
    indice = 0
    for letra in letras:
        indice = indice * 26 + ord(letra) - 64
    return indice - 1


def _filas_hoja(libro, ruta_hoja, compartidas):
    raiz = ElementTree.fromstring(libro.read(ruta_hoja))
    for fila in raiz.iter(f'{_NS}row'):
        celdas = {}
        for celda in fila.findall(f'{_NS}c'):
            tipo = celda.get('t')
            valor = celda.find(f'{_NS}v')
            if tipo == 'inlineStr':
                texto = ''.join(nodo.text or '' for nodo in celda.iter(f'{_NS}t'))
            elif valor is None:
                continue
            elif tipo == 's':
                texto = compartidas[int(valor.text)]
            else:
                texto = valor.text or ''
            celdas[_indice_columna(celda.get('r'))] = texto
        if celdas:
            yield int(fila.get('r')), [celdas.get(i, '') for i in range(max(celdas) + 1)]


def leer_hojas(ruta):
    """Todas las hojas de un XLSX, en orden: ``{nombre: [(número de fila, [celdas como texto])]}``."""
    try:
        libro = zipfile.ZipFile(ruta)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ErrorListaSAG(f'No se pudo abrir el XLSX: {exc}')
    with libro:
        compartidas = []
        if 'xl/sharedStrings.xml' in libro.namelist():
            raiz = ElementTree.fromstring(libro.read('xl/sharedStrings.xml'))
            compartidas = [''.join(t.text or '' for t in si.iter(f'{_NS}t')) for si in raiz.findall(f'{_NS}si')]
        libro_xml = ElementTree.fromstring(libro.read('xl/workbook.xml'))
        relaciones = ElementTree.fromstring(libro.read('xl/_rels/workbook.xml.rels'))
        destinos = {rel.get('Id'): rel.get('Target') for rel in relaciones}
        hojas = {}
        for hoja in libro_xml.iter(f'{_NS}sheet'):
            destino = destinos[hoja.get(f'{_NS_REL}id')].lstrip('/')
            destino = destino if destino.startswith('xl/') else 'xl/' + destino
            hojas[hoja.get('name')] = list(_filas_hoja(libro, destino, compartidas))
    return hojas


def hoja(hojas, nombre):
    """Filas de la hoja ``nombre`` (sin distinguir tildes ni mayúsculas)."""
    for actual, filas in hojas.items():
        if normalizar(actual) == normalizar(nombre):
            return filas
    raise ErrorListaSAG(f'El archivo no tiene la hoja «{nombre}».')


@dataclass
class ListaSAG:
    fecha_lista: date
    registros: list
    columnas: list
    filas_descartadas: int


def leer_lista_sag(ruta):
    """Lee el XLSX oficial y devuelve la fecha de la lista y sus registros (diccionarios con los campos del modelo).

    Ubica la fila de encabezados por su contenido (no por posición) y valida que estén las columnas esperadas.
    """
    filas = hoja(leer_hojas(ruta), HOJA_REGISTROS)

    fecha_lista = None
    encabezado = None
    for numero, fila in filas:
        textos = [normalizar(valor).rstrip(':').strip() for valor in fila]
        if fecha_lista is None and any(texto.startswith('fecha de actualizacion') for texto in textos):
            posicion = next(i for i, texto in enumerate(textos) if texto.startswith('fecha de actualizacion'))
            fecha_lista = next((fecha_excel(v) for v in fila[posicion + 1 :] if fecha_excel(v)), None)
        if 'nombre comercial' in textos:
            encabezado = (numero, {i: COLUMNAS[t] for i, t in enumerate(textos) if t in COLUMNAS})
            break
    if encabezado is None:
        raise ErrorListaSAG('No se encontró la fila de encabezados («NOMBRE COMERCIAL»…).')
    numero_encabezado, posiciones = encabezado
    faltantes = COLUMNAS_OBLIGATORIAS - set(posiciones.values())
    if faltantes:
        raise ErrorListaSAG('Faltan columnas en el archivo: ' + ', '.join(sorted(faltantes)) + '.')
    if fecha_lista is None:
        raise ErrorListaSAG('El archivo no indica la «Fecha de actualización Lista».')

    registros = []
    descartadas = 0
    for numero, fila in filas:
        if numero <= numero_encabezado:
            continue
        datos = {campo: (fila[i] if i < len(fila) else '') for i, campo in posiciones.items()}
        if not limpiar(datos.get('nombre_comercial')):
            descartadas += 1
            continue
        numero_lista = limpiar(datos.get('numero_lista'))
        registros.append(
            {
                'numero_lista': int(float(numero_lista)) if re.fullmatch(r'\d+(\.0)?', numero_lista) else None,
                'nombre_comercial': limpiar(datos['nombre_comercial']),
                'sustancia_activa': limpiar(datos.get('sustancia_activa')),
                'fabricante': limpiar(datos.get('fabricante')),
                'funcion': limpiar(datos.get('funcion')),
                'fecha_autorizacion': fecha_excel(datos.get('fecha_autorizacion')),
                'observaciones': limpiar(datos.get('observaciones')),
            }
        )
    return ListaSAG(fecha_lista, registros, sorted(set(posiciones.values())), descartadas)


# ---------------------------------------------------------------------------------------------------------------
# Importación
# ---------------------------------------------------------------------------------------------------------------


def fuente_lista(fecha_lista):
    return f'{NOMBRE_LISTA_SAG} (actualización {fecha_lista:%d-%m-%Y})'


def campos_insumo(registro, fecha_lista):
    """Datos del ``Insumo`` que salen de un registro de la lista (nunca dosis ni verificación)."""
    descripcion = f'Función declarada en la lista SAG: {registro["funcion"] or "no indicada"}.'
    if registro['observaciones']:
        descripcion += f'\nObservaciones del SAG: {registro["observaciones"]}'
    return {
        'nombre': registro['nombre_comercial'][:150],
        'fabricante': registro['fabricante'][:150],
        'composicion_nutricional': registro['sustancia_activa'][:255],
        'descripcion': descripcion,
        'fuente_nombre': fuente_lista(fecha_lista)[:255],
        'fuente_url': URL_LISTA_SAG,
    }


@dataclass
class ResultadoImportacion:
    leidos: int = 0
    repetidos_en_archivo: int = 0
    creados: int = 0
    actualizados: int = 0
    sin_cambios: int = 0
    vinculados_a_existentes: int = 0
    retirados: int = 0
    por_clasificacion: dict = None


def importar_lista(lista, archivo, url=URL_LISTA_SAG):
    """Guarda la lista en la base de datos. Debe llamarse dentro de una transacción (lo hace el comando)."""
    resultado = ResultadoImportacion(por_clasificacion={})
    tipos = {nombre: TipoInsumo.objects.get_or_create(nombre=nombre)[0] for nombre in set(TIPO_POR_CLASIFICACION.values())}
    existentes = {registro.clave: registro for registro in RegistroListaSAG.objects.select_related('insumo')}
    # Insumos que el administrador ya tenía (sin registro SAG): se vinculan por nombre y fabricante normalizados.
    # Nunca se vincula una semilla ni un fitosanitario: la lista solo tiene fertilizantes y bioestimulantes.
    curados = {}
    for insumo in Insumo.objects.filter(registro_sag__isnull=True).select_related('tipo'):
        if insumo.fabricante and not insumo.es_fitosanitario and insumo.tipo.nombre.casefold() != 'semilla':
            curados.setdefault((normalizar(insumo.nombre), normalizar(insumo.fabricante)), insumo)

    vistos = set()
    for fila in lista.registros:
        resultado.leidos += 1
        clave = huella(fila['nombre_comercial'], fila['fabricante'], fila['sustancia_activa'])
        if clave in vistos:
            resultado.repetidos_en_archivo += 1
            continue
        vistos.add(clave)
        clasificacion = clasificar(fila['funcion'])
        resultado.por_clasificacion[clasificacion] = resultado.por_clasificacion.get(clasificacion, 0) + 1
        datos_registro = {
            **fila,
            'clasificacion': clasificacion,
            'fecha_lista': lista.fecha_lista,
            'archivo_fuente': archivo[:255],
            'url_fuente': url,
            'en_lista_vigente': True,
        }
        nuevos = campos_insumo(fila, lista.fecha_lista)
        tipo = tipos[TIPO_POR_CLASIFICACION[clasificacion]]
        registro = existentes.get(clave)
        if registro is None:
            insumo = curados.pop((normalizar(fila['nombre_comercial']), normalizar(fila['fabricante'])), None)
            creado_por_importacion = insumo is None
            if insumo is None:
                insumo = Insumo(tipo=tipo, estado_verificacion='unverified', vigente=True, **nuevos)
                insumo.full_clean()
                insumo.save()
                resultado.creados += 1
            else:
                resultado.vinculados_a_existentes += 1
            RegistroListaSAG.objects.create(
                insumo=insumo, clave=clave, insumo_creado_por_importacion=creado_por_importacion, **datos_registro
            )
            continue

        anteriores = campos_insumo(
            {campo: getattr(registro, campo) for campo in ('nombre_comercial', 'fabricante', 'sustancia_activa', 'funcion', 'observaciones')},
            registro.fecha_lista,
        )
        cambios_registro = [campo for campo, valor in datos_registro.items() if getattr(registro, campo) != valor]
        cambios_insumo = []
        if registro.insumo_creado_por_importacion:
            insumo = registro.insumo
            for campo, valor in nuevos.items():
                if getattr(insumo, campo) == anteriores[campo] and getattr(insumo, campo) != valor:
                    setattr(insumo, campo, valor)
                    cambios_insumo.append(campo)
            if insumo.tipo_id == tipos[TIPO_POR_CLASIFICACION[registro.clasificacion]].pk and insumo.tipo_id != tipo.pk:
                insumo.tipo = tipo
                cambios_insumo.append('tipo')
            if cambios_insumo:
                insumo.full_clean()
                insumo.save(update_fields=cambios_insumo)
        if cambios_registro:
            for campo in cambios_registro:
                setattr(registro, campo, datos_registro[campo])
            registro.save()
        if cambios_registro or cambios_insumo:
            resultado.actualizados += 1
        else:
            resultado.sin_cambios += 1

    resultado.retirados = (
        RegistroListaSAG.objects.filter(en_lista_vigente=True).exclude(clave__in=vistos).update(en_lista_vigente=False)
    )
    return resultado
