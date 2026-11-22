from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from core import google_auth, views
from core.api_gateway import api_v1
from core.recuperacion import RecuperarContrasenaView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/<path:ruta>", api_v1, name="api_v1"),
    path("login/", views.login_view, name="login"),
    path(
        "politicas/privacidad-y-cookies/",
        views.politica_privacidad,
        name="politica_privacidad",
    ),
    path("logout/", views.logout_view, name="logout"),
    path("cuenta/google/", google_auth.google_iniciar, name="google_iniciar"),
    path("cuenta/google/retorno/", google_auth.google_callback, name="google_callback"),
    path("cuenta/google/completar/", google_auth.google_completar, name="google_completar"),
    path("cuenta/perfil/", views.perfil, name="perfil"),
    path("entrar-admin/", views.enter_as_admin, name="enter_as_admin"),
    path("", views.dashboard, name="dashboard"),
    path("predios/", views.predios, name="predios"),
    path("mapa-prueba/", views.mapa_prueba, name="mapa_prueba"),
    path("predios/nuevo/", views.predio_form, name="predio_create"),
    path("predios/<int:pk>/", views.predio_detail, name="predio_detail"),
    path("predios/archivados/", views.archivados, name="archivados"),
    path(
        "registros/<str:tipo>/<int:pk>/eliminar/",
        views.registro_confirmar,
        name="registro_confirmar",
    ),
    path("registros/<str:tipo>/<int:pk>/accion/", views.registro_accion, name="registro_accion"),
    path("predios/<int:pk>/editar/", views.predio_form, name="predio_edit"),
    path(
        "predios/<int:pk>/usar-completo/",
        views.predio_usar_completo,
        name="predio_usar_completo",
    ),
    path(
        "predios/<int:predio_pk>/potreros/nuevo/",
        views.potrero_form,
        name="potrero_create",
    ),
    path(
        "predios/<int:predio_pk>/potreros/<int:pk>/editar/",
        views.potrero_form,
        name="potrero_edit",
    ),
    path(
        "predios/<int:predio_pk>/potreros/<int:potrero_pk>/temporadas/nueva/",
        views.temporada_form,
        name="temporada_create",
    ),
    path(
        "predios/<int:predio_pk>/potreros/<int:potrero_pk>/temporadas/<int:pk>/editar/",
        views.temporada_form,
        name="temporada_edit",
    ),
    path("panel-admin/cultivos-base/", views.cultivos_base_cargar, name="cultivos_base"),
    path("api/potreros/<int:pk>/", views.potrero_api, name="potrero_api"),
    path("api/predios/<int:pk>/clima/", views.predio_clima_api, name="predio_clima_api"),
    path(
        "api/potreros/<int:pk>/cultivo/",
        views.potrero_assign_crop,
        name="potrero_assign_crop",
    ),
    path(
        "cuenta/recuperar/",
        RecuperarContrasenaView.as_view(
            template_name="registration/access_form.html",
            email_template_name="registration/password_reset_email.txt",
            subject_template_name="registration/agro_reset_subject.txt",
        ),
        name="password_reset",
    ),
    path(
        "cuenta/enviado/",
        auth_views.PasswordResetDoneView.as_view(template_name="registration/reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "cuenta/restablecer/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(template_name="registration/access_form.html"),
        name="password_reset_confirm",
    ),
    path(
        "cuenta/restablecida/",
        auth_views.PasswordResetCompleteView.as_view(template_name="registration/reset_complete.html"),
        name="password_reset_complete",
    ),
    path("calculadoras/", views.calculadoras, name="calculadoras"),
    path("decisiones/", views.decisiones, name="decisiones"),
    path("calculos/ejecutar/", views.calculate, name="calculate"),
    path("calculos/<int:pk>/pdf/", views.calculo_pdf, name="calculo_pdf"),
    path("productos/", views.productos, name="productos"),
    path("productos/<int:pk>/", views.producto_detail, name="producto_detail"),
    path("productos/<int:pk>/pdf/", views.producto_pdf, name="producto_pdf"),
    path("calendario/", views.calendario, name="calendario"),
    path("calendario/referencias/<int:pk>/pdf/", views.calendario_pdf, name="calendario_pdf"),
    path(
        "calendario/evaluar/",
        views.evaluate_calendar_date,
        name="evaluate_calendar_date",
    ),
    path("actividades/", views.activity_list, name="actividad_list"),
    path("actividades/nueva/", views.activity_create, name="actividad_create"),
    path("actividades/plan/", views.activity_plan, name="actividad_plan"),
    path("actividades/<int:pk>/", views.activity_detail, name="actividad_detail"),
    path("actividades/<int:pk>/editar/", views.activity_edit, name="actividad_edit"),
    path(
        "actividades/<int:pk>/estado/",
        views.activity_change_status,
        name="actividad_estado",
    ),
    path("actividades/<int:pk>/eliminar/", views.activity_delete, name="actividad_delete"),
    path("busqueda/", views.busqueda, name="busqueda"),
    path("reportes/", views.reportes, name="reportes"),
    path("panel-admin/", views.admin_panel, name="admin_panel"),
]
