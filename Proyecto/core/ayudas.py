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
            'Registra tu predio y dibuja su contorno en el mapa.',
            'Divide el predio en potreros y asigna un cultivo a cada uno.',
            'Calcula insumos y programa tus labores.',
        ],
    },
    'predios': {
        'titulo': 'Cómo registrar un predio',
        'resumen': '¿Cómo registro un predio?',
        'pasos': [
            'Presiona «Registrar predio» y dibuja tu campo en el mapa: la superficie se calcula sola.',
            'Desde el menú ⋯ de cada predio puedes verlo, editarlo o archivarlo.',
        ],
    },
    'potreros': {
        'titulo': 'Cómo dividir el terreno en potreros',
        'resumen': '¿Cómo divido el predio en potreros?',
        'pasos': [
            'Agrega cada potrero y dibújalo dentro del contorno del predio.',
            'Asígnale un cultivo y registra su siembra y cosecha.',
        ],
    },
    'calculadora': {
        'titulo': 'Cómo usar la calculadora',
        'resumen': '¿Cómo uso la calculadora?',
        'pasos': [
            'Elige el potrero y el insumo: la superficie y la dosis documentada se proponen solas.',
            'Ajusta la dosis, presiona «Calcular ahora» y descarga el PDF.',
        ],
    },
    'actividades': {
        'titulo': 'Cómo programar y registrar labores',
        'resumen': '¿Cómo programo una labor?',
        'pasos': [
            'Presiona «Programar labor» y elige potrero, tipo de labor y fecha.',
            'Al realizarla, márcala como completada y registra insumo y dosis.',
        ],
    },
    'calendario': {
        'titulo': 'Cómo organizar la siembra',
        'resumen': '¿Cómo uso el calendario de siembra?',
        'pasos': [
            'Elige un potrero en «Mis predios y potreros» para registrar su siembra y cosecha.',
            '«Mis fechas en el año» muestra tus siembras y cosechas mes a mes.',
        ],
    },
    'reportes': {
        'titulo': 'Cómo consultar y descargar información',
        'resumen': '¿Cómo descargo un reporte?',
        'pasos': [
            'Elige el tipo de reporte y filtra por predio o fechas.',
            'Descárgalo en PDF o CSV.',
        ],
    },
}
