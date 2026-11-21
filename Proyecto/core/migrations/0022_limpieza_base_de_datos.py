# Limpieza de la base de datos, etapa 1.
# OJO: es una operación destructiva. Haz un respaldo de la base antes de aplicarla. Borra:
#   * core_ayudaoculta: los consejos de ayuda ya plegados (ahora los recuerda el navegador de cada persona).
#   * core_cargacatalogo: el registro de cargas del catálogo inicial (ahora se carga solo si no hay cultivos).
#   * core_problemaagricola, core_problemaagricola_cultivos y core_insumo_problemas (estaban vacías).
#   * core_prediohistorial.geometria_anterior y geometria_nueva: instantáneas JSON del perímetro. El resumen y las
#     superficies de cada movimiento del historial se conservan.
#   * core_agricultor.nombre: el nombre ya está en la cuenta (first_name y last_name). Antes de quitar la columna se
#     copia a la cuenta si esta no tuviera nombre, así no se pierde.

from django.conf import settings
from django.db import migrations


def _partir_nombre(nombre_completo):
    """Misma regla que core.validators.partir_nombre, copiada para que la migración no dependa del código de la app."""
    partes = nombre_completo.split()
    if len(partes) <= 2:
        return partes[0], " ".join(partes[1:])
    mitad = 2 if len(partes) >= 4 else 1
    return " ".join(partes[:mitad]), " ".join(partes[mitad:])


def copiar_nombre_a_la_cuenta(apps, schema_editor):
    """Si la cuenta no tiene nombre propio, le pasa el de su ficha antes de quitar core_agricultor.nombre."""
    base = schema_editor.connection.alias
    Agricultor = apps.get_model("core", "Agricultor")
    Usuario = apps.get_model(settings.AUTH_USER_MODEL)
    fichas = Agricultor.objects.using(base).exclude(nombre="").filter(user__first_name="", user__last_name="")
    for ficha in fichas:
        if not ficha.nombre.split():
            continue
        nombre, apellidos = _partir_nombre(ficha.nombre)
        Usuario.objects.using(base).filter(pk=ficha.user_id).update(first_name=nombre[:150], last_name=apellidos[:150])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_remove_agricultor_rut"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(copiar_nombre_a_la_cuenta, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="agricultor",
            name="nombre",
        ),
        migrations.DeleteModel(
            name="AyudaOculta",
        ),
        migrations.DeleteModel(
            name="CargaCatalogo",
        ),
        migrations.RemoveField(
            model_name="insumo",
            name="problemas",
        ),
        migrations.DeleteModel(
            name="ProblemaAgricola",
        ),
        migrations.RemoveField(
            model_name="prediohistorial",
            name="geometria_anterior",
        ),
        migrations.RemoveField(
            model_name="prediohistorial",
            name="geometria_nueva",
        ),
    ]
