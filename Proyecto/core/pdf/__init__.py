"""Informes PDF de AgroAsesor, generados en el servidor con ReportLab.

* ``base.InformePDF``: plantilla común (encabezado, pie, tipografía, colores, tablas y tarjetas).
* ``informes``: el contenido de cada documento (cálculo, calendario, ficha de producto y reportes).
* ``respuesta_pdf``: arma la respuesta HTTP ``application/pdf`` para descargar.
"""

from django.http import HttpResponse


def respuesta_pdf(contenido, nombre_archivo):
    """Respuesta de descarga con un PDF válido (``application/pdf``)."""
    respuesta = HttpResponse(contenido, content_type='application/pdf')
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    respuesta['Content-Length'] = str(len(contenido))
    return respuesta
