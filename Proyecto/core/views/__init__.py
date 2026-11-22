"""Vistas de AgroAsesor, separadas por área funcional.

Cada módulo agrupa las pantallas de un área (predios, actividades, calculadora, etc.). Este archivo vuelve a
exportar las vistas para que ``AgroAsesor/urls.py`` siga usando ``views.<nombre>`` sin cambios.

Dependencias entre módulos (sin ciclos)::

    base ──► contexto_predios          (menú, ruta guiada y armado de página)
    cuentas, inicio, predios, actividades, calculadora,
    calendario, reportes, catalogo, administracion ──► base / contexto_predios
    inicio ──► reportes                (resumen de los últimos cálculos)

La lógica que no es de pantalla vive fuera de este paquete: ``core.calculos`` (cálculos agrícolas),
``core.seasons`` (temporadas), ``core.geometry`` (superficies y contornos), ``core.agronomy`` (fechas de siembra),
``core.catalog`` (catálogo de cultivos e insumos), ``core.search`` (buscador) y ``core.registros`` (eliminar,
archivar y restaurar predios y potreros).
"""
from .actividades import (  # noqa: F401
    ACTIVITY_GUIDANCE,
    activity_change_status,
    activity_create,
    activity_delete,
    activity_detail,
    activity_edit,
    activity_list,
    activity_plan,
)
from .administracion import (  # noqa: F401
    admin_panel,
    cultivos_base_cargar,
)
from .base import (  # noqa: F401
    JOURNEY_PAGES,
    build_menu,
    journey_payload,
    render_page,
)
from .calculadora import (  # noqa: F401
    calculadoras,
    calculate,
    calculo_pdf,
    decisiones,
)
from .calendario import (  # noqa: F401
    calendar_reference_payload,
    calendario,
    calendario_pdf,
    evaluate_calendar_date,
)
from .catalogo import (  # noqa: F401
    ACTIVITY_TYPE_BY_CATEGORY,
    PRODUCT_STATUS_LABELS,
    busqueda,
    producto_detail,
    producto_pdf,
    productos,
)
from .clima_predio import (  # noqa: F401
    predio_clima_api,
)
from .contexto_predios import (  # noqa: F401
    ACTIVITY_ICONS,
    decision_url,
    farm_cards,
    farm_payload,
    map_config,
    plan_form_url,
    plot_season_summary,
)
from .cuentas import (  # noqa: F401
    enter_as_admin,
    login_view,
    logout_view,
    perfil,
    politica_privacidad,
)
from .inicio import (  # noqa: F401
    dashboard,
)
from .predios import (  # noqa: F401
    mapa_prueba,
    RESUBMIT_WINDOW,
    potrero_api,
    potrero_assign_crop,
    potrero_form,
    predio_detail,
    predio_form,
    predio_usar_completo,
    predios,
    temporada_form,
)
from .registros import (  # noqa: F401
    archivados,
    registro_accion,
    registro_confirmar,
)
from .reportes import (  # noqa: F401
    REPORT_TYPES,
    describe_calculation,
    reportes,
)
