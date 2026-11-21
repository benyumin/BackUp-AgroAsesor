"""Carga INICIAL del catálogo desde data/*.csv (cultivos, zona, insumos de referencia…).

Uso:
    python manage.py cargar_catalogo_inicial            # carga data/ solo si la base aún no tiene cultivos
    python manage.py cargar_catalogo_inicial --simular  # muestra qué haría, sin guardar
    python manage.py cargar_catalogo_inicial --forzar   # vuelve a pasar por los CSV aunque ya haya cultivos

Cómo decide:
- Si la base ya tiene cultivos, no hace nada: reiniciar Django o el contenedor no vuelve a importar y un registro
  borrado a propósito no reaparece. (Antes lo decidía una huella de los CSV guardada en una tabla de control,
  ``CargaCatalogo``; se eliminó para no mantener una tabla solo para eso.)
- Si no tiene cultivos, o con ``--forzar``, ejecuta ``importar_catalogo <carpeta> --aplicar`` (atómico).

Garantías (las da importar_catalogo sin --actualizar):
- No duplica: un registro con el mismo nombre (y tipo, en insumos) se deja como está.
- No sobrescribe: los registros existentes, incluidos los editados o creados por personas, no se modifican.
- No elimina nada.
"""

from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.management.commands.importar_catalogo import CSV_SECTIONS
from core.models import Cultivo


def archivos_catalogo(carpeta):
    return sorted(p for p in Path(carpeta).glob('*.csv') if p.stem.casefold() in CSV_SECTIONS)


class Command(BaseCommand):
    help = 'Carga el catálogo inicial (data/*.csv) solo si la base aún no tiene cultivos.'

    def add_arguments(self, parser):
        parser.add_argument('carpeta', nargs='?', default=str(Path(settings.BASE_DIR) / 'data'))
        parser.add_argument('--forzar', action='store_true', help='Vuelve a importar aunque la base ya tenga cultivos.')
        parser.add_argument('--simular', action='store_true', help='Valida y muestra el resultado sin guardar nada.')

    def handle(self, *args, **options):
        carpeta = Path(options['carpeta'])
        if not carpeta.is_dir():
            raise CommandError(f'No existe la carpeta {carpeta}.')
        if not archivos_catalogo(carpeta):
            raise CommandError(f'La carpeta {carpeta} no contiene CSV reconocidos.')
        if Cultivo.objects.exists() and not options['forzar']:
            self.stdout.write('El catálogo ya tiene datos: no se importa nada (usa --forzar para revisar los CSV de nuevo).')
            return

        salida = StringIO()
        argumentos = [str(carpeta)] + ([] if options['simular'] else ['--aplicar'])
        with transaction.atomic():
            call_command('importar_catalogo', *argumentos, stdout=salida)
        self.stdout.write(salida.getvalue().rstrip())
        if options['simular']:
            self.stdout.write(self.style.WARNING('Simulación: no se guardó nada.'))
        else:
            self.stdout.write(self.style.SUCCESS('Catálogo inicial cargado.'))
