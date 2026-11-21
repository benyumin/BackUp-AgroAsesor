# Une las dos ramas de migraciones que se aplicaron en Supabase por separado:
#   * 0018_quita_tablas_sin_uso (rama limpieza-base-datos)
#   * 0018_archivado_y_eliminacion_protegida -> 0019_ayuda_oculta (rama main)
# No cambia nada en la base: solo deja un único historial lineal.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_quita_tablas_sin_uso"),
        ("core", "0019_ayuda_oculta"),
    ]

    operations = []
