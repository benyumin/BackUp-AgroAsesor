# AgroAsesor

Página web para que agricultores registren sus predios y potreros en un mapa, planifiquen labores y
calculen semillas e insumos usando solo datos con fuente verificada.

- **Django + SQLite**: toda la página (cuentas, predios, mapa, calendario, actividades, calculadoras,
  reportes y panel de administración).
- **`backend_flask/`**: API Flask de solo lectura que Django usa por detrás en `/api/v1/`.
  Ver [backend_flask/README.md](backend_flask/README.md).

## Estructura

```text
Proyecto/
├── AgroAsesor/          configuración de Django (settings, urls)
├── core/                la aplicación
│   ├── models.py        tablas: predios, potreros, temporadas, actividades, catálogo, cálculos
│   ├── views.py         páginas y respuestas JSON
│   ├── forms.py         formularios y validaciones
│   ├── validators.py    RUT, teléfono y otros datos de entrada
│   ├── agronomy.py      ventanas de siembra/cosecha y evaluación de fechas del calendario
│   ├── catalog.py       catálogo de cultivos e insumos para las páginas
│   ├── seasons.py       temporadas de cultivo de cada potrero
│   ├── geometry.py      área y perímetro de los polígonos del mapa
│   ├── auth.py          acceso con correo o usuario y manejo de sesión vencida
│   ├── api_gateway.py   puente firmado hacia la API Flask
│   ├── estadisticas.py  números del panel de administración
│   ├── dbutils.py       ruta de la base de datos y respaldos
│   ├── admin.py         Django Admin
│   ├── management/      comandos de consola (catálogo, base de datos, administradores)
│   ├── templates/       HTML
│   ├── static/          CSS, JS e imágenes
│   └── tests/           pruebas
├── data/                CSV del catálogo agrícola real
├── data_demo/           catálogo ficticio que usan las pruebas
└── backend_flask/       API Flask
```

## Ejecutar con Docker (forma oficial)

```powershell
cd Proyecto
docker compose up -d --build
```

Luego abre http://localhost:8000/.

La base de datos oficial es `/data/db.sqlite3`, dentro del volumen `agroasesor_hugo_agroasesor_data`.
Sobrevive a `docker compose down`, a reconstruir la imagen y a reiniciar el PC. **Nunca** uses
`docker compose down -v` ni borres ese volumen: se pierde la base. Cada vez que el contenedor arranca
hace un respaldo en `backups/` (guarda los últimos 20), aplica las migraciones y carga el catálogo de `data/`.

| Para qué | Comando |
|---|---|
| Ver qué base se usa y cuánto contiene | `docker exec agroasesor_hugo python manage.py estado_bd` |
| Hacer un respaldo ahora | `docker exec agroasesor_hugo python manage.py respaldar_bd` |
| Crear una cuenta de administrador | `docker exec -it agroasesor_hugo python manage.py createsuperuser` |
| Dar o quitar el rol de administrador | `docker exec agroasesor_hugo python manage.py administradores` |

Para levantar también la API Flask, define `AGROASESOR_INTERNAL_SECRET` en `.env` y usa:

```powershell
docker compose -f docker-compose.yml -f docker-compose.api.yml up -d --build
```

## Ejecutar sin Docker (desarrollo)

Requiere Python 3.12 o superior. Desde la carpeta del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r Proyecto/requirements.txt
Copy-Item Proyecto/.env.example Proyecto/.env   # solo la primera vez
.\.venv\Scripts\python.exe Proyecto/manage.py migrate
.\.venv\Scripts\python.exe Proyecto/manage.py createsuperuser
.\.venv\Scripts\python.exe Proyecto/manage.py runserver
```

Así se usa un `db.sqlite3` local, que **no** es la base oficial (Django lo avisa al arrancar).

## Variables de entorno

Se definen en `Proyecto/.env` (parte copiando `.env.example`). Ese archivo no se sube a Git.

| Variable | Para qué |
|---|---|
| `DJANGO_SECRET_KEY` | clave secreta; obligatoria con `DJANGO_DEBUG=False` |
| `DJANGO_DEBUG` | `True` en desarrollo, `False` en producción (activa cookies seguras) |
| `DJANGO_ALLOWED_HOSTS` | dominios permitidos, separados por coma |
| `MAP_TILE_URL`, `MAP_TILE_ATTRIBUTION` | proveedor de mapas (por defecto OpenStreetMap.de) |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | correo para recuperar la contraseña (en local se imprime en la consola) |
| `AGROASESOR_API_URL`, `AGROASESOR_INTERNAL_SECRET` | conexión con la API Flask; sin ellas `/api/v1/` responde 503 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | inicio de sesión con Google; sin ellas el botón no aparece |
| `GOOGLE_REDIRECT_URI` | opcional: URL de retorno exacta registrada en Google |
| `GOOGLE_MAPS_API_KEY` | clave de Google Maps JavaScript API para el mapa de predios; sin ella se usa el mapa de respaldo |

### Inicio de sesión con Google

Django sigue administrando la autenticación y la sesión; Google solo confirma el correo (OAuth 2.0 +
OpenID Connect con PKCE, sin dependencias nuevas). Para activarlo:

1. En [Google Cloud Console](https://console.cloud.google.com/) → *APIs y servicios* → *Pantalla de consentimiento
   de OAuth*: tipo **Externo**, nombre «AgroAsesor», ámbitos `openid`, `email` y `profile`.
2. *Credenciales* → *Crear credenciales* → **ID de cliente de OAuth** → tipo **Aplicación web**.
3. En **URI de redireccionamiento autorizados** agrega la dirección exacta de cada entorno, por ejemplo
   `http://127.0.0.1:8000/cuenta/google/retorno/`, `http://localhost:8000/cuenta/google/retorno/` y,
   en Codespaces, `https://<tu-codespace>-8000.app.github.dev/cuenta/google/retorno/`.
4. Copia el ID y el secreto en `Proyecto/.env` (`GOOGLE_CLIENT_ID=...`, `GOOGLE_CLIENT_SECRET=...`) y reinicia Django.

Reglas: si el correo de Google ya tiene cuenta, entra a esa cuenta (no se duplica ni cambia su rol);
si no, se pide completar el registro de agricultor (nombre, teléfono, comuna, RUT opcional y política).

## Catálogo agrícola

Zonas, cultivos, problemas, insumos y referencias se cargan desde los CSV de `data/` o desde `/admin/`.
Ningún dato técnico se inventa: completa cada fila solo con valores de una fuente oficial
(SAG, ODEPA, INIA o la etiqueta del fabricante).

**Carga inicial controlada (recomendada).** Al arrancar el contenedor se ejecuta
`cargar_catalogo_inicial`, que importa `data/*.csv` (cultivos, zona Melipilla e insumos de referencia
con sus categorías) **solo si los CSV cambiaron** desde la última carga registrada (tabla `CargaCatalogo`,
visible en `/admin/`). Un reinicio normal no importa nada. Nunca duplica, sobrescribe ni borra registros:
lo que ya existe (por ejemplo, un insumo corregido en `/admin/`) se deja tal cual.

```powershell
python manage.py cargar_catalogo_inicial            # carga data/ si es nueva o cambió
python manage.py cargar_catalogo_inicial --simular  # muestra qué haría, sin guardar
python manage.py cargar_catalogo_inicial --forzar   # revisa de nuevo los CSV (solo agrega lo que falte)
```

Importación manual (para otros archivos o para corregir registros existentes):

```powershell
python manage.py importar_catalogo --categorias                   # categorías de insumo (una vez)
python manage.py importar_catalogo data                           # solo valida, no guarda
python manage.py importar_catalogo data --aplicar                 # guarda
python manage.py importar_catalogo data --aplicar --actualizar    # además corrige registros existentes
```

- Separador `;` o `,`, UTF-8, decimales con coma o punto y fechas `AAAA-MM-DD`.
- Las listas (problemas, cultivos, objetivos de control) van separadas por `|`. Una celda vacía significa «sin dato».
- El orden de carga es zonas → cultivos → problemas → insumos → usos → calendario y rendimientos.
- Es seguro repetirlo: no duplica registros ni borra valores guardados.

## Pruebas

```powershell
.\.venv\Scripts\python.exe Proyecto/manage.py test core
```

Las pruebas de la API Flask están en `backend_flask/tests` (ver su README).
