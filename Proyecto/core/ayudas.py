"""Consejos breves que enseñan a usar cada sección de AgroAsesor sin instrucciones externas.

Cada consejo se muestra abierto la primera vez que la persona entra a la sección. Al presionar «Entendido» (o
plegarlo) el navegador lo recuerda (``localStorage``, por usuario; ver ``static/js/ayudas.js``) y desde entonces se
ve solo como una línea «¿Cómo funciona…?» que se puede abrir cuando se quiera. En «Mi perfil» se pueden volver a
mostrar todos. No se guarda nada en la base de datos.

Para agregar uno: sumar la clave aquí y poner ``{% ayuda 'clave' %}`` en la plantilla.
"""

AYUDAS = {
    'bienvenida': {
        'titulo': 'Bienvenido a AgroAsesor: así se trabaja',
        'resumen': 'Primeros pasos en AgroAsesor',
        'pasos': [
            'Registra tu predio en Mis predios y dibuja su contorno en el mapa.',
            'Divide el predio en potreros y asigna el cultivo de cada uno.',
            'Calcula semillas e insumos con la superficie y la dosis por hectárea.',
            'Programa y registra tus labores en Actividades y revisa las fechas en Siembra.',
        ],
    },
    'predios': {
        'titulo': 'Cómo registrar un predio',
        'resumen': '¿Cómo registro un predio?',
        'pasos': [
            'Presiona «Registrar predio» y dibuja el contorno de tu campo en el mapa: la superficie se calcula sola.',
            'Si no puedes dibujarlo, escribe la superficie en hectáreas.',
            'Desde el menú ⋯ de cada predio puedes verlo, editarlo, archivarlo o eliminarlo.',
        ],
    },
    'potreros': {
        'titulo': 'Cómo dividir el terreno en potreros',
        'resumen': '¿Cómo divido el predio en potreros?',
        'pasos': [
            'Agrega cada potrero desde la cabecera de la ficha y dibújalo dentro del contorno del predio; no '
            'pueden superponerse.',
            'Asigna el cultivo de cada potrero y registra su temporada (siembra y cosecha prevista).',
            '«Superficie utilizada» muestra cuánto del predio ya está dividido en potreros.',
        ],
    },
    'calculadora': {
        'titulo': 'Cómo usar la calculadora',
        'resumen': '¿Cómo uso la calculadora?',
        'pasos': [
            'Elige el potrero: la superficie se completa con la registrada y puedes usar solo una parte.',
            'Revisa el cultivo y la semilla o el insumo; si el catálogo tiene una dosis documentada, se propone sola.',
            'Ajusta la dosis por hectárea y presiona «Calcular ahora». Después puedes descargar el PDF.',
        ],
    },
    'actividades': {
        'titulo': 'Cómo programar y registrar labores',
        'resumen': '¿Cómo programo una labor?',
        'pasos': [
            'Presiona «Programar labor» y elige el potrero, el tipo de labor y la fecha.',
            'Cuando la realices, márcala como completada y registra el insumo, la dosis y el responsable.',
            'Cambia a la vista Calendario para ver las labores del mes; las vencidas se destacan.',
        ],
    },
    'calendario': {
        'titulo': 'Cómo organizar la siembra',
        'resumen': '¿Cómo uso el calendario de siembra?',
        'pasos': [
            'En «Mis predios y potreros» elige un potrero para registrar o revisar su siembra y cosecha.',
            '«Mis fechas en el año» muestra tus siembras y cosechas mes a mes; «Lo que sigue» resume lo próximo.',
            'Cuando el catálogo tiene la ventana de siembra de referencia de un cultivo, puedes comparar tus fechas.',
        ],
    },
    'reportes': {
        'titulo': 'Cómo consultar y descargar información',
        'resumen': '¿Cómo descargo un reporte?',
        'pasos': [
            'Elige el tipo de reporte y filtra por predio o por fechas.',
            'Descárgalo en PDF para imprimir o compartir, o en CSV para abrirlo en Excel.',
            'Los reportes usan solo los registros guardados en tu cuenta.',
        ],
    },
}
