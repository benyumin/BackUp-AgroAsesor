# Crea SOLO la tabla core_cachecompartida (caché compartida del clima). No toca ninguna tabla existente.

from django.db import migrations, models

TABLA = 'core_cachecompartida'

ACTIVAR_RLS = f'ALTER TABLE {TABLA} ENABLE ROW LEVEL SECURITY'
QUITAR_PERMISOS_PUBLICOS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON TABLE {TABLA} FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON TABLE {TABLA} FROM authenticated';
    END IF;
END
$$
"""


def cerrar_acceso_publico(apps, schema_editor):
    """En Supabase, toda tabla nueva nace legible y escribible por la API pública (roles anon y authenticated).

    Django y Flask se conectan con el rol dueño de la tabla, que no pasa por RLS, así que activar RLS y quitar esos
    permisos no les afecta. Solo se ejecuta en PostgreSQL (SQLite, usado en las pruebas, no tiene roles).
    """
    if schema_editor.connection.vendor == 'postgresql':
        for sentencia in (ACTIVAR_RLS, QUITAR_PERMISOS_PUBLICOS):  # una sentencia por llamada
            schema_editor.execute(sentencia, params=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_reparar_referenciaagronomica"),
    ]

    operations = [
        migrations.CreateModel(
            name="CacheCompartida",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("clave", models.CharField(max_length=80, unique=True)),
                ("carga", models.TextField()),
                ("firma", models.CharField(max_length=64)),
                ("obtenido_en", models.DateTimeField()),
                ("vence_en", models.DateTimeField()),
            ],
            options={
                "verbose_name": "entrada de caché compartida",
                "verbose_name_plural": "caché compartida",
            },
        ),
        migrations.RunPython(cerrar_acceso_publico, migrations.RunPython.noop),
    ]
