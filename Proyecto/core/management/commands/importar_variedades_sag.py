"""Importa el Registro de variedades aptas para certificación del SAG (XLSX oficial).

Uso:
    python manage.py importar_variedades_sag "data/sag/registro_de_variedades_aptas_para_certificacion_ABRIL 2026.xlsx"
    ... --aplicar   # guarda

Fuente: https://www.sag.gob.cl/content/registro-de-variedades-aptas-para-certificacion

- Solo guarda las especies que corresponden a un cultivo de la aplicación; las demás se informan y se omiten.
- Sin ``--aplicar`` solo muestra qué haría. Con ``--aplicar`` la importación es atómica.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.sag import ErrorListaSAG
from core.sag_variedades import importar_variedades, leer_registro_variedades


class _Simulacion(Exception):
    pass


class Command(BaseCommand):
    help = 'Importa las variedades del registro SAG para los cultivos de la aplicación (XLSX).'

    def add_arguments(self, parser):
        parser.add_argument('archivo', help='Ruta del XLSX descargado desde el sitio del SAG.')
        parser.add_argument('--aplicar', action='store_true', help='Guarda los cambios (sin esto es una simulación).')

    def handle(self, *args, **options):
        ruta = Path(options['archivo'])
        if not ruta.is_file():
            raise CommandError(f'No existe el archivo {ruta}.')
        try:
            lectura = leer_registro_variedades(ruta)
        except ErrorListaSAG as exc:
            raise CommandError(str(exc))
        self.stdout.write(f'Archivo: {ruta.name}')
        for hoja, cantidad in lectura.por_hoja.items():
            self.stdout.write(f'Hoja «{hoja}»: {cantidad} variedades')

        try:
            with transaction.atomic():
                resultado = importar_variedades(lectura, ruta.name)
                if not options['aplicar']:
                    raise _Simulacion
        except _Simulacion:
            pass

        self.stdout.write(f'Variedades nuevas: {resultado.creadas}')
        self.stdout.write(f'Actualizadas: {resultado.actualizadas} · sin cambios: {resultado.sin_cambios}')
        self.stdout.write(f'Repetidas dentro del archivo (se importan una vez): {resultado.repetidas}')
        self.stdout.write(f'Ya no aparecen en el registro (se conservan, marcadas): {resultado.retiradas}')
        for cultivo, cantidad in sorted(resultado.por_cultivo.items(), key=lambda item: -item[1]):
            self.stdout.write(f'  · {cultivo}: {cantidad}')
        if resultado.especies_omitidas:
            omitidas = ', '.join(f'{especie} ({n})' for especie, n in sorted(resultado.especies_omitidas.items()))
            self.stdout.write(f'Especies sin cultivo en la aplicación (no se importan): {omitidas}')
        if options['aplicar']:
            self.stdout.write(self.style.SUCCESS('Importación guardada.'))
        else:
            self.stdout.write(self.style.WARNING('Simulación: no se guardó nada. Usa --aplicar para guardar.'))
