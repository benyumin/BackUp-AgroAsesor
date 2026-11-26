"""Importa los insecticidas, herbicidas y fungicidas vigentes del SAG (planillas XLSX oficiales).

Uso:
    python manage.py importar_plaguicidas_sag "data/sag/Planilla general plaguicidas vigentes (26-10-26).xlsx" \
        "data/sag/Plaguicidas Autorizados - resumen al 01-10-2025.xlsx"             # simulación
    ... --aplicar                                                                   # guarda

Fuente: https://www.sag.gob.cl/ambitos-de-accion/listado-de-plaguicidas-autorizados-prohibidos-restringidos-y-cancelados-etiquetas-y-hds

- El primer archivo es la planilla general de vigentes (el SAG la publica en .xls: ábrela en Excel y guárdala como
  .xlsx). El segundo es la planilla resumida, que aporta la aptitud y la formulación.
- Sin ``--aplicar`` solo muestra qué haría. Con ``--aplicar`` la importación es atómica.
- No inventa dosis, cultivos ni carencia. Ver reglas completas en ``core/sag_plaguicidas.py``.
"""

from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.sag import ErrorListaSAG
from core.sag_plaguicidas import importar_planillas, leer_planillas


class _Simulacion(Exception):
    pass


class Command(BaseCommand):
    help = 'Importa los insecticidas, herbicidas y fungicidas vigentes del SAG (planillas XLSX).'

    def add_arguments(self, parser):
        parser.add_argument('vigentes', help='XLSX de la planilla general de plaguicidas vigentes.')
        parser.add_argument('resumen', help='XLSX de la planilla resumida (aporta la aptitud y la formulación).')
        parser.add_argument('--fecha', help='Fecha de la planilla de vigentes (dd-mm-aaaa) si el nombre no la trae.')
        parser.add_argument('--aplicar', action='store_true', help='Guarda los cambios (sin esto es una simulación).')

    def handle(self, *args, **options):
        rutas = [Path(options['vigentes']), Path(options['resumen'])]
        for ruta in rutas:
            if not ruta.is_file():
                raise CommandError(f'No existe el archivo {ruta}.')
        fecha = None
        if options['fecha']:
            try:
                fecha = datetime.strptime(options['fecha'], '%d-%m-%Y').date()
            except ValueError:
                raise CommandError('La fecha debe tener la forma dd-mm-aaaa.')
        try:
            planilla = leer_planillas(*rutas, fecha=fecha)
        except ErrorListaSAG as exc:
            raise CommandError(str(exc))
        self.stdout.write(f'Planilla de vigentes: {rutas[0].name} (fecha {planilla.fecha:%d-%m-%Y})')
        self.stdout.write(f'Productos vigentes: {planilla.vigentes}')
        self.stdout.write(f'Insecticidas, herbicidas y fungicidas: {len(planilla.registros)}')
        self.stdout.write(f'Otras aptitudes (no se importan): {planilla.otras_aptitudes}')
        self.stdout.write(f'Sin aptitud en la planilla resumida (no se importan): {len(planilla.sin_aptitud)}')
        for numero, nombre in planilla.sin_aptitud:
            self.stdout.write(f'    N° SAG {numero} · {nombre}')

        try:
            with transaction.atomic():
                resultado = importar_planillas(planilla, rutas[0].name)
                if not options['aplicar']:
                    raise _Simulacion
        except _Simulacion:
            pass

        self.stdout.write(f'Insumos nuevos: {resultado.creados}')
        self.stdout.write(f'Vinculados a insumos que ya estaban (mismo N° SAG): {resultado.vinculados_a_existentes}')
        self.stdout.write(f'Registros actualizados: {resultado.actualizados} · sin cambios: {resultado.sin_cambios}')
        self.stdout.write(f'Ya no están vigentes (se conservan, marcados): {resultado.retirados}')
        for tipo, cantidad in sorted(resultado.por_tipo.items(), key=lambda item: -item[1]):
            self.stdout.write(f'  · {tipo}: {cantidad}')
        if options['aplicar']:
            self.stdout.write(self.style.SUCCESS('Importación guardada.'))
        else:
            self.stdout.write(self.style.WARNING('Simulación: no se guardó nada. Usa --aplicar para guardar.'))
