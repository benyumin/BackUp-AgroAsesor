# Elimina el RUT del agricultor: ya no se pide en el registro ni en el perfil.
# OJO: es una operación destructiva. Borra la columna «rut» (y su restricción de unicidad) de core_agricultor,
# por lo que los RUT que hubiera guardados se pierden. Haz un respaldo de la base antes de aplicarla.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_merge_20261119"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="agricultor",
            name="rut",
        ),
    ]
