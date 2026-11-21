#!/bin/sh
set -e

# Única base oficial: /data/db.sqlite3 (volumen persistente). Antes de tocarla se guarda un respaldo fechado en ./backups.
if [ -f "${DJANGO_DB_PATH:-/data/db.sqlite3}" ]; then
  python manage.py respaldar_bd --mantener 20 || echo "AVISO: no se pudo respaldar la base."
fi

# Crea/actualiza las tablas (idempotente).
python manage.py migrate --noinput

# Borra las sesiones de inicio de sesión ya vencidas (sin esto la tabla django_session solo crece).
# No es crítico: si falla, la aplicación igual arranca y el aviso queda en el log.
python manage.py clearsessions || echo "AVISO: no se pudieron limpiar las sesiones vencidas."

# Catálogo inicial desde data/*.csv (cultivos, zona Melipilla e insumos de referencia, con sus categorías).
# Carga INICIAL: solo importa si la base aún no tiene cultivos; un reinicio normal no importa nada y un registro
# borrado a propósito no reaparece. Nunca duplica, sobrescribe ni borra registros existentes.
# Si falla, la aplicación igual arranca y el aviso queda en el log.
python manage.py cargar_catalogo_inicial data || echo "AVISO: no se pudo cargar el catálogo inicial desde data/ (revisa los CSV)."

# Deja en el log qué base se está usando y cuánto contiene.
python manage.py estado_bd || true

exec "$@"
