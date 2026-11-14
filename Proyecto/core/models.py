from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal
import math
from datetime import date, timedelta
from .geometry import (
    polygon_area,
    polygon_perimeter,
    polygon_geojson,
    coordinates_from_geojson,
    polygon_contains,
    polygons_overlap,
)


class Zona(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre


class Cultivo(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    descripcion = models.TextField(blank=True)
    rendimiento_ton_ha = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    densidad_siembra = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unidad_siembra = models.CharField(max_length=20, default='kg')
    rendimiento_fuente = models.CharField(max_length=255, blank=True)
    rendimiento_url = models.URLField(blank=True)
    rendimiento_verificado = models.DateField(null=True, blank=True)
    densidad_fuente = models.CharField(max_length=255, blank=True)
    densidad_url = models.URLField(blank=True)
    densidad_verificada = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre


class ReferenciaAgronomica(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = 'draft', 'Borrador'
        VERIFICADA = 'verified', 'Verificada con fuente'
        RETIRADA = 'retired', 'Retirada'

    cultivo = models.ForeignKey(
        Cultivo,
        on_delete=models.PROTECT,
        related_name='referencias_agronomicas',
    )
    zona = models.ForeignKey(
        Zona,
        on_delete=models.PROTECT,
        related_name='referencias_agronomicas',
    )
    anio_referencia = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1900), MaxValueValidator(2100)],
        help_text='Déjalo vacío para una ventana recurrente anual.',
    )
    variedad = models.CharField(max_length=150, blank=True)
    nombre_cientifico = models.CharField(max_length=180, blank=True)
    familia_botanica = models.CharField(max_length=150, blank=True)
    tipo_cultivo = models.CharField(max_length=100, blank=True)
    condicion_siembra = models.TextField(blank=True)
    requerimientos = models.TextField(blank=True)
    profundidad_siembra = models.CharField(max_length=100, blank=True)
    etapas_fenologicas = models.TextField(blank=True)
    condiciones_crecimiento = models.TextField(blank=True)
    manejo_general = models.TextField(blank=True)
    indicadores_madurez = models.TextField(blank=True)
    consideraciones_cosecha = models.TextField(blank=True)
    plagas = models.TextField(blank=True)
    enfermedades = models.TextField(blank=True)
    malezas = models.TextField(blank=True)
    manejo_integrado = models.TextField(blank=True)
    siembra_inicio_mes = models.PositiveSmallIntegerField(null=True, blank=True)
    siembra_inicio_dia = models.PositiveSmallIntegerField(null=True, blank=True)
    siembra_fin_mes = models.PositiveSmallIntegerField(null=True, blank=True)
    siembra_fin_dia = models.PositiveSmallIntegerField(null=True, blank=True)
    cosecha_inicio_mes = models.PositiveSmallIntegerField(null=True, blank=True)
    cosecha_inicio_dia = models.PositiveSmallIntegerField(null=True, blank=True)
    cosecha_fin_mes = models.PositiveSmallIntegerField(null=True, blank=True)
    cosecha_fin_dia = models.PositiveSmallIntegerField(null=True, blank=True)
    duracion_ciclo_dias = models.PositiveSmallIntegerField(null=True, blank=True)
    rendimiento_ton_ha = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    institucion_fuente = models.CharField(max_length=255, blank=True)
    documento_fuente = models.CharField(max_length=255, blank=True)
    url_fuente = models.URLField(blank=True)
    fecha_consulta_fuente = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=12,
        choices=Estado.choices,
        default=Estado.BORRADOR,
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['cultivo__nombre', 'zona__nombre', 'variedad', 'pk']
        constraints = [
            models.UniqueConstraint(
                'cultivo',
                'zona',
                'anio_referencia',
                models.functions.Lower('variedad'),
                name='uniq_agro_ref_crop_zone_variety',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for prefix, label in (
            ('siembra', 'siembra'),
            ('cosecha', 'cosecha'),
        ):
            start_month = getattr(self, f'{prefix}_inicio_mes')
            start_day = getattr(self, f'{prefix}_inicio_dia')
            end_month = getattr(self, f'{prefix}_fin_mes')
            end_day = getattr(self, f'{prefix}_fin_dia')
            values = (start_month, start_day, end_month, end_day)
            if any(value is not None for value in values) and not all(value is not None for value in values):
                errors[f'{prefix}_inicio_mes'] = f'Completa las fechas de inicio y término de la ventana de {label}.'
                continue
            if all(value is not None for value in values):
                try:
                    date(2000, start_month, start_day)
                    date(2000, end_month, end_day)
                except ValueError:
                    errors[f'{prefix}_inicio_mes'] = f'La ventana de {label} contiene una fecha inválida.'

        duplicates = (
            type(self)
            .objects.filter(
                cultivo_id=self.cultivo_id,
                zona_id=self.zona_id,
                anio_referencia=self.anio_referencia,
                variedad__iexact=self.variedad.strip(),
            )
            .exclude(pk=self.pk)
        )
        if self.cultivo_id and self.zona_id and duplicates.exists():
            errors['variedad'] = 'Ya existe una referencia para este cultivo, zona y variedad.'
        if self.duracion_ciclo_dias == 0:
            errors['duracion_ciclo_dias'] = 'La duración del ciclo debe ser mayor que cero.'
        if self.rendimiento_ton_ha is not None and self.rendimiento_ton_ha <= 0:
            errors['rendimiento_ton_ha'] = 'El rendimiento debe ser mayor que cero.'
        if self.estado == self.Estado.VERIFICADA and not all(
            (
                self.institucion_fuente,
                self.documento_fuente,
                self.url_fuente,
                self.fecha_consulta_fuente,
            )
        ):
            errors['estado'] = (
                'Para verificar esta referencia registra institución, documento, ' 'URL y fecha de consulta.'
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.variedad = self.variedad.strip()
        return super().save(*args, **kwargs)

    @property
    def tiene_fuente_verificada(self):
        return self.estado == self.Estado.VERIFICADA and all(
            (
                self.institucion_fuente,
                self.documento_fuente,
                self.url_fuente,
                self.fecha_consulta_fuente,
            )
        )

    def __str__(self):
        context = f' · {self.variedad}' if self.variedad else ''
        return f'{self.cultivo} · {self.zona}{context}'


class TipoInsumo(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.nombre


class ProblemaAgricola(models.Model):
    class Tipo(models.TextChoices):
        PLAGA = 'pest', 'Plaga'
        ENFERMEDAD = 'disease', 'Enfermedad'
        MALEZA = 'weed', 'Maleza'
        NUTRICIONAL = 'nutrition', 'Problema nutricional'
        OTRO = 'other', 'Otro problema agrícola'

    nombre = models.CharField(max_length=150, unique=True)
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.OTRO)
    descripcion = models.TextField(blank=True)
    sintomas = models.TextField(blank=True)
    fuente_nombre = models.CharField(max_length=255, blank=True)
    fuente_url = models.URLField(blank=True)
    cultivos = models.ManyToManyField(
        Cultivo,
        blank=True,
        related_name='problemas_agricolas',
    )
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre', 'pk']

    def __str__(self):
        return self.nombre


class Insumo(models.Model):
    ESTADO_VERIFICACION = [
        ('unverified', 'Información no verificada'),
        ('verified', 'Verificado con fuente'),
        ('restricted', 'Restringido'),
        ('cancelled', 'Registro cancelado'),
    ]
    tipo = models.ForeignKey(TipoInsumo, on_delete=models.PROTECT, related_name='insumos')
    cultivo = models.ForeignKey(Cultivo, on_delete=models.SET_NULL, null=True, blank=True, related_name='insumos')
    zona = models.ForeignKey(Zona, on_delete=models.SET_NULL, null=True, blank=True, related_name='insumos')
    problemas = models.ManyToManyField(
        ProblemaAgricola,
        blank=True,
        related_name='insumos',
    )
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    dosis_hectarea = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    unidad_dosis = models.CharField(max_length=50, blank=True)
    dosis_maxima_hectarea = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.001'))],
    )
    unidad_dosis_maxima = models.CharField(max_length=50, blank=True)
    vigente = models.BooleanField(default=True)
    vigencia_desde = models.DateField(null=True, blank=True)
    vigencia_hasta = models.DateField(null=True, blank=True)
    fabricante = models.CharField(max_length=150, blank=True)
    estado_verificacion = models.CharField(max_length=12, choices=ESTADO_VERIFICACION, default='unverified')
    fuente_nombre = models.CharField(max_length=255, blank=True)
    fuente_url = models.URLField(blank=True)
    fecha_verificacion = models.DateField(null=True, blank=True)
    dosis_verificada = models.BooleanField(default=False)
    dosis_fuente = models.CharField(max_length=255, blank=True)
    etiqueta_url = models.URLField(blank=True)
    hoja_seguridad_url = models.URLField(blank=True)
    ingrediente_activo = models.CharField(max_length=255, blank=True)
    concentracion = models.CharField(max_length=100, blank=True)
    formulacion = models.CharField(max_length=100, blank=True)
    numero_autorizacion_sag = models.CharField(max_length=100, blank=True)
    cultivos_autorizados = models.JSONField(default=list, blank=True)
    objetivos_control = models.JSONField(default=list, blank=True)
    restricciones = models.TextField(blank=True)
    modo_aplicacion = models.TextField(blank=True)
    periodo_carencia = models.CharField(max_length=100, blank=True)
    tiempo_reingreso = models.CharField(max_length=100, blank=True)
    precauciones_ambientales = models.TextField(blank=True)
    advertencia_polinizadores = models.TextField(blank=True)
    presentacion_cantidad = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(Decimal('0.001'))]
    )
    presentacion_unidad = models.CharField(max_length=50, blank=True)
    composicion_nutricional = models.CharField(max_length=255, blank=True)
    nitrogeno_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
    )
    fosforo_p2o5_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
    )
    potasio_k2o_pct = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
    )
    variedad_semilla = models.CharField(max_length=150, blank=True)
    tipo_semilla = models.CharField(max_length=100, blank=True)
    plantas_objetivo_m2 = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(Decimal('0.001'))]
    )
    peso_mil_semillas_g = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(Decimal('0.001'))]
    )
    germinacion_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('100'))],
    )
    pureza_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('100'))],
    )

    def clean(self):
        super().clean()
        errors = {}
        if self.dosis_hectarea is not None and self.dosis_hectarea <= 0:
            errors['dosis_hectarea'] = 'La dosis debe ser mayor que cero.'
        if self.dosis_maxima_hectarea is not None:
            if not self.unidad_dosis_maxima:
                errors['unidad_dosis_maxima'] = 'Indica la unidad de la dosis máxima.'
            if self.unidad_dosis and self.unidad_dosis_maxima != self.unidad_dosis:
                errors['unidad_dosis_maxima'] = 'La unidad de la dosis máxima debe coincidir con la dosis registrada.'
            if self.dosis_hectarea is not None and self.dosis_hectarea > self.dosis_maxima_hectarea:
                errors['dosis_maxima_hectarea'] = 'La dosis máxima no puede ser menor que la dosis registrada.'
            if not (self.dosis_verificada and self.dosis_fuente and self.fuente_url and self.fecha_verificacion):
                errors['dosis_maxima_hectarea'] = (
                    'Verifica la dosis máxima con una fuente, URL y fecha antes de registrarla.'
                )
        if self.vigencia_desde and self.vigencia_hasta and self.vigencia_hasta < self.vigencia_desde:
            errors['vigencia_hasta'] = 'La fecha final de vigencia no puede ser anterior a la inicial.'
        if self.estado_verificacion == 'verified':
            if not self.fuente_nombre or not self.fuente_url or not self.fecha_verificacion:
                errors['estado_verificacion'] = (
                    'Registra una fuente y fecha de verificación antes de marcar el producto como verificado.'
                )
            if self.es_fitosanitario and (
                not self.numero_autorizacion_sag
                or not self.etiqueta_url
                or not isinstance(self.cultivos_autorizados, list)
                or not self.cultivos_autorizados
                or not isinstance(self.objetivos_control, list)
                or not self.objetivos_control
            ):
                errors['estado_verificacion'] = (
                    'Para verificar un plaguicida registra autorización, etiqueta, cultivo y objetivo de control.'
                )
        if self.dosis_verificada:
            if not self.dosis_hectarea or self.dosis_hectarea <= 0 or not self.unidad_dosis:
                errors['dosis_verificada'] = 'Registra una dosis positiva y su unidad antes de verificarla.'
            if not self.dosis_fuente or not self.fuente_url or not self.fecha_verificacion:
                errors['dosis_verificada'] = 'Registra la fuente y fecha de verificación de la dosis.'
            if self.es_fitosanitario and self.unidad_dosis not in {'kg/ha', 'g/ha', 'L/ha', 'mL/ha'}:
                errors['unidad_dosis'] = 'La dosis fitosanitaria debe expresarse por hectárea en kg, g, L o mL.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.nombre

    @property
    def es_fitosanitario(self):
        return self.tipo.nombre.casefold() in {'herbicida', 'fungicida', 'insecticida', 'acaricida', 'plaguicida'}

    @property
    def dosis_con_fuente(self):
        return (
            self.vigente_en
            and self.estado_verificacion == 'verified'
            and self.dosis_verificada
            and self.dosis_hectarea is not None
            and self.dosis_hectarea > 0
            and bool(self.dosis_fuente)
            and bool(self.fuente_url)
            and bool(self.fecha_verificacion)
        )

    @property
    def uso_verificado(self):
        return (
            self.es_fitosanitario
            and self.dosis_con_fuente
            and bool(self.etiqueta_url)
            and bool(self.numero_autorizacion_sag)
            and bool(self.cultivo_id)
            and isinstance(self.cultivos_autorizados, list)
            and self.cultivo.nombre in self.cultivos_autorizados
            and isinstance(self.objetivos_control, list)
            and bool(self.objetivos_control)
            and self.unidad_dosis in {'kg/ha', 'g/ha', 'L/ha', 'mL/ha'}
        )

    @property
    def vigente_en(self):
        today = timezone.localdate()
        return (
            self.vigente
            and (self.vigencia_desde is None or self.vigencia_desde <= today)
            and (self.vigencia_hasta is None or self.vigencia_hasta >= today)
        )


class Agricultor(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='agricultor')
    nombre = models.CharField(max_length=150)
    # Opcional: si se informa, se valida (módulo 11) y es único. NULL permite varias cuentas sin RUT.
    rut = models.CharField(max_length=12, unique=True, null=True, blank=True)
    telefono = models.CharField(max_length=20)
    comuna = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre


class AceptacionPoliticas(models.Model):
    VERSION_ACTUAL = '1.0'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='aceptaciones_politicas',
    )
    version = models.CharField(max_length=20)
    aceptada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-aceptada_en', '-pk']
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'version'],
                name='aceptacion_politicas_usuario_version_unica',
            ),
        ]

    def __str__(self):
        return f'{self.usuario} · políticas {self.version}'


class Predio(models.Model):
    propietario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='predios')
    nombre = models.CharField(max_length=150)
    ubicacion = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True)
    observaciones = models.TextField(blank=True)
    superficie_ha = models.DecimalField(
        max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))]
    )
    latitud = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-85), MaxValueValidator(85)])
    longitud = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])
    geojson = models.JSONField(null=True, blank=True)
    zona = models.ForeignKey(Zona, on_delete=models.PROTECT, null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre', 'pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(superficie_ha__gt=0), name='predio_superficie_positiva')
        ]

    def clean(self):
        super().clean()
        if (self.latitud is None) != (self.longitud is None):
            raise ValidationError({'latitud': 'Completa ambas coordenadas o deja las dos vacías.'})
        if self.geojson:
            try:
                polygon_area(coordinates_from_geojson(self.geojson))
            except ValidationError as exc:
                raise ValidationError({'geojson': exc.messages})
        if self.pk and self.superficie_ha is not None:
            total = self.potreros.aggregate(total=Sum('superficie_ha'))['total'] or 0
            if self.superficie_ha < total:
                raise ValidationError({'superficie_ha': 'No puede ser menor que la superficie total de sus potreros.'})

    @property
    def superficie_m2(self):
        if not self.geojson:
            return None
        return polygon_area(coordinates_from_geojson(self.geojson))

    @property
    def perimetro_m(self):
        if not self.geojson:
            return None
        return polygon_perimeter(coordinates_from_geojson(self.geojson))

    def __str__(self):
        return self.nombre


class PredioHistorialQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Los registros históricos no se pueden modificar.')

    def delete(self):
        raise ValidationError('Los registros históricos no se pueden eliminar.')


class PredioHistorial(models.Model):
    class Accion(models.TextChoices):
        CREADO = 'created', 'Predio creado'
        ACTUALIZADO = 'updated', 'Datos del predio modificados'
        PERIMETRO = 'perimeter_updated', 'Perímetro modificado'
        SUPERFICIE = 'area_updated', 'Superficie modificada'
        POTRERO_CREADO = 'plot_created', 'Potrero creado'
        POTRERO_ACTUALIZADO = 'plot_updated', 'Potrero modificado'
        CULTIVO_ASIGNADO = 'crop_assigned', 'Cultivo asignado por temporada'

    predio = models.ForeignKey(
        Predio,
        on_delete=models.PROTECT,
        related_name='historial',
    )
    potrero = models.ForeignKey(
        'Potrero',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='historial',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='historial_predios',
    )
    accion = models.CharField(max_length=32, choices=Accion.choices)
    resumen = models.TextField()
    creado = models.DateTimeField(auto_now_add=True)
    geometria_anterior = models.JSONField(null=True, blank=True)
    geometria_nueva = models.JSONField(null=True, blank=True)
    superficie_anterior_ha = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    superficie_nueva_ha = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    objects = PredioHistorialQuerySet.as_manager()

    class Meta:
        ordering = ['-creado', '-pk']
        indexes = [
            models.Index(fields=['predio', '-creado'], name='predio_historial_fecha'),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Los registros históricos no se pueden modificar.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Los registros históricos no se pueden eliminar.')

    def __str__(self):
        return f'{self.get_accion_display()} · {self.predio}'


class Potrero(models.Model):
    predio = models.ForeignKey(Predio, on_delete=models.PROTECT, related_name='potreros')
    nombre = models.CharField(max_length=150)
    cultivo = models.ForeignKey(Cultivo, on_delete=models.PROTECT, null=True, blank=True, related_name='potreros')
    variedad = models.CharField(max_length=150, blank=True)
    fecha_siembra = models.DateField(null=True, blank=True)
    fecha_cosecha = models.DateField(null=True, blank=True)
    superficie_ha = models.DecimalField(
        max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))]
    )
    perimetro = models.JSONField(default=list, blank=True)
    geojson = models.JSONField(null=True, blank=True)
    latitud = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-85), MaxValueValidator(85)])
    longitud = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre', 'pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(superficie_ha__gt=0), name='potrero_superficie_positiva')
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.perimetro:
            try:
                self.superficie_ha = (polygon_area(self.perimetro) / Decimal('10000')).quantize(Decimal('0.0001'))
            except ValidationError as exc:
                errors['perimetro'] = exc.messages
        elif self.perimetro != []:
            errors['perimetro'] = 'Usa una lista vacía cuando no haya perímetro.'
        if (self.latitud is None) != (self.longitud is None):
            errors['latitud'] = 'Completa ambas coordenadas o deja las dos vacías.'
        if any(v is not None and not math.isfinite(v) for v in (self.latitud, self.longitud)):
            errors['latitud'] = 'Las coordenadas deben ser finitas.'
        if self.fecha_siembra and self.fecha_cosecha and self.fecha_cosecha < self.fecha_siembra:
            errors['fecha_cosecha'] = 'La cosecha no puede ser anterior a la siembra.'
        if self.cultivo_id and not self.cultivo.activo:
            errors['cultivo'] = 'Selecciona un cultivo activo.'
        if self.predio_id and self.superficie_ha is not None:
            total = self.predio.potreros.exclude(pk=self.pk).aggregate(total=Sum('superficie_ha'))['total'] or 0
            if self.superficie_ha + total > self.predio.superficie_ha:
                errors['superficie_ha'] = 'Los potreros no pueden superar la superficie del predio.'
        if self.perimetro and self.predio_id:
            predio = self.predio
            if predio.geojson:
                try:
                    boundary = coordinates_from_geojson(predio.geojson)
                    if not polygon_contains(boundary, self.perimetro):
                        errors['perimetro'] = 'El perímetro del potrero debe quedar dentro del predio.'
                except ValidationError as exc:
                    errors['perimetro'] = exc.messages
            if not errors.get('perimetro'):
                for sibling in Potrero.objects.filter(predio=predio).exclude(pk=self.pk).exclude(perimetro=[]):
                    if polygons_overlap(self.perimetro, sibling.perimetro):
                        errors['perimetro'] = 'El potrero se superpone con otro potrero de este predio.'
                        break
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.geojson = polygon_geojson(self.perimetro)
        super().save(*args, **kwargs)

    @property
    def perimetro_m(self):
        return polygon_perimeter(self.perimetro) if self.perimetro else None

    @property
    def superficie_m2(self):
        return self.superficie_ha * Decimal('10000')

    def __str__(self):
        return f'{self.predio} · {self.nombre}'


class TemporadaCultivoQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError('Las temporadas de cultivo no se pueden eliminar.')


class TemporadaCultivo(models.Model):
    class Estado(models.TextChoices):
        PLANIFICADA = 'planned', 'Planificada'
        SEMBRADA = 'sown', 'Sembrada'
        COSECHADA = 'harvested', 'Cosechada'
        CERRADA = 'closed', 'Cerrada'

    potrero = models.ForeignKey(
        Potrero,
        on_delete=models.PROTECT,
        related_name='temporadas',
    )
    cultivo = models.ForeignKey(
        Cultivo,
        on_delete=models.PROTECT,
        related_name='temporadas',
    )
    variedad = models.CharField(max_length=150, blank=True)
    temporada_agricola = models.CharField(
        max_length=9,
        blank=True,
        help_text='Formato AAAA-AAAA, por ejemplo 2026-2027.',
    )
    fecha_siembra = models.DateField(null=True, blank=True)
    fecha_cosecha = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=12,
        choices=Estado.choices,
        default=Estado.PLANIFICADA,
    )
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    objects = TemporadaCultivoQuerySet.as_manager()

    class Meta:
        ordering = ['-fecha_siembra', '-creado', '-pk']
        indexes = [
            models.Index(fields=['potrero', '-fecha_siembra'], name='temporada_potrero_fecha'),
        ]

    def clean(self):
        super().clean()
        if self.fecha_siembra and self.fecha_cosecha and self.fecha_cosecha < self.fecha_siembra:
            raise ValidationError(
                {
                    'fecha_cosecha': 'La cosecha no puede ser anterior a la siembra.',
                }
            )
        if self.temporada_agricola:
            parts = self.temporada_agricola.split('-')
            if (
                len(parts) != 2
                or not all(part.isdigit() and len(part) == 4 for part in parts)
                or int(parts[1]) not in {int(parts[0]), int(parts[0]) + 1}
            ):
                raise ValidationError(
                    {
                        'temporada_agricola': 'Usa el formato AAAA-AAAA con años consecutivos, por ejemplo 2026-2027.',
                    }
                )
        if self.cultivo_id and not self.cultivo.activo and not self.pk:
            raise ValidationError({'cultivo': 'Selecciona un cultivo activo del catálogo.'})
        if self.estado == self.Estado.SEMBRADA and not self.fecha_siembra:
            raise ValidationError(
                {
                    'fecha_siembra': 'Registra la fecha de siembra para marcar la temporada como sembrada.',
                }
            )
        if self.estado == self.Estado.COSECHADA and not self.fecha_cosecha:
            raise ValidationError(
                {
                    'fecha_cosecha': 'Registra la fecha de cosecha para cerrar la temporada como cosechada.',
                }
            )

    def save(self, *args, **kwargs):
        self.variedad = self.variedad.strip()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Las temporadas de cultivo no se pueden eliminar.')

    def __str__(self):
        variety = f' · {self.variedad}' if self.variedad else ''
        return f'{self.cultivo}{variety} · {self.potrero}'


class CalculoAgricolaQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError('El historial de cálculos no se puede eliminar.')


class CalculoAgricola(models.Model):
    class Operacion(models.TextChoices):
        DOSIS = 'dose', 'Dosis'
        COBERTURA = 'coverage', 'Cobertura'
        POBLACION_SEMILLA = 'seed_population', 'Población de semillas'
        NUTRICION = 'fertilizer_nutrition', 'Nutrición'
        RENDIMIENTO = 'yield', 'Rendimiento'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calculos_agricolas',
    )
    potrero = models.ForeignKey(
        Potrero,
        on_delete=models.PROTECT,
        related_name='calculos',
    )
    operacion = models.CharField(max_length=24, choices=Operacion.choices)
    entrada = models.JSONField(default=dict)
    resultado = models.JSONField(default=dict)
    creado = models.DateTimeField(auto_now_add=True)
    objects = CalculoAgricolaQuerySet.as_manager()

    class Meta:
        ordering = ['-creado', '-pk']
        indexes = [
            models.Index(fields=['usuario', '-creado'], name='calculo_usuario_fecha'),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError('El historial de cálculos no se puede eliminar.')

    def __str__(self):
        return f'{self.get_operacion_display()} · {self.potrero} · {self.creado:%Y-%m-%d}'


class ActividadAgricola(models.Model):
    class Tipo(models.TextChoices):
        PREPARACION = 'preparacion', 'Preparación'
        SIEMBRA = 'siembra', 'Siembra'
        FERTILIZACION = 'fertilizacion', 'Fertilización'
        RIEGO = 'riego', 'Riego'
        MONITOREO = 'monitoreo', 'Monitoreo'
        CONTROL_FITOSANITARIO = 'control_fitosanitario', 'Control fitosanitario'
        COSECHA = 'cosecha', 'Cosecha'
        OTRA = 'otra', 'Otra'

    class Estado(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente'
        COMPLETADA = 'completada', 'Completada'
        CANCELADA = 'cancelada', 'Cancelada'

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='actividades_agricolas',
    )
    potrero = models.ForeignKey(
        Potrero,
        on_delete=models.CASCADE,
        related_name='actividades',
    )

    class Prioridad(models.TextChoices):
        ALTA = 'alta', 'Alta'
        MEDIA = 'media', 'Media'
        BAJA = 'baja', 'Baja'

    TIPOS_CON_INSUMO = {'siembra', 'fertilizacion', 'control_fitosanitario'}

    nombre = models.CharField(max_length=180)
    tipo = models.CharField(max_length=32, choices=Tipo.choices)
    fecha_programada = models.DateField()
    estado = models.CharField(
        max_length=12,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    prioridad = models.CharField(max_length=8, choices=Prioridad.choices, default=Prioridad.MEDIA)
    temporada = models.ForeignKey(
        'TemporadaCultivo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='actividades',
    )
    # Registro de aplicación (cuaderno de campo): qué se aplicó, cuánto, dónde y quién.
    insumo = models.ForeignKey(Insumo, on_delete=models.SET_NULL, null=True, blank=True, related_name='actividades')
    insumo_otro = models.CharField(max_length=150, blank=True)
    dosis = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(Decimal('0.001'))]
    )
    unidad_dosis = models.CharField(max_length=20, blank=True)
    superficie_ha = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal('0.0001'))]
    )
    objetivo = models.CharField(max_length=150, blank=True)
    responsable = models.CharField(max_length=150, blank=True)
    fecha_realizada = models.DateField(null=True, blank=True)
    condiciones_clima = models.CharField(max_length=255, blank=True)
    observaciones = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['fecha_programada', 'pk']
        indexes = [
            models.Index(
                fields=['usuario', 'estado', 'fecha_programada'],
                name='actividad_usuario_estado_fecha',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.usuario_id and self.potrero_id:
            if self.potrero.predio.propietario_id != self.usuario_id:
                errors['potrero'] = 'Selecciona un potrero de tu cuenta.'
        if self.temporada_id and self.potrero_id and self.temporada.potrero_id != self.potrero_id:
            errors['temporada'] = 'La temporada no corresponde al potrero seleccionado.'
        if self.superficie_ha is not None and self.potrero_id and self.superficie_ha > self.potrero.superficie_ha:
            errors['superficie_ha'] = f'La superficie no puede superar las {self.potrero.superficie_ha} ha del potrero.'
        if (self.dosis is None) != (not self.unidad_dosis):
            errors['unidad_dosis'] = 'Registra la dosis junto con su unidad.'
        if self.insumo_id and self.insumo_otro:
            errors['insumo_otro'] = 'Elige un producto del catálogo o escribe otro, no ambos.'
        if self.estado == self.Estado.COMPLETADA and self.fecha_realizada is None:
            errors['fecha_realizada'] = 'Indica la fecha en que se realizó la labor.'
        if self.fecha_realizada and self.fecha_realizada > timezone.localdate():
            errors['fecha_realizada'] = 'La fecha de realización no puede ser futura.'
        if (
            self.tipo == self.Tipo.CONTROL_FITOSANITARIO
            and self.estado == self.Estado.COMPLETADA
            and not ((self.insumo_id or self.insumo_otro) and self.dosis and self.responsable)
        ):
            errors['responsable'] = (
                'Para registrar una aplicación fitosanitaria realizada indica producto, dosis y responsable.'
            )
        if errors:
            raise ValidationError(errors)

    @property
    def producto_nombre(self):
        return self.insumo.nombre if self.insumo_id else self.insumo_otro

    @property
    def cantidad_total(self):
        if self.dosis is None or self.superficie_ha is None or not self.unidad_dosis.endswith('/ha'):
            return None
        return (self.dosis * self.superficie_ha).quantize(Decimal('0.001'))

    @property
    def unidad_total(self):
        return self.unidad_dosis.removesuffix('/ha') if self.unidad_dosis else ''

    @property
    def dias_carencia(self):
        if not self.insumo_id or not self.insumo.periodo_carencia:
            return None
        digits = ''
        for char in self.insumo.periodo_carencia.strip():
            if char.isdigit():
                digits += char
            elif digits:
                break
        return int(digits) if digits else None

    @property
    def fecha_fin_carencia(self):
        days = self.dias_carencia
        base = self.fecha_realizada or self.fecha_programada
        return base + timedelta(days=days) if days is not None and base else None

    @property
    def conflicto_carencia(self):
        """Cosecha registrada antes de que termine el período de carencia de la etiqueta."""
        end = self.fecha_fin_carencia
        harvest = (self.temporada.fecha_cosecha if self.temporada_id else None) or self.potrero.fecha_cosecha
        return bool(end and harvest and harvest < end)

    @property
    def vencida(self):
        return self.estado == self.Estado.PENDIENTE and self.fecha_programada < timezone.localdate()

    def __str__(self):
        return f'{self.nombre} · {self.potrero}'


class CargaCatalogo(models.Model):
    """Registro de cada carga controlada del catálogo inicial (data/*.csv).

    Guarda la huella SHA-256 de los CSV cargados: si los archivos no cambian, la carga no se repite al reiniciar.
    Solo se agrega un registro; nunca borra ni modifica datos del catálogo ni de los agricultores.
    """

    huella = models.CharField(max_length=64, db_index=True)
    carpeta = models.CharField(max_length=255)
    archivos = models.JSONField(default=list)
    resumen = models.TextField(blank=True)
    forzada = models.BooleanField(default=False)
    aplicada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-aplicada', '-pk']
        verbose_name = 'carga del catálogo inicial'
        verbose_name_plural = 'cargas del catálogo inicial'

    def __str__(self):
        return f'{self.carpeta} · {self.huella[:12]} · {self.aplicada:%d/%m/%Y %H:%M}'
