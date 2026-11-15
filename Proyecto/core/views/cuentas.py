"""Acceso a la cuenta: inicio de sesión, cierre de sesión, perfil, política de privacidad y entrada al admin.

El registro y la recuperación de contraseña están en ``core.auth`` y el acceso con Google en ``core.google_auth``.
"""

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..auth import MENSAJE_VENCIDA
from ..forms import (
    PerfilForm,
    RegistroForm,
)
from ..google_auth import google_configurado, google_login_url
from ..models import (
    AceptacionPoliticas,
)
from .base import render_page


def politica_privacidad(request):
    return render(
        request,
        'politica_privacidad.html',
        {
            'policy_version': AceptacionPoliticas.VERSION_ACTUAL,
        },
    )


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    mode = request.POST.get('mode') or request.GET.get('mode') or 'login'
    if mode in {'recover', 'reset'}:
        return redirect('password_reset')
    mode = 'register' if mode == 'register' else 'login'
    error = ''
    if request.method == 'GET' and request.GET.get('vencida'):
        messages.warning(
            request, MENSAJE_VENCIDA
        )  # la página detectó que se perdió la sesión (p. ej. cookies borradas)
    identifier = (request.POST.get('username') or request.POST.get('email') or '').strip()
    registration = RegistroForm(request.POST if request.method == 'POST' and mode == 'register' else None)
    if request.method == 'POST':
        if mode == 'register':
            if registration.is_valid():
                try:
                    with transaction.atomic():
                        user = registration.save()
                        AceptacionPoliticas.objects.create(
                            usuario=user,
                            version=AceptacionPoliticas.VERSION_ACTUAL,
                        )
                except IntegrityError:
                    error = 'El nombre de usuario o el correo electrónico ya está registrado.'
                else:
                    login(request, user, backend='core.auth.EmailBackend')
                    messages.success(request, 'Tu cuenta se creó correctamente. Ya iniciaste sesión.')
                    return redirect('dashboard')
        else:
            user = authenticate(
                request,
                username=identifier,
                password=request.POST.get('password', ''),
            )
            if user is None:
                error = 'Usuario, correo electrónico o contraseña incorrectos.'
            else:
                login(request, user)
                return redirect('dashboard')
    return render(
        request,
        'login.html',
        {
            'mode': mode,
            'error': error,
            'registration_form': registration,
            'identifier': identifier,
            'google_login_url': google_login_url(),
            'google_configurado': google_configurado(),
            'headline': (
                'Optimiza tus decisiones agrícolas con datos reales de tu campo.'
                if mode == 'login'
                else 'Únete a la red de agricultores líderes de la provincia de Melipilla.'
            ),
        },
    )


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def perfil(request):
    """Datos personales del agricultor: nombre, teléfono, comuna y RUT opcional."""
    form = PerfilForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Tus datos se guardaron correctamente.')
        return redirect('perfil')
    return render_page(request, 'perfil.html', 'perfil', {'form': form, 'tiene_ficha': form.ficha is not None})


@login_required
@require_POST
def enter_as_admin(request):
    if not request.user.is_staff:
        raise PermissionDenied
    return redirect('admin_panel')
