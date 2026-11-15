"""Plantilla común de los informes PDF de AgroAsesor (ReportLab).

Todos los PDF de la plataforma (cálculos, calendario, ficha de producto y reportes) se arman con
:class:`InformePDF`, de modo que comparten la misma identidad gráfica:

* hoja A4 (vertical u horizontal) con márgenes de 18 mm;
* encabezado con la marca, título, subtítulo y fecha de generación en la primera página, y un encabezado
  reducido en las siguientes;
* tipografía Outfit (la misma de la web), incluida en ``core/pdf/fuentes`` bajo licencia OFL;
* colores de la plataforma: verde ``#0B6B4B``, verde suave ``#E6F7EF``, tinta ``#0F1B2D`` y grises neutros;
* pie de página con el nombre de la plataforma, la nota de que el contenido es orientativo y «Página X de Y».

Cada sección se agrupa con ``KeepTogether`` para que un título no quede separado de su contenido ni una tabla
corta se parta entre dos páginas. Las tablas largas (reportes) sí pueden continuar en la página siguiente y
repiten su fila de títulos.

Uso::

    informe = InformePDF('Informe de cálculo de semillas', 'Potrero chico · Fundo Yarek')
    informe.estado('Simulación con datos ingresados', 'aviso')
    informe.seccion('Información del terreno', informe.datos([('Predio', 'Fundo Yarek'), ...]))
    contenido = informe.generar()   # bytes de un PDF válido
"""

import io
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------------------------------------------
# Identidad visual
# ---------------------------------------------------------------------------------------------------------------

VERDE = colors.HexColor('#0B6B4B')
VERDE_CLARO = colors.HexColor('#34B27B')
VERDE_SUAVE = colors.HexColor('#E6F7EF')
TINTA = colors.HexColor('#0F1B2D')
GRIS = colors.HexColor('#64748B')
LINEA = colors.HexColor('#E2E8F0')
FONDO = colors.HexColor('#F8FAFC')
AMBAR = colors.HexColor('#B7791F')
AMBAR_SUAVE = colors.HexColor('#FDF6E3')
ROJO = colors.HexColor('#B42318')
ROJO_SUAVE = colors.HexColor('#FEF3F2')

TONOS = {
    'ok': (VERDE, VERDE_SUAVE),
    'aviso': (AMBAR, AMBAR_SUAVE),
    'alerta': (ROJO, ROJO_SUAVE),
    'neutro': (GRIS, FONDO),
}

NOTA_ORIENTATIVA = (
    'Los resultados de este documento son orientativos y no reemplazan la evaluación de un profesional agrónomo.'
)
MARGEN = 18 * mm

_FUENTES = Path(__file__).resolve().parent / 'fuentes'
FUENTE = 'Outfit'
FUENTE_NEGRITA = 'Outfit-Bold'


def _registrar_fuentes():
    """Registra Outfit una sola vez. Si faltara el archivo, se usa Helvetica para no impedir el informe."""
    global FUENTE, FUENTE_NEGRITA
    if FUENTE in pdfmetrics.getRegisteredFontNames():
        return
    try:
        pdfmetrics.registerFont(TTFont('Outfit', str(_FUENTES / 'Outfit-Regular.ttf')))
        pdfmetrics.registerFont(TTFont('Outfit-Bold', str(_FUENTES / 'Outfit-Bold.ttf')))
        pdfmetrics.registerFontFamily('Outfit', normal='Outfit', bold='Outfit-Bold')
    except Exception:  # pragma: no cover - solo si el archivo de fuente no está en el despliegue
        FUENTE, FUENTE_NEGRITA = 'Helvetica', 'Helvetica-Bold'


_registrar_fuentes()

# Caracteres que la fuente no incluye: se reemplazan por equivalentes legibles.
_REEMPLAZOS = str.maketrans({'₂': '2', '₅': '5', '₃': '3', '≤': '<=', '≥': '>=', '⚠': '', '✔': '✓', ' ': ' '})


def texto(valor, vacio='—'):
    """Texto seguro para un párrafo de ReportLab (escapa <, > y &; quita caracteres sin glifo)."""
    if valor is None or valor == '':
        return vacio
    return escape(str(valor).translate(_REEMPLAZOS).strip()) or vacio


def numero(valor, decimales=2):
    """Número en formato chileno (1.234,56) sin ceros sobrantes. ``None`` o texto inválido → «—»."""
    try:
        cifra = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return '—'
    if not cifra.is_finite():
        return '—'
    redondeado = round(cifra, decimales)
    cuerpo = f'{redondeado:,.{decimales}f}' if decimales else f'{redondeado:,.0f}'
    cuerpo = cuerpo.replace(',', 'X').replace('.', ',').replace('X', '.')
    if decimales:
        cuerpo = cuerpo.rstrip('0').rstrip(',')
    return '0' if cuerpo in {'-0', ''} else cuerpo


def fecha(valor, con_hora=False):
    if not valor:
        return '—'
    if hasattr(valor, 'hour') and timezone.is_aware(valor):
        valor = timezone.localtime(valor)
    return valor.strftime('%d/%m/%Y %H:%M' if con_hora and hasattr(valor, 'hour') else '%d/%m/%Y')


# ---------------------------------------------------------------------------------------------------------------
# Estilos de texto
# ---------------------------------------------------------------------------------------------------------------


def _estilos():
    base = ParagraphStyle('base', fontName=FUENTE, fontSize=9.5, leading=13, textColor=TINTA, alignment=TA_LEFT)
    return {
        'base': base,
        'chico': ParagraphStyle('chico', parent=base, fontSize=8, leading=10.5, textColor=GRIS),
        'etiqueta': ParagraphStyle('etiqueta', parent=base, fontSize=7.5, leading=9.5, textColor=GRIS),
        'valor': ParagraphStyle('valor', parent=base, fontName=FUENTE_NEGRITA, fontSize=10, leading=13),
        'seccion': ParagraphStyle(
            'seccion', parent=base, fontName=FUENTE_NEGRITA, fontSize=12.5, leading=16, textColor=VERDE,
            spaceBefore=4, spaceAfter=5,
        ),
        'subseccion': ParagraphStyle(
            'subseccion', parent=base, fontName=FUENTE_NEGRITA, fontSize=10, leading=13, spaceBefore=2, spaceAfter=3
        ),
        'celda': ParagraphStyle('celda', parent=base, fontSize=8, leading=10),
        'celda_titulo': ParagraphStyle(
            'celda_titulo', parent=base, fontName=FUENTE_NEGRITA, fontSize=8, leading=10, textColor=colors.white
        ),
        'resultado_etiqueta': ParagraphStyle(
            'resultado_etiqueta', parent=base, fontName=FUENTE_NEGRITA, fontSize=8.5, leading=11, textColor=VERDE
        ),
        'resultado_valor': ParagraphStyle(
            'resultado_valor', parent=base, fontName=FUENTE_NEGRITA, fontSize=28, leading=31, textColor=TINTA
        ),
        'resultado_detalle': ParagraphStyle('resultado_detalle', parent=base, fontSize=9.5, leading=13),
        'formula': ParagraphStyle('formula', parent=base, fontSize=10.5, leading=15),
        'derecha': ParagraphStyle('derecha', parent=base, alignment=TA_RIGHT),
    }


ESTILOS = _estilos()


# ---------------------------------------------------------------------------------------------------------------
# Lienzo con «Página X de Y»
# ---------------------------------------------------------------------------------------------------------------


class _LienzoNumerado(rl_canvas.Canvas):
    """Canvas que guarda las páginas y, al final, dibuja el pie con el total de páginas."""

    def __init__(self, *args, informe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas = []
        self._informe = informe

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self._informe._dibujar_pie(self, total)
            super().showPage()
        super().save()


# ---------------------------------------------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------------------------------------------


class InformePDF:
    """Documento PDF con la identidad de AgroAsesor. Se le agregan bloques y se genera con :meth:`generar`."""

    def __init__(self, titulo, subtitulo='', *, referencia='', horizontal=False, autor=''):
        self.titulo = titulo
        self.subtitulo = subtitulo
        self.referencia = referencia  # por ejemplo «Cálculo N.º 12 · registrado el 15/11/2026 10:20»
        self.autor = autor
        self.generado = timezone.localtime()
        self.tamano = landscape(A4) if horizontal else A4
        self.ancho_util = self.tamano[0] - 2 * MARGEN
        self.bloques = []

    # ---- bloques -------------------------------------------------------------------------------------------

    def estado(self, mensaje, tono='neutro', detalle=''):
        """Franja de estado bajo el encabezado (p. ej. «Simulación con datos ingresados»)."""
        borde, fondo = TONOS[tono]
        contenido = [Paragraph(f'<font name="{FUENTE_NEGRITA}" color="{borde.hexval()}">{texto(mensaje)}</font>',
                               ESTILOS['base'])]
        if detalle:
            contenido.append(Paragraph(texto(detalle), ESTILOS['chico']))
        tabla = Table([[contenido]], colWidths=[self.ancho_util])
        tabla.setStyle(
            TableStyle(
                [
                    ('BACKGROUND', (0, 0), (-1, -1), fondo),
                    ('LINEBEFORE', (0, 0), (0, -1), 3, borde),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('TOPPADDING', (0, 0), (-1, -1), 7),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                ]
            )
        )
        self.bloques += [tabla, Spacer(1, 8)]

    def seccion(self, titulo, *contenido, numero_seccion=None, dividir=False):
        """Título de sección con línea verde y su contenido. Por defecto no se separa entre páginas."""
        rotulo = f'{numero_seccion}. {titulo}' if numero_seccion else titulo
        cabecera = [Paragraph(texto(rotulo), ESTILOS['seccion']), _Linea(self.ancho_util)]
        cuerpo = [bloque for bloque in contenido if bloque is not None]
        if dividir:
            # Contenido largo (tablas de reportes, textos agronómicos): puede seguir en la página siguiente, pero
            # el título nunca queda solo al final de una página.
            self.bloques += [CondPageBreak(45 * mm), *cabecera, *cuerpo, Spacer(1, 7)]
        else:
            self.bloques += [KeepTogether(cabecera + cuerpo), Spacer(1, 7)]

    def datos(self, filas, columnas=2):
        """Tabla de pares «etiqueta / valor» en ``columnas`` columnas. Omite filas con valor ``None``."""
        pares = [(etiqueta, valor) for etiqueta, valor in filas if valor is not None]
        if not pares:
            return None
        ancho = self.ancho_util / columnas
        celdas = [
            [Paragraph(texto(etiqueta), ESTILOS['etiqueta']), Paragraph(texto(valor), ESTILOS['valor'])]
            for etiqueta, valor in pares
        ]
        filas_tabla = []
        for inicio in range(0, len(celdas), columnas):
            grupo = celdas[inicio : inicio + columnas]
            grupo += [''] * (columnas - len(grupo))
            filas_tabla.append(grupo)
        tabla = Table(filas_tabla, colWidths=[ancho] * columnas)
        estilo = [
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, -1), FONDO),
            ('BOX', (0, 0), (-1, -1), 0.6, LINEA),
            ('INNERGRID', (0, 0), (-1, -1), 0.6, LINEA),
            ('LEFTPADDING', (0, 0), (-1, -1), 9),
            ('RIGHTPADDING', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]
        return _con_estilo(tabla, estilo)

    def tabla(self, columnas, filas, anchos=None):
        """Tabla de datos con fila de títulos verde (se repite si la tabla continúa en otra página)."""
        # Con muchas columnas (cuaderno de campo) la letra baja un punto para que todo quepa en la hoja.
        celda, titulo = ESTILOS['celda'], ESTILOS['celda_titulo']
        if len(columnas) > 10:
            celda = ParagraphStyle('celda_chica', parent=celda, fontSize=7, leading=8.6)
            titulo = ParagraphStyle('titulo_chico', parent=titulo, fontSize=7, leading=8.6)
        cabecera = [Paragraph(texto(c), titulo) for c in columnas]
        cuerpo = [[Paragraph(texto(c, vacio=''), celda) for c in fila] for fila in filas]
        if not cuerpo:
            return Paragraph('No hay registros para los filtros elegidos.', ESTILOS['chico'])
        if not anchos:
            # Ancho proporcional al contenido: la palabra más larga del título o el largo promedio de la columna.
            anchos = []
            for i, columna in enumerate(columnas):
                textos = [str(fila[i] or '') for fila in filas]
                promedio = sum(len(t) for t in textos) / max(1, len(textos))
                palabras = str(columna).split() + [p for t in textos for p in t.split()]
                palabra = min(18, max((len(p) for p in palabras), default=4))
                anchos.append(max(palabra + 1, min(28, promedio), 5))
        total = sum(anchos)
        anchos = [self.ancho_util * a / total for a in anchos]
        tabla = Table([cabecera] + cuerpo, colWidths=anchos, repeatRows=1)
        estilo = [
            ('BACKGROUND', (0, 0), (-1, 0), VERDE),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, LINEA),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]
        estilo += [('BACKGROUND', (0, i), (-1, i), FONDO) for i in range(2, len(cuerpo) + 1, 2)]
        return _con_estilo(tabla, estilo)

    def resultado(self, etiqueta, valor, unidad, detalle='', tono='ok', extra=None):
        """Tarjeta del resultado principal: rótulo, cifra grande con su unidad y una línea de contexto."""
        borde, fondo = TONOS[tono]
        partes = [
            Paragraph(texto(etiqueta).upper(), ESTILOS['resultado_etiqueta']),
            Paragraph(
                f'{texto(valor)} <font name="{FUENTE_NEGRITA}" size="14" color="{VERDE.hexval()}">'
                f'{texto(unidad, vacio="")}</font>',
                ESTILOS['resultado_valor'],
            ),
        ]
        if detalle:
            partes.append(Paragraph(texto(detalle), ESTILOS['resultado_detalle']))
        for linea in extra or []:
            partes.append(Paragraph(texto(linea), ESTILOS['chico']))
        tabla = Table([[partes]], colWidths=[self.ancho_util])
        tabla.setStyle(
            TableStyle(
                [
                    ('BACKGROUND', (0, 0), (-1, -1), fondo),
                    ('BOX', (0, 0), (-1, -1), 1, borde),
                    ('LEFTPADDING', (0, 0), (-1, -1), 16),
                    ('TOPPADDING', (0, 0), (-1, -1), 9),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 11),
                    ('ROUNDEDCORNERS', [8, 8, 8, 8]),
                ]
            )
        )
        return tabla

    def formula(self, enunciado, operaciones):
        """Recuadro con la fórmula (texto) y las operaciones con los valores usados (una por línea)."""
        lineas = [Paragraph(f'<font name="{FUENTE_NEGRITA}">{texto(enunciado)}</font>', ESTILOS['base'])]
        lineas += [Paragraph(op, ESTILOS['formula']) for op in operaciones]
        tabla = Table([[lineas]], colWidths=[self.ancho_util])
        tabla.setStyle(
            TableStyle(
                [
                    ('BACKGROUND', (0, 0), (-1, -1), colors.white),
                    ('BOX', (0, 0), (-1, -1), 0.8, LINEA),
                    ('LINEBEFORE', (0, 0), (0, -1), 3, VERDE_CLARO),
                    ('LEFTPADDING', (0, 0), (-1, -1), 12),
                    ('TOPPADDING', (0, 0), (-1, -1), 8),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                ]
            )
        )
        return tabla

    def observaciones(self, items):
        """Lista de observaciones; cada ítem es ``(titulo, texto, tono)``."""
        filas = []
        for titulo, detalle, tono in items:
            borde, fondo = TONOS[tono]
            filas.append(
                [
                    Paragraph(
                        f'<font name="{FUENTE_NEGRITA}" color="{borde.hexval()}">{texto(titulo)}</font><br/>'
                        + texto(detalle, vacio='').replace('\n', '<br/>'),
                        ESTILOS['base'],
                    )
                ]
            )
        if not filas:
            return None
        tabla = Table(filas, colWidths=[self.ancho_util])
        estilo = [
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]
        for i, (_titulo, _detalle, tono) in enumerate(items):
            borde, fondo = TONOS[tono]
            estilo += [('BACKGROUND', (0, i), (0, i), fondo), ('LINEBEFORE', (0, i), (0, i), 3, borde)]
        estilo.append(('LINEBELOW', (0, 0), (-1, -2), 2, colors.white))
        return _con_estilo(tabla, estilo)

    def parrafo(self, contenido, estilo='base'):
        return Paragraph(texto(contenido).replace('\n', '<br/>'), ESTILOS[estilo])

    def agregar(self, *bloques):
        self.bloques += [b for b in bloques if b is not None]

    # ---- dibujo de página ----------------------------------------------------------------------------------

    def _logo(self, canvas, x, y, lado):
        """Marca: cuadrado verde redondeado con un brote (dos hojas y un tallo) en blanco."""
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.roundRect(x, y, lado, lado, lado * 0.24, stroke=0, fill=1)
        canvas.setFillColor(VERDE)
        canvas.setStrokeColor(VERDE)
        canvas.setLineWidth(lado * 0.075)
        canvas.setLineCap(1)
        cx = x + lado / 2
        canvas.line(cx, y + lado * 0.2, cx, y + lado * 0.6)
        hoja = canvas.beginPath()  # hoja izquierda
        hoja.moveTo(cx, y + lado * 0.5)
        hoja.curveTo(cx - lado * 0.08, y + lado * 0.78, cx - lado * 0.3, y + lado * 0.8, cx - lado * 0.32, y + lado * 0.74)
        hoja.curveTo(cx - lado * 0.3, y + lado * 0.56, cx - lado * 0.12, y + lado * 0.48, cx, y + lado * 0.5)
        canvas.drawPath(hoja, stroke=0, fill=1)
        hoja = canvas.beginPath()  # hoja derecha
        hoja.moveTo(cx, y + lado * 0.6)
        hoja.curveTo(cx + lado * 0.06, y + lado * 0.84, cx + lado * 0.28, y + lado * 0.88, cx + lado * 0.32, y + lado * 0.82)
        hoja.curveTo(cx + lado * 0.3, y + lado * 0.64, cx + lado * 0.1, y + lado * 0.56, cx, y + lado * 0.6)
        canvas.drawPath(hoja, stroke=0, fill=1)
        canvas.restoreState()

    def _primera_pagina(self, canvas, doc):
        ancho, alto = self.tamano
        banda = 34 * mm
        canvas.saveState()
        canvas.setFillColor(VERDE)
        canvas.rect(0, alto - banda, ancho, banda, stroke=0, fill=1)
        canvas.setFillColor(VERDE_CLARO)
        canvas.rect(0, alto - banda - 1.4 * mm, ancho, 1.4 * mm, stroke=0, fill=1)
        lado = 13 * mm
        self._logo(canvas, MARGEN, alto - banda + (banda - lado) / 2, lado)
        canvas.setFillColor(colors.white)
        canvas.setFont(FUENTE_NEGRITA, 17)
        canvas.drawString(MARGEN + lado + 5 * mm, alto - banda / 2 + 1.2 * mm, 'AgroAsesor')
        canvas.setFont(FUENTE, 8.5)
        canvas.setFillColor(colors.HexColor('#CFEDE0'))
        canvas.drawString(MARGEN + lado + 5 * mm, alto - banda / 2 - 4 * mm, 'Plataforma de gestión agrícola')
        canvas.setFillColor(colors.white)
        canvas.setFont(FUENTE, 8.5)
        derecha = ancho - MARGEN
        canvas.drawRightString(derecha, alto - banda / 2 + 1.2 * mm, f'Generado el {fecha(self.generado, True)}')
        if self.autor:
            canvas.setFillColor(colors.HexColor('#CFEDE0'))
            canvas.drawRightString(derecha, alto - banda / 2 - 4 * mm, f'Preparado para {self.autor}')
        canvas.restoreState()

    def _otras_paginas(self, canvas, doc):
        ancho, alto = self.tamano
        canvas.saveState()
        canvas.setFillColor(VERDE)
        canvas.rect(0, alto - 4 * mm, ancho, 4 * mm, stroke=0, fill=1)
        canvas.setFont(FUENTE_NEGRITA, 8.5)
        canvas.setFillColor(VERDE)
        canvas.drawString(MARGEN, alto - 11 * mm, 'AgroAsesor')
        canvas.setFont(FUENTE, 8.5)
        canvas.setFillColor(GRIS)
        canvas.drawRightString(ancho - MARGEN, alto - 11 * mm, self.titulo)
        canvas.setStrokeColor(LINEA)
        canvas.setLineWidth(0.6)
        canvas.line(MARGEN, alto - 13.5 * mm, ancho - MARGEN, alto - 13.5 * mm)
        canvas.restoreState()

    def _dibujar_pie(self, canvas, total):
        ancho, _alto = self.tamano
        canvas.saveState()
        canvas.setStrokeColor(LINEA)
        canvas.setLineWidth(0.6)
        canvas.line(MARGEN, 15 * mm, ancho - MARGEN, 15 * mm)
        canvas.setFont(FUENTE_NEGRITA, 7.5)
        canvas.setFillColor(VERDE)
        canvas.drawString(MARGEN, 10.5 * mm, 'AgroAsesor')
        canvas.setFont(FUENTE, 7.5)
        canvas.setFillColor(GRIS)
        canvas.drawString(MARGEN + 15 * mm, 10.5 * mm, '· ' + NOTA_ORIENTATIVA)
        canvas.drawRightString(ancho - MARGEN, 10.5 * mm, f'Página {canvas.getPageNumber()} de {total}')
        canvas.restoreState()

    # ---- generación ----------------------------------------------------------------------------------------

    def _portada(self):
        """Título, subtítulo y referencia, arriba del contenido de la primera página."""
        bloques = [
            Paragraph(
                texto(self.titulo),
                ParagraphStyle('titulo', parent=ESTILOS['base'], fontName=FUENTE_NEGRITA, fontSize=20, leading=24),
            )
        ]
        if self.subtitulo:
            bloques.append(
                Paragraph(
                    texto(self.subtitulo),
                    ParagraphStyle('subtitulo', parent=ESTILOS['base'], fontSize=11, leading=15, textColor=GRIS),
                )
            )
        if self.referencia:
            bloques.append(Spacer(1, 2))
            bloques.append(Paragraph(texto(self.referencia), ESTILOS['chico']))
        return bloques + [Spacer(1, 8)]

    def generar(self):
        """Devuelve el PDF como ``bytes``.

        La primera página deja espacio para la banda de la marca; las siguientes, solo para el encabezado reducido.
        """
        salida = io.BytesIO()
        ancho, alto = self.tamano
        abajo = 19 * mm
        documento = BaseDocTemplate(
            salida,
            pagesize=self.tamano,
            leftMargin=MARGEN,
            rightMargin=MARGEN,
            topMargin=20 * mm,
            bottomMargin=abajo,
            title=f'AgroAsesor · {self.titulo}',
            author='AgroAsesor',
            subject=self.subtitulo or self.titulo,
            creator='AgroAsesor',
        )
        marco_primera = Frame(MARGEN, abajo, self.ancho_util, alto - abajo - 37 * mm, id='primera', showBoundary=0)
        marco_siguientes = Frame(MARGEN, abajo, self.ancho_util, alto - abajo - 20 * mm, id='siguientes')
        documento.addPageTemplates(
            [
                PageTemplate(id='primera', frames=[marco_primera], onPage=self._primera_pagina),
                PageTemplate(id='siguientes', frames=[marco_siguientes], onPage=self._otras_paginas),
            ]
        )
        documento.build(
            [NextPageTemplate('siguientes')] + self._portada() + self.bloques,
            canvasmaker=lambda *a, **k: _LienzoNumerado(*a, informe=self, **k),
        )
        return salida.getvalue()


class _Linea(Table):
    """Línea fina verde bajo los títulos de sección."""

    def __init__(self, ancho):
        super().__init__([['']], colWidths=[ancho], rowHeights=[2])
        self.setStyle(
            TableStyle(
                [
                    ('LINEABOVE', (0, 0), (-1, 0), 1.2, VERDE_CLARO),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ]
            )
        )


def _con_estilo(tabla, estilo):
    tabla.setStyle(TableStyle(estilo))
    return tabla
