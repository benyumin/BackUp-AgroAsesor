
from django.db import migrations


def reparar_referencia_agronomica(apps, schema_editor):
    """
    Reconstruye la tabla ReferenciaAgronomica si falta.

    Utiliza el modelo historico de Django para respetar
    los campos, relaciones e indices existentes.
    """
    modelo = apps.get_model("core", "ReferenciaAgronomica")
    conexion = schema_editor.connection

    tablas_existentes = set(
        conexion.introspection.table_names()
    )

    nombre_tabla = modelo._meta.db_table

    if nombre_tabla in tablas_existentes:
        print(
            f"La tabla {nombre_tabla} ya existe. "
            "No se realizan cambios."
        )
        return

    print(f"Creando tabla faltante: {nombre_tabla}")

    schema_editor.create_model(modelo)

    print("Tabla reconstruida correctamente.")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_limpieza_base_de_datos"),
    ]

    operations = [
        migrations.RunPython(
            reparar_referencia_agronomica,
            migrations.RunPython.noop,
        ),
    ]
