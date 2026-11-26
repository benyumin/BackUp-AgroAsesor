"""Importa la lista SAG de fertilizantes y bioestimulantes autorizados para agricultura orgánica (XLSX oficial).

Uso:
    python manage.py importar_lista_sag "data/sag/39. Fertilizantes y bioestimulantes_09112026.xlsx"            # simulación
    python manage.py importar_lista_sag "data/sag/39. Fertilizantes y bioestimulantes_09112026.xlsx" --aplicar  # guarda

Fuente: https://www.sag.gob.cl/content/lista-de-fertilizantes-y-bioestimulantes-autorizados-para-agricultura-organica

- Sin ``--aplicar`` solo lee el archivo, valida las columnas y muestra qué haría (no escribe en la base de datos).
- Con ``--aplicar`` la importación es atómica: si algo falla, no se guarda nada.
- No inventa dosis ni marca nada como verificado. Ver reglas completas en ``core/sag.py``.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import RegistroListaSAG
from core.sag import ErrorListaSAG, URL_LISTA_SAG, importar_lista, leer_lista_sag


class _Simulacion(Exception):
    pass


class Command(BaseCommand):
    help = 'Importa la lista SAG de fertilizantes y bioestimulantes para agricultura orgánica (XLSX).'

    def add_arguments(self, parser):
        parser.add_argument('archivo', help='Ruta del XLSX descargado desde el sitio del SAG.')
        parser.add_argument('--aplicar', action='store_true', help='Guarda los cambios (sin esto es una simulación).')
        parser.add_argument('--url', default=URL_LISTA_SAG, help='Página oficial de donde se descargó el archivo.')

    def handle(self, *args, **options):
        ruta = Path(options['archivo'])
        if not ruta.is_file():
            raise CommandError(f'No existe el archivo {ruta}.')
        try:
            lista = leer_lista_sag(ruta)
        except ErrorListaSAG as exc:
            raise CommandError(str(exc))
        self.stdout.write(f'Archivo: {ruta.name}')
        self.stdout.write(f'Fecha de actualización de la lista: {lista.fecha_lista:%d-%m-%Y}')
        self.stdout.write(f'Columnas reconocidas: {", ".join(lista.columnas)}')
        self.stdout.write(f'Registros en la hoja: {len(lista.registros)} (filas vacías descartadas: {lista.filas_descartadas})')

        try:
            with transaction.atomic():
                resultado = importar_lista(lista, ruta.name, options['url'])
                if not options['aplicar']:
                    raise _Simulacion
        except _Simulacion:
            pass

        etiquetas = dict(RegistroListaSAG.Clasificacion.choices)
        self.stdout.write(f'Repetidos dentro del archivo (se importan una vez): {resultado.repetidos_en_archivo}')
        self.stdout.write(f'Insumos nuevos: {resultado.creados}')
        self.stdout.write(f'Vinculados a insumos que ya estaban en el catálogo: {resultado.vinculados_a_existentes}')
        self.stdout.write(f'Registros actualizados: {resultado.actualizados} · sin cambios: {resultado.sin_cambios}')
        self.stdout.write(f'Ya no aparecen en la lista (se conservan, marcados): {resultado.retirados}')
        for clave, cantidad in sorted(resultado.por_clasificacion.items(), key=lambda item: -item[1]):
            self.stdout.write(f'  · {etiquetas[clave]}: {cantidad}')
        if options['aplicar']:
            self.stdout.write(self.style.SUCCESS('Importación guardada.'))
        else:
            self.stdout.write(self.style.WARNING('Simulación: no se guardó nada. Usa --aplicar para guardar.'))
