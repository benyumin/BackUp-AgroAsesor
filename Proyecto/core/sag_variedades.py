"""Registro de variedades aptas para certificación del SAG (XLSX oficial).

Lee las hojas «Variedades Comerciales» y «Variedades Locales» y guarda solo las especies que corresponden a un
cultivo de la aplicación (``VariedadSAG``). Las variedades alimentan el selector de variedad del potrero
(``crop_varieties``); el registro no trae datos agronómicos y no se inventa ninguno.

Una variedad que deja de aparecer en un registro nuevo no se borra: queda ``en_registro_vigente = False`` y deja de
ofrecerse.
"""

from dataclasses import dataclass, field

from .models import Cultivo, VariedadSAG
from .sag import ErrorListaSAG, leer_hojas, limpiar, normalizar

URL_VARIEDADES_SAG = 'https://www.sag.gob.cl/content/registro-de-variedades-aptas-para-certificacion'
HOJAS = {
    'Variedades Comerciales': VariedadSAG.Registro.COMERCIAL,
    'Variedades Locales': VariedadSAG.Registro.LOCAL,
}
COLUMNAS = {
    'especie': 'especie',
    'nombre cientifico': 'nombre_cientifico',
    'variedad': 'variedad',
    'mantenedor de la variedad': 'mantenedor',
}
# Especies del registro cuyo nombre no coincide con el del cultivo en la aplicación.
CULTIVO_POR_ESPECIE = {
    'trigo harinero': 'Trigo',
    'trigo candeal': 'Trigo',
    'trebol rosado': 'Trébol',
    'ballica italiana': 'Ballica',
}


@dataclass
class RegistroVariedades:
    registros: list  # diccionarios con los campos de VariedadSAG (sin cultivo)
    por_hoja: dict


def leer_registro_variedades(ruta):
    hojas = leer_hojas(ruta)
    registros, por_hoja = [], {}
    for nombre_hoja, registro in HOJAS.items():
        filas = next((filas for nombre, filas in hojas.items() if normalizar(nombre) == normalizar(nombre_hoja)), None)
        if filas is None:
            raise ErrorListaSAG(f'El archivo no tiene la hoja «{nombre_hoja}».')
        actualizacion, indices, leidas = '', None, 0
        for _, fila in filas:
            textos = [normalizar(valor) for valor in fila]
            if indices is None:
                for valor in fila:
                    if normalizar(valor).startswith('ultima actualizacion'):
                        actualizacion = limpiar(valor.split(':', 1)[-1])
                if 'especie' in textos and 'variedad' in textos:
                    indices = {i: COLUMNAS[t] for i, t in enumerate(textos) if t in COLUMNAS}
                continue
            datos = {campo: limpiar(fila[i]) if i < len(fila) else '' for i, campo in indices.items()}
            if not datos.get('especie') or not datos.get('variedad'):
                continue
            registros.append({**datos, 'registro': registro, 'actualizacion': actualizacion[:40]})
            leidas += 1
        if indices is None:
            raise ErrorListaSAG(f'No se encontró la fila de encabezados en la hoja «{nombre_hoja}».')
        por_hoja[nombre_hoja] = leidas
    return RegistroVariedades(registros, por_hoja)


def cultivos_por_especie():
    """Cultivo activo de la aplicación que corresponde a cada especie (normalizada) del registro."""
    cultivos = {normalizar(cultivo.nombre): cultivo for cultivo in Cultivo.objects.filter(activo=True)}

    def buscar(especie):
        clave = normalizar(especie)
        return cultivos.get(normalizar(CULTIVO_POR_ESPECIE.get(clave, '')) or clave)

    return buscar


@dataclass
class ResultadoVariedades:
    creadas: int = 0
    actualizadas: int = 0
    sin_cambios: int = 0
    retiradas: int = 0
    repetidas: int = 0
    por_cultivo: dict = field(default_factory=dict)
    especies_omitidas: dict = field(default_factory=dict)


def importar_variedades(lectura, archivo):
    """Guarda las variedades. Debe llamarse dentro de una transacción (lo hace el comando)."""
    resultado = ResultadoVariedades()
    cultivo_de = cultivos_por_especie()
    existentes = {(v.especie, v.variedad): v for v in VariedadSAG.objects.all()}
    nuevas, vistas = [], set()
    for fila in lectura.registros:
        cultivo = cultivo_de(fila['especie'])
        if cultivo is None:
            resultado.especies_omitidas[fila['especie']] = resultado.especies_omitidas.get(fila['especie'], 0) + 1
            continue
        clave = (fila['especie'][:150], fila['variedad'][:150])
        if clave in vistas:
            resultado.repetidas += 1
            continue
        vistas.add(clave)
        resultado.por_cultivo[cultivo.nombre] = resultado.por_cultivo.get(cultivo.nombre, 0) + 1
        datos = {
            'cultivo_id': cultivo.pk,
            'nombre_cientifico': fila['nombre_cientifico'][:150],
            'mantenedor': fila['mantenedor'][:255],
            'registro': fila['registro'],
            'actualizacion': fila['actualizacion'],
            'archivo_fuente': archivo[:255],
            'url_fuente': URL_VARIEDADES_SAG,
            'en_registro_vigente': True,
        }
        actual = existentes.get(clave)
        if actual is None:
            nuevas.append(VariedadSAG(especie=clave[0], variedad=clave[1], **datos))
            resultado.creadas += 1
            continue
        cambios = [campo for campo, valor in datos.items() if getattr(actual, campo) != valor]
        if cambios:
            for campo in cambios:
                setattr(actual, campo, datos[campo])
            actual.save()
            resultado.actualizadas += 1
        else:
            resultado.sin_cambios += 1
    VariedadSAG.objects.bulk_create(nuevas, batch_size=200)
    retiradas = [v.pk for clave, v in existentes.items() if v.en_registro_vigente and clave not in vistas]
    resultado.retiradas = VariedadSAG.objects.filter(pk__in=retiradas).update(en_registro_vigente=False)
    return resultado
