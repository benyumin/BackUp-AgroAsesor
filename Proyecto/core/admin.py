from django.contrib import admin

from .models import (
    AceptacionPoliticas,
    ActividadAgricola,
    CalculoAgricola,
    Cultivo,
    Insumo,
    TipoInsumo,
    Zona,
    Agricultor,
    Predio,
    PredioHistorial,
    Potrero,
    ReferenciaAgronomica,
    RegistroListaSAG,
    RegistroPlaguicidaSAG,
    TemporadaCultivo,
    VariedadSAG,
)


@admin.register(Zona)
class ZonaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'activo')
    search_fields = ('nombre',)


@admin.register(Cultivo)
class CultivoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'rendimiento_ton_ha', 'densidad_siembra', 'unidad_siembra', 'activo')
    list_filter = ('activo',)
    search_fields = ('nombre',)
    fieldsets = (
        (None, {'fields': ('nombre', 'descripcion', 'activo')}),
        (
            'Parámetros de referencia (no son recomendaciones)',
            {
                'fields': (
                    'rendimiento_ton_ha',
                    'rendimiento_fuente',
                    'rendimiento_url',
                    'rendimiento_verificado',
                    'densidad_siembra',
                    'unidad_siembra',
                    'densidad_fuente',
                    'densidad_url',
                    'densidad_verificada',
                )
            },
        ),
    )


@admin.register(ReferenciaAgronomica)
class ReferenciaAgronomicaAdmin(admin.ModelAdmin):
    list_display = (
        'cultivo',
        'zona',
        'variedad',
        'estado',
        'institucion_fuente',
        'fecha_consulta_fuente',
    )
    list_filter = ('estado', 'zona', 'cultivo')
    search_fields = (
        'cultivo__nombre',
        'zona__nombre',
        'variedad',
        'institucion_fuente',
        'documento_fuente',
    )
    fieldsets = (
        (
            'Cultivo y contexto',
            {
                'fields': (
                    'cultivo',
                    'zona',
                    'anio_referencia',
                    'variedad',
                    'nombre_cientifico',
                    'familia_botanica',
                    'tipo_cultivo',
                )
            },
        ),
        (
            'Calendario documentado',
            {
                'fields': (
                    ('siembra_inicio_mes', 'siembra_inicio_dia'),
                    ('siembra_fin_mes', 'siembra_fin_dia'),
                    ('cosecha_inicio_mes', 'cosecha_inicio_dia'),
                    ('cosecha_fin_mes', 'cosecha_fin_dia'),
                    'duracion_ciclo_dias',
                    'rendimiento_ton_ha',
                )
            },
        ),
        (
            'Siembra y desarrollo',
            {
                'fields': (
                    'condicion_siembra',
                    'requerimientos',
                    'profundidad_siembra',
                    'etapas_fenologicas',
                    'condiciones_crecimiento',
                    'manejo_general',
                )
            },
        ),
        (
            'Cosecha y sanidad',
            {
                'fields': (
                    'indicadores_madurez',
                    'consideraciones_cosecha',
                    'plagas',
                    'enfermedades',
                    'malezas',
                    'manejo_integrado',
                )
            },
        ),
        (
            'Fuente y verificación',
            {
                'fields': (
                    'estado',
                    'institucion_fuente',
                    'documento_fuente',
                    'url_fuente',
                    'fecha_consulta_fuente',
                )
            },
        ),
        ('Trazabilidad', {'fields': ('creado', 'actualizado')}),
    )
    readonly_fields = ('creado', 'actualizado')


@admin.register(TipoInsumo)
class TipoInsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre',)
    search_fields = ('nombre',)


@admin.register(RegistroListaSAG)
class RegistroListaSAGAdmin(admin.ModelAdmin):
    """Procedencia de los insumos importados de la lista SAG orgánica. Las columnas del archivo no se editan aquí:
    se corrigen reimportando la lista oficial (``importar_lista_sag``)."""

    list_display = ('nombre_comercial', 'fabricante', 'clasificacion', 'en_lista_vigente', 'fecha_lista')
    list_filter = ('clasificacion', 'en_lista_vigente', 'insumo_creado_por_importacion', 'fecha_lista')
    search_fields = ('nombre_comercial', 'fabricante', 'sustancia_activa', 'funcion')
    raw_id_fields = ('insumo',)
    readonly_fields = (
        'clave',
        'numero_lista',
        'nombre_comercial',
        'sustancia_activa',
        'fabricante',
        'funcion',
        'clasificacion',
        'fecha_autorizacion',
        'observaciones',
        'fecha_lista',
        'archivo_fuente',
        'url_fuente',
        'en_lista_vigente',
        'insumo_creado_por_importacion',
        'importado',
        'actualizado',
    )

    def has_add_permission(self, request):
        return False


@admin.register(RegistroPlaguicidaSAG)
class RegistroPlaguicidaSAGAdmin(admin.ModelAdmin):
    """Procedencia de los plaguicidas importados del SAG. Se corrigen reimportando (``importar_plaguicidas_sag``)."""

    list_display = ('nombre_comercial', 'numero_sag', 'aptitud', 'titular', 'en_planilla_vigente', 'fecha_planilla')
    list_filter = ('en_planilla_vigente', 'insumo_creado_por_importacion', 'fecha_planilla')
    search_fields = ('nombre_comercial', 'numero_sag', 'titular', 'sustancias_activas', 'aptitud')
    raw_id_fields = ('insumo',)
    readonly_fields = [f.name for f in RegistroPlaguicidaSAG._meta.fields if f.name not in {'id', 'insumo'}]

    def has_add_permission(self, request):
        return False


@admin.register(VariedadSAG)
class VariedadSAGAdmin(admin.ModelAdmin):
    """Variedades del registro SAG. Se corrigen reimportando (``importar_variedades_sag``)."""

    list_display = ('variedad', 'cultivo', 'especie', 'registro', 'mantenedor', 'en_registro_vigente')
    list_filter = ('registro', 'en_registro_vigente', 'cultivo')
    search_fields = ('variedad', 'especie', 'mantenedor')
    readonly_fields = [f.name for f in VariedadSAG._meta.fields if f.name != 'id']

    def has_add_permission(self, request):
        return False


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo', 'cultivo', 'estado_verificacion', 'fecha_verificacion', 'vigencia_actual')
    list_filter = ('tipo', 'estado_verificacion', 'vigente', 'dosis_verificada')
    search_fields = ('nombre', 'fabricante', 'numero_autorizacion_sag', 'ingrediente_activo')
    fieldsets = (
        (None, {'fields': ('nombre', 'tipo', 'cultivo', 'zona', 'descripcion', 'fabricante', 'vigente')}),
        ('Vigencia', {'fields': ('vigencia_desde', 'vigencia_hasta')}),
        (
            'Verificación y trazabilidad',
            {
                'fields': (
                    'estado_verificacion',
                    'fuente_nombre',
                    'fuente_url',
                    'fecha_verificacion',
                    'dosis_hectarea',
                    'unidad_dosis',
                    'dosis_maxima_hectarea',
                    'unidad_dosis_maxima',
                    'dosis_verificada',
                    'dosis_fuente',
                )
            },
        ),
        (
            'Documentación y plaguicidas',
            {
                'fields': (
                    'numero_autorizacion_sag',
                    'ingrediente_activo',
                    'concentracion',
                    'formulacion',
                    'cultivos_autorizados',
                    'objetivos_control',
                    'etiqueta_url',
                    'hoja_seguridad_url',
                    'modo_aplicacion',
                    'restricciones',
                    'periodo_carencia',
                    'tiempo_reingreso',
                    'precauciones_ambientales',
                    'advertencia_polinizadores',
                )
            },
        ),
        (
            'Composición y presentación de fertilizantes',
            {
                'fields': (
                    'composicion_nutricional',
                    'nitrogeno_pct',
                    'fosforo_p2o5_pct',
                    'potasio_k2o_pct',
                    'presentacion_cantidad',
                    'presentacion_unidad',
                )
            },
        ),
        (
            'Datos documentados de semillas',
            {
                'fields': (
                    'variedad_semilla',
                    'tipo_semilla',
                    'plantas_objetivo_m2',
                    'peso_mil_semillas_g',
                    'germinacion_pct',
                    'pureza_pct',
                )
            },
        ),
    )

    @admin.display(boolean=True, description='Vigente hoy')
    def vigencia_actual(self, obj):
        return obj.vigente_en


@admin.register(Agricultor)
class AgricultorAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'comuna', 'user')
    list_select_related = ('user',)
    search_fields = ('user__first_name', 'user__last_name', 'user__email')

    @admin.display(description='Nombre', ordering='user__first_name')
    def nombre(self, obj):
        return obj.user.get_full_name() or obj.user.get_username()


@admin.register(AceptacionPoliticas)
class AceptacionPoliticasAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'version', 'aceptada_en')
    search_fields = ('usuario__username', 'usuario__email')
    readonly_fields = ('usuario', 'version', 'aceptada_en')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Predio)
class PredioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'propietario', 'superficie_ha', 'zona', 'archivado_en')
    list_filter = (('archivado_en', admin.EmptyFieldListFilter),)
    search_fields = ('nombre', 'ubicacion')

    def get_queryset(self, request):
        # El administrador también ve los predios archivados (la plataforma los oculta).
        return Predio.todos.select_related('propietario', 'zona')


@admin.register(PredioHistorial)
class PredioHistorialAdmin(admin.ModelAdmin):
    list_display = ('predio', 'potrero', 'accion', 'usuario', 'creado')
    list_filter = ('accion', 'creado')
    search_fields = ('predio__nombre', 'resumen', 'usuario__email')
    readonly_fields = (
        'predio',
        'potrero',
        'usuario',
        'accion',
        'resumen',
        'creado',
        'superficie_anterior_ha',
        'superficie_nueva_ha',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Potrero)
class PotreroAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'predio', 'cultivo', 'superficie_ha', 'archivado_en')
    list_filter = ('cultivo', ('archivado_en', admin.EmptyFieldListFilter))
    search_fields = ('nombre', 'predio__nombre')

    def get_queryset(self, request):
        return Potrero.todos.select_related('predio', 'cultivo')


@admin.register(TemporadaCultivo)
class TemporadaCultivoAdmin(admin.ModelAdmin):
    list_display = (
        'cultivo',
        'variedad',
        'temporada_agricola',
        'potrero',
        'fecha_siembra',
        'fecha_cosecha',
        'estado',
    )
    list_filter = ('estado', 'cultivo', 'temporada_agricola')
    search_fields = (
        'cultivo__nombre',
        'variedad',
        'potrero__nombre',
        'potrero__predio__nombre',
    )
    readonly_fields = ('creado', 'actualizado')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CalculoAgricola)
class CalculoAgricolaAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'potrero', 'operacion', 'creado')
    list_filter = ('operacion', 'creado')
    search_fields = (
        'usuario__username',
        'usuario__email',
        'potrero__nombre',
        'potrero__predio__nombre',
    )
    readonly_fields = ('usuario', 'potrero', 'operacion', 'entrada', 'resultado', 'creado')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ActividadAgricola)
class ActividadAgricolaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'tipo', 'potrero', 'usuario', 'fecha_programada', 'estado')
    list_filter = ('tipo', 'estado', 'fecha_programada')
    search_fields = ('nombre', 'potrero__nombre', 'potrero__predio__nombre', 'usuario__email')
