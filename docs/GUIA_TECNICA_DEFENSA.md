# AgroAsesor · Guía técnica para la defensa

Esta guía describe cómo está construido AgroAsesor **según el código actual** del repositorio. Cada sección nombra los
archivos y funciones reales para que se puedan abrir y mostrar durante la evaluación.

> Rutas relativas a la carpeta `Proyecto/`, salvo que se indique otra cosa.

---

## 1. Arquitectura general

AgroAsesor es una aplicación web **Django** (servidor que arma las páginas HTML) con JavaScript sin frameworks en
el navegador y una **API Flask** interna de solo lectura.

```
Navegador ──HTML/CSS/JS──► Django (core)  ──ORM──► PostgreSQL (Supabase) / SQLite en desarrollo
   │                         │
   │ fetch JSON              └─ /api/v1/... ─firma HMAC─► API Flask (backend_flask) ─► misma base, solo lectura
   └──────────────► /calculos/ejecutar/, /api/potreros/...
```

| Pieza | Qué hace | Dónde |
|---|---|---|
| Django | Cuentas, sesiones, pantallas, formularios, cálculos y guardado | `AgroAsesor/`, `core/` |
| Plantillas | HTML de cada pantalla (Django Templates) | `core/templates/` |
| JavaScript | Interacción: mapa, calculadora, filtros, PDF | `core/static/js/` |
| API Flask | Catálogo y consultas en JSON, detrás de Django | `backend_flask/` |
| Base de datos | PostgreSQL en Supabase (oficial); SQLite en pruebas | `AgroAsesor/settings.py` (`DATABASE_URL`) |

Django es el **único** sistema de cuentas: la API Flask no recibe contraseñas. `core/api_gateway.py` reenvía las
peticiones `/api/v1/...` a Flask firmadas con HMAC-SHA256, indicando qué usuario las hace.

## 2. Estructura de carpetas

```
Proyecto/
├── AgroAsesor/            configuración del proyecto Django
│   ├── settings.py        base de datos, apps, autenticación, variables de entorno
│   └── urls.py            todas las rutas (cada una apunta a core.views.<vista>)
├── core/                  la aplicación de AgroAsesor
│   ├── models.py          tablas (predios, potreros, temporadas, catálogo, cálculos, actividades)
│   ├── forms.py           formularios y sus validaciones
│   ├── views/             pantallas, separadas por área (ver sección 5)
│   ├── calculos.py        fórmulas y validaciones de la calculadora
│   ├── pdf/               informes PDF (ReportLab): diseño común y contenido de cada documento
│   ├── seasons.py         reglas de temporadas de cultivo
│   ├── geometry.py        superficie, perímetro y superposición de polígonos
│   ├── agronomy.py        evaluación de fechas de siembra y cosecha
│   ├── catalog.py         catálogo de cultivos e insumos para las pantallas
│   ├── search.py          buscador general
│   ├── auth.py            acceso con correo o usuario y sesión vencida
│   ├── google_auth.py     acceso con Google (OAuth 2.0)
│   ├── api_gateway.py     puente Django → API Flask
│   ├── validators.py      RUT, teléfono, comuna y nombre
│   ├── templates/         HTML
│   ├── static/css|js      estilos y scripts
│   └── tests/             pruebas automatizadas (Django)
└── backend_flask/         API Flask (app/, tests/, openapi.yaml)
```

## 3. Responsabilidad de cada aplicación

* **`core` (Django)**: todo lo que ve y hace el agricultor. Una sola app de Django; dentro, la lógica que no es de
  pantalla vive en módulos propios (`calculos.py`, `seasons.py`, `geometry.py`, `agronomy.py`, `catalog.py`,
  `search.py`) para que las vistas solo coordinen.
* **`backend_flask`**: API REST de consulta (catálogo, fertilizantes, cálculos guardados) documentada en
  `backend_flask/openapi.yaml`. Se protege con la firma de `core/api_gateway.py`
  (`backend_flask/app/security/gateway.py` la verifica).

## 4. Modelos principales (`core/models.py`)

| Modelo | Para qué sirve | Relaciones |
|---|---|---|
| `Agricultor` | Datos del agricultor (nombre, RUT opcional, teléfono, comuna) | 1 a 1 con el usuario de Django |
| `Predio` | Campo del agricultor: superficie, ubicación y contorno (`geojson`) | `propietario` → usuario |
| `Potrero` | División del predio: superficie (4 decimales), contorno, cultivo actual | `predio` → `Predio`; `cultivo` → `Cultivo` |
| `TemporadaCultivo` | Planificación de una temporada: cultivo, variedad, fechas, estado | `potrero` → `Potrero` |
| `PredioHistorial` | Registro de cambios del predio y sus potreros | `predio`, `potrero`, `usuario` |
| `Cultivo` | Catálogo de cultivos, con densidad de siembra **solo si tiene fuente** | — |
| `Insumo` / `TipoInsumo` | Catálogo de semillas, fertilizantes y fitosanitarios con su fuente | `tipo`, `cultivo`, `zona` |
| `ReferenciaAgronomica` | Ventanas de siembra y cosecha por cultivo y zona | `cultivo`, `zona` |
| `CalculoAgricola` | Cálculo guardado: operación, `entrada` y `resultado` en JSON | `usuario`, `potrero` |
| `ActividadAgricola` | Labor planificada o hecha (siembra, fertilización…) | `usuario`, `potrero`, `temporada`, `insumo` |
| `AceptacionPoliticas` | Versión de la política de privacidad aceptada | `usuario` |

Los `on_delete=PROTECT` en `Predio`, `Potrero` y `CalculoAgricola.potrero` impiden borrar por accidente datos que
tienen historial.

## 5. Vistas principales (`core/views/`)

Las vistas estaban en un único `views.py` de 3.440 líneas. Ahora son un **paquete** con un módulo por área.
`core/views/__init__.py` vuelve a exportar todas las vistas, por eso `AgroAsesor/urls.py` no cambió.

| Módulo | Vistas | Qué resuelve |
|---|---|---|
| `base.py` | `render_page`, `build_menu`, `journey_payload` | Contexto común de todas las páginas: menú, usuario, datos del mapa |
| `contexto_predios.py` | `farm_payload`, `farm_cards`, `_plot_ui` | Datos de predios y potreros listos para plantillas y JavaScript |
| `cuentas.py` | `login_view`, `logout_view`, `perfil`, `politica_privacidad` | Acceso, registro y perfil |
| `inicio.py` | `dashboard` | Panel de inicio |
| `predios.py` | `predios`, `predio_detail`, `predio_form`, `potrero_form`, `temporada_form`, `potrero_api` | Predios, potreros, planificación y API del mapa |
| `actividades.py` | `activity_list`, `activity_create`, `activity_plan`… | Actividades agrícolas |
| `calculadora.py` | `decisiones`, `calculate`, `calculo_pdf` | Pantalla de la calculadora, cálculo JSON e informe PDF |
| `calendario.py` | `calendario`, `evaluate_calendar_date`, `calendario_pdf` | Calendario de siembra, evaluación de fechas y PDF |
| `reportes.py` | `reportes`, `describe_calculation` | Reportes, filtros y exportación CSV y PDF |
| `catalogo.py` | `productos`, `producto_detail`, `producto_pdf`, `busqueda` | Catálogo agrícola, ficha PDF y buscador |
| `administracion.py` | `admin_panel`, `cultivos_base_cargar` | Panel propio para el personal |

Las dependencias van en un solo sentido (sin ciclos): todos los módulos usan `base` y `contexto_predios`; `inicio`
usa además `reportes.describe_calculation`.

Ejemplo, la vista completa de la calculadora (`core/views/calculadora.py`):

```python
@login_required
@require_POST
def calculate(request):
    if request.content_type != 'application/json':
        return JsonResponse({'error': 'Envía los datos como JSON.'}, status=415)
    ...
    try:
        payload = json.loads(request.body)
        calculo = ejecutar_calculo(request.user, payload)     # lógica en core/calculos.py
    except ErrorCalculo as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    ...
    guardado = CalculoAgricola.objects.create(usuario=request.user, potrero=calculo.potrero, ...)
    return JsonResponse({'ok': True, 'calculation_id': guardado.pk, **calculo.resultado})
```

## 6. Comunicación entre la interfaz y el backend

1. **Páginas HTML.** Cada vista llama a `render_page(request, plantilla, clave_menu, contexto)`. La plantilla base
   `core/templates/body.html` incrusta los datos que necesita el JavaScript con `json_script`:
   `agro-catalog` (catálogo), `agro-farms` (predios y potreros del usuario) y `agro-crop-picker`.
   Así el navegador no hace consultas extra para dibujar la página.
2. **Formularios.** Predios, potreros, temporadas y actividades se envían por POST normal con token CSRF; la vista
   valida con un `Form` de `core/forms.py` y redirige.
3. **Peticiones JSON (`fetch`).** La calculadora envía `POST /calculos/ejecutar/` con el encabezado `X-CSRFToken`.
   El mapa usa `/api/potreros/<id>/`. Si la sesión venció, `core/auth.SesionVencidaMiddleware` responde
   `401 JSON` y `static/js/layout.js` lleva al login.
4. **API Flask.** `/api/v1/...` pasa por `core/api_gateway.api_v1`, que exige sesión de Django y firma la
   petición.

## 7. Cálculos agrícolas (`core/calculos.py`)

Todas las fórmulas están en un solo módulo, sin código de HTTP, y se prueban por separado (`core/tests/test_calculos.py`).

| Operación (`operation`) | Fórmula (función) |
|---|---|
| `dose` (semillas e insumos) | cantidad = superficie (ha) × dosis por ha (`cantidad_por_dosis`) |
| `coverage` («¿Me alcanza?») | superficie cubierta = mín(superficie, disponible ÷ dosis) |
| `seed_population` (avanzado) | kg/ha = plantas/m² × peso de mil semillas (g) ÷ (100 × germinación × pureza) (`dosis_semilla_por_poblacion`) |
| `fertilizer_nutrition` | kg de producto/ha = necesidad del nutriente ÷ (% del nutriente ÷ 100) (`producto_por_nutriente`) |

**Derivación de la fórmula de población.** Plantas por ha = plantas/m² × 10.000. Semillas por ha = plantas por ha ÷
(germinación × pureza), con ambos como fracción. Kilos por ha = semillas × peso de mil semillas (g) ÷ 1.000 ÷ 1.000.
Simplificando queda la fórmula de la tabla. Ejemplo: 300 plantas/m², 45 g, 90 % y 98 % → 153,06 kg/ha.
No incluye pérdidas en campo; si se quieren considerar, se suben las plantas objetivo.

**Precisión.** Los valores se convierten a `Decimal` (`numero_positivo`) y se multiplican sin redondear. La superficie
se guarda con 4 decimales en `Potrero.superficie_ha`. Solo al mostrar se redondea: superficie con hasta 4 decimales,
dosis con hasta 3 y cantidad con hasta 2 (`core/static/js/calc-resultado.js`). Ejemplo verificado por prueba:
0,1496 ha × 100 kg/ha = 14,96 kg, y la pantalla, el PDF y Reportes muestran «0,1496 ha».

**Procedencia del dato (`clasificar_origen`).** El sistema nunca inventa una densidad:

| Origen | Cuándo | Cómo se muestra |
|---|---|---|
| `documented` | La dosis es igual a la del catálogo y esa tiene fuente, URL y fecha | «Densidad documentada», con la fuente |
| `catalog` | Igual a la del catálogo, pero sin respaldo completo | «Dato de catálogo», como referencia |
| `adjusted` | La persona cambió el valor del catálogo | «Simulación», indicando el valor del catálogo |
| `simulation` | No había valor en el catálogo y se escribió a mano | «Simulación con tus datos» |

Si la dosis es manual, la persona puede anotar de dónde la sacó (`dose_source`). Se guarda y se muestra como «Fuente
indicada por ti», pero **no** convierte la simulación en un dato verificado.

## 8. Cómo se guardan los resultados

`CalculoAgricola` guarda cada cálculo con:

* `operacion`: tipo de cálculo (`dose`, `coverage`, `seed_population`, `fertilizer_nutrition`);
* `entrada`: lo que envió la persona (superficie como texto exacto, dosis, unidad, cultivo, fuente…), armado en
  `ejecutar_calculo` con la lista `CAMPOS_DE_ENTRADA`;
* `resultado`: lo calculado y su procedencia (`amount`, `unit`, `dose`, `origin`, `verified`, `source`…);
* `usuario` y `potrero`, para filtrar siempre por dueño.

Con `"preview": true` el mismo cálculo se hace **sin guardar** (vista previa mientras se escribe).

## 9. Reportes y exportaciones

* **Reportes** (`core/views/reportes.py`, función `reportes`): historial de cálculos, actividades y predios del
  usuario, con filtros por predio, potrero y fechas. Cada cálculo se convierte en texto con
  `describe_calculation` (resultado, detalle «dosis × superficie» y origen del dato).
* **CSV y PDF**: el mismo reporte con `?formato=csv` responde un archivo separado por `;`, y con `?formato=pdf` un informe PDF con los filtros aplicados y la misma tabla.
* **PDF**: se genera **en el servidor** con ReportLab (`core/pdf/`) y se descarga como `application/pdf`.
  `core/pdf/base.py` (`InformePDF`) define el diseño común: hoja A4, encabezado con la marca, tipografía Outfit,
  colores de la plataforma, tablas, tarjeta de resultado y pie con «Página X de Y» y la nota de que el contenido es
  orientativo. `core/pdf/informes.py` arma cada documento con los datos guardados:

  | Documento | Ruta | Función |
  |---|---|---|
  | Informe de un cálculo (semillas, insumos, «¿Me alcanza?», avanzado, por nutriente) | `/calculos/<id>/pdf/` | `informe_calculo` |
  | Calendario de siembra de una referencia | `/calendario/referencias/<id>/pdf/?anio=` | `informe_calendario` |
  | Ficha técnica de un producto | `/productos/<id>/pdf/` | `informe_producto` |
  | Reportes (resumen, cuaderno, cultivos, cálculos) | `/reportes/?tipo=…&formato=pdf` | `informe_reporte` |

  El informe de cálculo se arma con el `CalculoAgricola` guardado (no con lo que hay en pantalla), así el PDF
  coincide con el historial y con Reportes. Solo el dueño del cálculo puede descargarlo.

## 10. Validaciones

| Capa | Dónde | Ejemplos |
|---|---|---|
| Formularios Django | `core/forms.py` (`clean`, `clean_<campo>`) | datos del registro, superficie o contorno obligatorio en el potrero (si hay contorno, la superficie se calcula con `polygon_area`) |
| Modelos (`clean`) | `core/models.py` (`Potrero.clean`, `TemporadaCultivo.clean`, `Insumo.clean`…) | cosecha no anterior a la siembra; potreros que no superan la superficie del predio, que quedan dentro de su contorno y que no se superponen; dosis del catálogo con fuente antes de marcarla verificada |
| Datos personales | `core/validators.py` | `normalizar_rut` (dígito verificador), `normalizar_telefono`, `normalizar_comuna` |
| Geometría | `core/geometry.py` | `polygon_area`, `polygon_contains`, `polygons_overlap` |
| Calculadora (servidor) | `core/calculos.py` | número > 0, potrero del usuario, superficie ≤ la del potrero, producto vigente y del cultivo, fitosanitario solo con uso verificado, germinación y pureza ≤ 100 % |
| Calculadora (pantalla) | `calc-panel.js` (`validate`, `readiness`) | avisa antes de enviar, pero **la regla final es la del servidor** |
| Campos del modelo | `core/models.py` | `MinValueValidator(0.0001)` en superficies, latitud y longitud en rango |

## 11. Usuarios y permisos

* **Acceso**: con correo o usuario (`core/auth.EmailBackend`) o con Google (`core/google_auth.py`, OAuth 2.0 con PKCE).
  El registro crea el `Agricultor` y la `AceptacionPoliticas`. La recuperación de contraseña usa las vistas
  estándar de Django (`AgroAsesor/urls.py`).
* **Sesión**: todas las pantallas privadas usan `@login_required` o `render_page`, que redirige al login.
* **Datos propios**: cada consulta filtra por dueño, por ejemplo `Potrero.objects.filter(predio__propietario=usuario)`
  en `calculos._potrero_del_usuario`, o `get_object_or_404(Predio, pk=..., propietario=request.user)` en
  `views/predios.py`. Un usuario que pide un predio ajeno recibe 404.
* **Personal**: el panel `admin_panel` exige `is_staff` (si no, `PermissionDenied`); el admin de Django
  (`core/admin.py`) gestiona el catálogo.
* **CSRF**: los formularios y los `fetch` envían el token; `core/auth.fallo_csrf` muestra un aviso en vez del error.

---

## 12. Flujo de la calculadora de semillas, paso a paso

Pantalla: `/decisiones/?tipo=semillas&herramienta=llevar`.

| Etapa | Qué pasa | Archivos y funciones |
|---|---|---|
| 0. Abrir la página | La vista arma el contexto y la plantilla incrusta potreros (`agro-farms`) y catálogo (`agro-catalog`) | `views/calculadora.decisiones` → `views/base.render_page`; `templates/decisiones.html`; `templates/body.html` |
| 1. Selección del potrero | Tarjetas de potreros (con su predio). Al elegir, la superficie se copia con 4 decimales | `calc-flow.js` `renderPlots`; `calc-panel.js` `syncPlot` |
| 2. Selección del cultivo | Se propone el cultivo del potrero; se puede elegir otro para simular | `calc-flow.js` `renderCrops`; `calc-panel.js` `onCropChange` |
| 3. Ingreso de parámetros | Superficie, dosis por ha y unidad. Si el catálogo tiene densidad **documentada** se completa sola; si no, el campo queda **vacío** y se puede anotar la fuente | `calc-panel.js` `refreshProducts`, `syncProduct`, `setDose`, `renderDoseOrigin` |
| 4. Validación | En pantalla: `validate` y `readiness` marcan lo que falta. En el servidor: `ejecutar_calculo` repite todas las reglas | `calc-panel.js`; `core/calculos.py` `numero_positivo`, `_potrero_del_usuario`, `_resolver_cultivo`, `_resolver_producto` |
| 5. Cálculo | `POST /calculos/ejecutar/` con JSON; superficie × dosis en `Decimal` y clasificación del origen | `calc-panel.js` `runCalculation` → `views/calculadora.calculate` → `calculos.ejecutar_calculo`, `cantidad_por_dosis`, `clasificar_origen` |
| 6. Almacenamiento | Se crea un `CalculoAgricola` con `entrada` y `resultado` | `views/calculadora.calculate`; `models.CalculoAgricola` |
| 7. Presentación | Tarjeta de resultado: cantidad destacada, cultivo y potrero, dosis y superficie, procedencia del dato y «Ver detalles del cálculo». Acciones: PDF, planificar actividad, Reportes y «¿Me alcanza?» | `calc-resultado.js` `html`; `calc-panel.js` `paint`; `calculadora.css` |
| 8. Informe PDF | El botón «Descargar PDF» pide `/calculos/<id>/pdf/`; el servidor arma el informe con el cálculo guardado: terreno, parámetros, resultado, fórmula con valores y observaciones | `calc-panel.js` `pdfFor` → `views/calculadora.calculo_pdf` → `core/pdf/informes.informe_calculo` |

**Opciones avanzadas** (contraídas por defecto): con plantas/m², peso de mil semillas, germinación, pureza y la
fuente de esos datos, `runAdvancedSeed` envía `operation: 'seed_population'` y el servidor aplica
`dosis_semilla_por_poblacion`. El resultado se rotula «Parámetros con fuente declarada».

## 13. Pruebas automatizadas

* Django: `python manage.py test core` (591 pruebas). Las de la calculadora están en `core/tests/test_calculos.py`
  (fórmulas, precisión, origen del dato, validaciones, guardado y Reportes) y en `test_calculadora_unificada.py`.
* Flask: `cd backend_flask && python -m pytest` (125 pruebas).
* `python manage.py check` sin observaciones.

## 14. Decisiones de arquitectura (para justificar)

1. **Lógica fuera de las vistas.** Las fórmulas (`calculos.py`) se pueden probar y explicar sin HTTP ni base de
   datos, y la vista queda en ~30 líneas.
2. **Vistas por área en vez de un archivo de 3.440 líneas.** Cada módulo tiene una responsabilidad; las rutas y
   nombres públicos no cambiaron, gracias a `views/__init__.py`.
3. **El servidor decide.** La pantalla ayuda a completar, pero el cálculo, las reglas del catálogo y el guardado
   están en el servidor; el JavaScript no puede presentar como verificado algo que no lo es.
4. **No inventar datos agronómicos.** Sin densidad documentada no hay valor por defecto y el resultado se rotula
   como simulación.
5. **Precisión primero, redondeo al final.** Decimales completos en los cálculos y en lo guardado; redondeo solo al
   mostrar.
