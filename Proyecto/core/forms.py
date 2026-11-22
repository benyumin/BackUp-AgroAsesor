import hashlib
import json
from decimal import Decimal
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import ActividadAgricola, Agricultor, Predio, Potrero, Cultivo, Zona, TemporadaCultivo
from .validators import (
    formatear_telefono,
    normalizar_comuna,
    normalizar_nombre_completo,
    normalizar_telefono,
    partir_nombre,
)
from django.db import models
from .calculos import productos_vigentes
from .catalog import crop_group_name, default_zone, grouped_crop_choices
from .geometry import polygon_area, coordinates_from_geojson


class DatosAgricultorMixin(forms.Form):
    """Datos personales del agricultor que se piden en el registro y se editan en el perfil."""

    nombre_completo = forms.CharField(
        max_length=150,
        label='Nombre completo',
        widget=forms.TextInput(attrs={'autocomplete': 'name', 'placeholder': 'Nombre y apellido'}),
    )
    telefono = forms.CharField(
        max_length=20,
        label='Teléfono',
        help_text='Celular o fijo de Chile, por ejemplo +56 9 1234 5678.',
        widget=forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel', 'placeholder': '+56 9 1234 5678'}),
    )
    comuna = forms.CharField(
        max_length=100,
        label='Comuna',
        widget=forms.TextInput(attrs={'autocomplete': 'address-level2', 'placeholder': 'Por ejemplo: Melipilla'}),
    )

    def clean_nombre_completo(self):
        return normalizar_nombre_completo(self.cleaned_data.get('nombre_completo'))

    def clean_telefono(self):
        return normalizar_telefono(self.cleaned_data.get('telefono'))

    def clean_comuna(self):
        return normalizar_comuna(self.cleaned_data.get('comuna'))



def _nombre_de_usuario_desde_correo(correo):
    """Las cuentas nuevas entran con su correo; el nombre de usuario interno es el mismo correo."""
    if len(correo) <= 150:
        return correo
    return 'u' + hashlib.sha1(correo.encode('utf-8')).hexdigest()


class RegistroForm(DatosAgricultorMixin, UserCreationForm):
    """Registro público: solo crea agricultores (nunca administradores)."""

    error_messages = {
        **UserCreationForm.error_messages,
        'password_mismatch': 'Las contraseñas no coinciden.',
    }

    email = forms.EmailField(
        max_length=254,
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'placeholder': 'tu@correo.com'}),
    )
    acepta_politicas = forms.BooleanField(
        required=True,
        error_messages={
            'required': 'Debes leer y aceptar la Política de privacidad, seguridad y cookies para crear tu cuenta.',
        },
    )

    class Meta:
        model = get_user_model()
        fields = ('email',)

    field_order = [
        'nombre_completo',
        'email',
        'telefono',
        'comuna',
        'password1',
        'password2',
        'acepta_politicas',
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].label = 'Contraseña'
        self.fields['password1'].widget.attrs.update({'autocomplete': 'new-password'})
        self.fields['password2'].label = 'Confirmar contraseña'
        self.fields['password2'].widget.attrs.update({'autocomplete': 'new-password'})
        self.fields['password1'].help_text = (
            'Usa al menos 8 caracteres; evita las contraseñas comunes, solo numéricas '
            'o que contengan tus datos personales.'
        )

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        users = get_user_model().objects
        if users.filter(email__iexact=email).exists() or users.filter(username__iexact=email).exists():
            raise ValidationError('Este correo electrónico ya está registrado.')
        return email

    def _post_clean(self):
        # La clave se compara con los datos personales, así que el usuario debe tenerlos antes de validarla.
        nombre = self.cleaned_data.get('nombre_completo')
        correo = self.cleaned_data.get('email')
        if nombre:
            self.instance.first_name, self.instance.last_name = partir_nombre(nombre)
        if correo:
            self.instance.username = _nombre_de_usuario_desde_correo(correo)
        super()._post_clean()

    def save(self, commit=True):
        """Crea el usuario y su ficha de agricultor juntos (el llamador debe envolverlo en una transacción)."""
        user = super().save(commit=False)
        user.username = _nombre_de_usuario_desde_correo(user.email)
        user.first_name, user.last_name = partir_nombre(self.cleaned_data['nombre_completo'])
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
            crear_ficha_agricultor(user, self.cleaned_data)
        return user


def crear_ficha_agricultor(user, datos):
    """Ficha de agricultor de una cuenta nueva (registro normal o con Google).

    El nombre no se guarda aquí: vive en la cuenta (``first_name`` y ``last_name``), que el llamador ya completó.
    """
    return Agricultor.objects.create(
        user=user,
        telefono=datos['telefono'],
        comuna=datos['comuna'],
    )


class RegistroGoogleForm(DatosAgricultorMixin):
    """Completa el registro de quien entró con Google: los mismos datos obligatorios del agricultor.

    El correo ya lo verificó Google y no se pide contraseña (la cuenta entra con Google; si después quiere una
    contraseña puede crearla desde «¿Olvidaste tu contraseña?»).
    """

    acepta_politicas = RegistroForm.base_fields['acepta_politicas']

    field_order = ['nombre_completo', 'telefono', 'comuna', 'acepta_politicas']

    def __init__(self, *args, correo, **kwargs):
        self.correo = correo.strip().lower()
        super().__init__(*args, **kwargs)

    def clean(self):
        datos = super().clean()
        users = get_user_model().objects
        if users.filter(email__iexact=self.correo).exists() or users.filter(username__iexact=self.correo).exists():
            raise ValidationError('Este correo electrónico ya está registrado. Inicia sesión con tu cuenta.')
        return datos

    def save(self):
        """Crea la cuenta (sin contraseña utilizable) y su ficha; el llamador la envuelve en una transacción."""
        user = get_user_model()(
            username=_nombre_de_usuario_desde_correo(self.correo),
            email=self.correo,
            is_staff=False,
            is_superuser=False,
        )
        user.first_name, user.last_name = partir_nombre(self.cleaned_data['nombre_completo'])
        user.set_unusable_password()
        user.full_clean(exclude=['password'])
        user.save()
        crear_ficha_agricultor(user, self.cleaned_data)
        return user


class PerfilForm(DatosAgricultorMixin):
    """Edición de los datos personales de la cuenta (y alta de la ficha para cuentas antiguas)."""

    field_order = ['nombre_completo', 'telefono', 'comuna']

    def __init__(self, *args, user, **kwargs):
        self.user = user
        self.ficha = Agricultor.objects.filter(user=user).first()
        if 'initial' not in kwargs:
            # El nombre sale de la cuenta (first_name + last_name); teléfono y comuna, de la ficha si ya existe.
            kwargs['initial'] = {'nombre_completo': user.get_full_name()}
            if self.ficha is not None:
                kwargs['initial'].update(
                    telefono=formatear_telefono(self.ficha.telefono),
                    comuna=self.ficha.comuna,
                )
        super().__init__(*args, **kwargs)

    def save(self):
        datos = self.cleaned_data
        ficha = self.ficha or Agricultor(user=self.user)
        ficha.telefono = datos['telefono']
        ficha.comuna = datos['comuna']
        ficha.save()
        self.user.first_name, self.user.last_name = partir_nombre(datos['nombre_completo'])
        self.user.save(update_fields=['first_name', 'last_name'])
        self.ficha = ficha
        return ficha


class PredioForm(forms.ModelForm):
    geojson = forms.JSONField(required=False, widget=forms.HiddenInput())
    perimetro_aceptado = forms.BooleanField(required=False, widget=forms.HiddenInput())
    fecha_creacion = forms.CharField(
        label='Fecha de creación',
        required=False,
        disabled=True,
        initial='Se asignará automáticamente al guardar',
    )

    class Meta:
        model = Predio
        fields = [
            'nombre',
            'descripcion',
            'ubicacion',
            'observaciones',
            'superficie_ha',
            'zona',
            'latitud',
            'longitud',
            'geojson',
        ]
        labels = {
            'nombre': 'Nombre del predio',
            'superficie_ha': 'Superficie total (ha)',
            'ubicacion': 'Referencia de ubicación',
            'descripcion': 'Descripción (opcional)',
            'observaciones': 'Observaciones (opcional)',
        }
        # Superficie, latitud y longitud las calcula el mapa: viajan como campos ocultos, no se editan a mano.
        widgets = {
            'nombre': forms.TextInput(attrs={'placeholder': 'Ej: Fundo Santa Rosa'}),
            'ubicacion': forms.TextInput(attrs={'placeholder': 'Ej: Camino a Bollenar, parcela 12'}),
            'descripcion': forms.Textarea(attrs={'rows': 3}),
            'observaciones': forms.Textarea(attrs={'rows': 3}),
            'superficie_ha': forms.HiddenInput(),
            'latitud': forms.HiddenInput(),
            'longitud': forms.HiddenInput(),
        }

    # Descripción y observaciones se muestran como un solo campo; ambas columnas se conservan en la base.
    notas = forms.CharField(
        label='Notas del predio (opcional)',
        required=False,
        widget=forms.Textarea(
            attrs={'rows': 3, 'placeholder': 'Ej: Sector destinado a hortalizas, acceso por camino interior.'}
        ),
    )

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        if instance and instance.pk:
            bound_data = args[0] if args else kwargs.get('data')
            if bound_data is not None and 'geojson' not in bound_data:
                bound_data = bound_data.copy()
                bound_data['geojson'] = json.dumps(instance.geojson) if instance.geojson else ''
                if args:
                    args = (bound_data, *args[1:])
                else:
                    kwargs['data'] = bound_data
        super().__init__(*args, **kwargs)
        # Notas ya guardadas: si el formulario llega sin ningún campo de notas no se vacían (ver _post_clean).
        self._notas_guardadas = (instance.descripcion, instance.observaciones) if instance and instance.pk else None
        self.fields['superficie_ha'].required = False
        # Melipilla va primero y queda preseleccionada en un predio nuevo: es la zona de trabajo del proyecto.
        self.fields['zona'].queryset = (
            Zona.objects.filter(activo=True)
            .annotate(
                _orden=models.Case(
                    models.When(nombre__icontains='melipilla', then=models.Value(0)),
                    default=models.Value(1),
                    output_field=models.IntegerField(),
                ),
            )
            .order_by('_orden', 'nombre')
        )
        self.fields['zona'].label = 'Zona agrícola'
        self.fields['zona'].empty_label = 'Selecciona una zona'
        # La zona es obligatoria solo cuando el catálogo ya tiene zonas: así nunca se bloquea el registro.
        has_zones = self.fields['zona'].queryset.exists()
        legacy_without_zone = bool(instance and instance.pk and not instance.zona_id)
        self.fields['zona'].required = has_zones and not legacy_without_zone
        preferred = default_zone()
        if not (instance and instance.pk) and preferred:
            self.initial['zona'] = preferred.pk  # el valor inicial del modelo (vacío) pisaría al del campo
        if has_zones:
            self.fields['zona'].help_text = (
                'Define las ventanas de siembra y las referencias que se usan para tu predio'
                + (f'. Por defecto: {preferred.nombre}.' if preferred and not (instance and instance.pk) else '.')
            )
        else:
            self.fields['zona'].help_text = 'Aún no hay zonas cargadas; puedes guardar el predio sin zona.'
        if instance and instance.pk:
            self.initial['notas'] = '\n\n'.join(
                text.strip() for text in (instance.descripcion, instance.observaciones) if text and text.strip()
            )
        if instance and instance.pk:
            self.fields['fecha_creacion'].initial = timezone.localtime(instance.creado).strftime('%d/%m/%Y %H:%M')

    def _post_clean(self):
        super()._post_clean()
        # Si llegan las notas unificadas, se guardan en «descripcion» y «observaciones» queda vacía
        # (el texto antiguo ya se mostró dentro de las notas, así que no se pierde nada).
        if 'notas' in self.data:
            self.instance.descripcion = self.cleaned_data.get('notas', '')
            self.instance.observaciones = ''
        elif self._notas_guardadas is not None and 'descripcion' not in self.data and 'observaciones' not in self.data:
            # ModelForm pondría en blanco lo que no llega en el POST: se conservan las notas guardadas.
            self.instance.descripcion, self.instance.observaciones = self._notas_guardadas

    def clean(self):
        data = super().clean()
        geometry = data.get('geojson')
        if geometry:
            try:
                coordinates = coordinates_from_geojson(geometry)
                data['superficie_ha'] = (polygon_area(coordinates) / 10000).quantize(Decimal('0.0001'))
            except ValidationError as exc:
                self.add_error('geojson', exc)
        elif data.get('superficie_ha') is None:
            self.add_error('superficie_ha', 'Ingresa la superficie o dibuja el perímetro.')
        return data


class PotreroForm(forms.ModelForm):
    class Meta:
        model = Potrero
        fields = [
            'nombre',
            'cultivo',
            # La variedad y las fechas de siembra y cosecha se registran en la planificación (TemporadaCultivoForm).
            # Los campos del modelo se conservan: este formulario simplemente ya no los edita ni los borra.
            'superficie_ha',
            'latitud',
            'longitud',
            'perimetro',
        ]
        labels = {
            'superficie_ha': 'Superficie (ha)',
            'perimetro': 'Perímetro',
            'latitud': 'Latitud',
            'longitud': 'Longitud',
        }
        widgets = {
            'perimetro': forms.HiddenInput(),
            'superficie_ha': forms.NumberInput(attrs={'min': '0.0001', 'step': '0.0001'}),
            'latitud': forms.NumberInput(attrs={'min': -85, 'max': 85, 'step': 'any'}),
            'longitud': forms.NumberInput(attrs={'min': -180, 'max': 180, 'step': 'any'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        crops = Cultivo.objects.filter(activo=True)
        self.fields['cultivo'].queryset = crops
        self.fields['cultivo'].choices = grouped_crop_choices(crops, 'Sin cultivo por ahora')
        self.fields['cultivo'].label = 'Cultivo'
        self.fields['cultivo'].help_text = 'Opcional. Puedes dejarlo vacío y asignarlo después.'
        self.fields['superficie_ha'].required = False

    def clean(self):
        data = super().clean()
        perimeter = data.get('perimetro') or []
        data['perimetro'] = perimeter
        if perimeter:
            try:
                data['superficie_ha'] = (polygon_area(perimeter) / 10000).quantize(Decimal('0.0001'))
            except ValidationError as exc:
                self.add_error('perimetro', exc)
        elif data.get('superficie_ha') is None:
            self.add_error('superficie_ha', 'Ingresa la superficie o dibuja el perímetro.')
        return data


def sowing_date_label(crop_name):
    """«Fecha de plantación» para frutales, «siembra o trasplante» para hortalizas y «siembra» para el resto."""
    group = crop_group_name(crop_name) if crop_name else ''
    if group == 'Frutales':
        return 'Fecha de plantación'
    if group == 'Hortalizas':
        return 'Fecha de siembra o trasplante'
    return 'Fecha de siembra'


class TemporadaCultivoForm(forms.ModelForm):
    class Meta:
        model = TemporadaCultivo
        fields = [
            'cultivo',
            'variedad',
            'temporada_agricola',
            'fecha_siembra',
            'fecha_cosecha',
            'estado',
            'observaciones',
        ]
        labels = {
            'cultivo': 'Cultivo',
            'variedad': 'Variedad',
            'temporada_agricola': 'Temporada agrícola',
            'fecha_siembra': 'Fecha de siembra',
            'fecha_cosecha': 'Fecha de cosecha',
            'estado': 'Estado de la temporada',
            'observaciones': 'Observaciones',
        }
        widgets = {
            'fecha_siembra': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'fecha_cosecha': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'observaciones': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        crops = Cultivo.objects.filter(activo=True).order_by('nombre')
        self.fields['cultivo'].queryset = crops
        self.fields['cultivo'].choices = grouped_crop_choices(crops, 'Selecciona un cultivo')
        self.fields['temporada_agricola'].widget.attrs.update(
            {'placeholder': '2026-2027', 'pattern': '[0-9]{4}-[0-9]{4}'}
        )
        self.fields['fecha_siembra'].required = False
        self.fields['fecha_cosecha'].required = False
        # El nombre de la primera fecha depende del cultivo: un frutal se planta, una hortaliza se siembra o trasplanta.
        self.fields['fecha_siembra'].label = sowing_date_label(self._crop_name())

    def _crop_name(self):
        crop_id = self.data.get('cultivo') if self.is_bound else (self.initial.get('cultivo') or self.instance.cultivo_id)
        crop_id = getattr(crop_id, 'pk', crop_id)
        if not str(crop_id or '').isdigit():
            return ''
        return Cultivo.objects.filter(pk=crop_id).values_list('nombre', flat=True).first() or ''


UNIDADES_DOSIS = [
    ('', 'Unidad'),
    ('kg/ha', 'kg/ha'),
    ('g/ha', 'g/ha'),
    ('L/ha', 'L/ha'),
    ('mL/ha', 'mL/ha'),
    ('unidades/ha', 'unidades/ha'),
]


class ActividadAgricolaForm(forms.ModelForm):
    unidad_dosis = forms.ChoiceField(choices=UNIDADES_DOSIS, required=False, label='Unidad')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['potrero'].queryset = (
            Potrero.objects.filter(predio__propietario=user)
            .select_related('predio', 'cultivo')
            .order_by('predio__nombre', 'nombre')
        )
        self.fields['potrero'].empty_label = 'Selecciona un potrero'
        self.fields['potrero'].label_from_instance = lambda plot: f'{plot.predio.nombre} · {plot.nombre}'
        self.fields['temporada'].queryset = TemporadaCultivo.objects.filter(
            potrero__predio__propietario=user
        ).select_related('cultivo', 'potrero')
        self.fields['temporada'].empty_label = 'Sin temporada asociada'
        self.fields['temporada'].label_from_instance = lambda season: (
            f'{season.temporada_agricola + " · " if season.temporada_agricola else ""}'
            f'{season.cultivo.nombre}{" · " + season.variedad if season.variedad else ""} · {season.get_estado_display()}'
        )
        self.fields['insumo'].queryset = (
            productos_vigentes()
            .exclude(estado_verificacion__in=['restricted', 'cancelled'])
            .select_related('tipo', 'cultivo')
            .order_by('tipo__nombre', 'nombre')
        )
        self.fields['insumo'].empty_label = 'Sin producto del catálogo'
        self.fields['insumo'].label_from_instance = lambda item: f'{item.tipo.nombre} · {item.nombre}'
        self.fields['prioridad'].required = False
        self.fields['tipo'].choices = [
            ('', 'Selecciona un tipo de actividad'),
            *[choice for choice in self.fields['tipo'].choices if choice[0]],
        ]

    class Meta:
        model = ActividadAgricola
        fields = [
            'tipo',
            'nombre',
            'potrero',
            'temporada',
            'prioridad',
            'fecha_programada',
            'estado',
            'fecha_realizada',
            'responsable',
            'insumo',
            'insumo_otro',
            'objetivo',
            'dosis',
            'unidad_dosis',
            'superficie_ha',
            'condiciones_clima',
            'observaciones',
        ]
        labels = {
            'nombre': 'Nombre de la actividad',
            'tipo': 'Tipo de labor',
            'potrero': 'Potrero',
            'temporada': 'Temporada de cultivo',
            'prioridad': 'Prioridad',
            'fecha_programada': 'Fecha programada',
            'estado': 'Estado',
            'fecha_realizada': 'Fecha en que se realizó',
            'responsable': 'Responsable / aplicador',
            'insumo': 'Producto del catálogo',
            'insumo_otro': 'Otro producto (no catalogado)',
            'objetivo': 'Objetivo (plaga, maleza, nutriente…)',
            'dosis': 'Dosis',
            'superficie_ha': 'Superficie tratada (ha)',
            'condiciones_clima': 'Condiciones al momento de la labor',
            'observaciones': 'Observaciones (opcional)',
        }
        widgets = {
            'fecha_programada': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'fecha_realizada': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'observaciones': forms.Textarea(attrs={'rows': 4}),
            'dosis': forms.NumberInput(attrs={'min': '0.001', 'step': 'any'}),
            'superficie_ha': forms.NumberInput(attrs={'min': '0.0001', 'step': 'any'}),
            'condiciones_clima': forms.TextInput(attrs={'placeholder': 'Ej.: despejado, viento suave, suelo húmedo'}),
            'responsable': forms.TextInput(attrs={'placeholder': 'Nombre de quien ejecuta la labor'}),
        }

    def clean_potrero(self):
        potrero = self.cleaned_data['potrero']
        if potrero.predio.propietario_id != self.user.pk:
            raise ValidationError('Selecciona un potrero de tu cuenta.')
        return potrero

    def clean(self):
        data = super().clean()
        if self.instance.pk and self.instance.usuario_id != self.user.pk:
            raise ValidationError('No puedes modificar actividades de otra cuenta.')
        plot = data.get('potrero')
        season = data.get('temporada')
        product = data.get('insumo')
        crop = season.cultivo if season else (plot.cultivo if plot else None)
        if product and product.cultivo_id and crop and product.cultivo_id != crop.pk:
            self.add_error('insumo', f'El producto está asociado a {product.cultivo.nombre}, no a {crop.nombre}.')
        if (
            product
            and data.get('unidad_dosis')
            and product.unidad_dosis
            and data['unidad_dosis'] != product.unidad_dosis
        ):
            self.add_error('unidad_dosis', f'La unidad registrada del producto es {product.unidad_dosis}.')
        if (
            product
            and product.dosis_maxima_hectarea is not None
            and data.get('dosis')
            and data['dosis'] > product.dosis_maxima_hectarea
        ):
            self.add_error(
                'dosis',
                f'Supera la dosis máxima registrada ({product.dosis_maxima_hectarea} {product.unidad_dosis_maxima}).',
            )
        if not data.get('prioridad'):
            data['prioridad'] = ActividadAgricola.Prioridad.MEDIA
        if data.get('tipo') not in ActividadAgricola.TIPOS_CON_INSUMO:
            for field in ('insumo', 'insumo_otro', 'dosis', 'unidad_dosis'):
                if data.get(field):
                    data[field] = None if field in ('insumo', 'dosis') else ''
        return data
