"""Calculadora agrícola (semillas, insumos y «¿Me alcanza?»).

``decisiones`` muestra la pantalla; ``calculate`` recibe el cálculo en JSON y ``calculo_pdf`` entrega el informe
PDF de un cálculo guardado. Las fórmulas y validaciones están en ``core.calculos`` y el diseño del PDF en
``core.pdf``.
"""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from ..calculos import ErrorCalculo, ejecutar_calculo
from ..models import CalculoAgricola
from ..pdf import respuesta_pdf
from ..pdf.informes import informe_calculo
from .base import render_page


@login_required
@require_POST
def calculate(request):
    """Calcula desde la calculadora (POST JSON) y guarda el resultado en el historial del usuario.

    La validación y las fórmulas están en ``core.calculos.ejecutar_calculo``; esta vista solo revisa el formato
    de la petición, devuelve los errores como JSON y guarda el cálculo. Con ``"preview": true`` calcula sin guardar.
    """
    if request.content_type != 'application/json':
        return JsonResponse({'error': 'Envía los datos como JSON.'}, status=415)
    if len(request.body) > 65536:
        return JsonResponse({'error': 'El cálculo supera el tamaño permitido.'}, status=413)
    try:
        payload = json.loads(request.body)
        calculo = ejecutar_calculo(request.user, payload)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'El cuerpo JSON no se pudo interpretar.'}, status=400)
    except ErrorCalculo as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    if payload.get('preview') is True:
        # Vista previa en pantalla: se calcula con la misma lógica pero NO se guarda en el historial.
        return JsonResponse({'ok': True, 'calculation_id': None, 'preview': True, **calculo.resultado})
    guardado = CalculoAgricola.objects.create(
        usuario=request.user,
        potrero=calculo.potrero,
        operacion=calculo.operacion,
        entrada=calculo.entrada,
        resultado=calculo.resultado,
    )
    return JsonResponse({'ok': True, 'calculation_id': guardado.pk, **calculo.resultado})


@login_required
def calculo_pdf(request, pk):
    """Informe PDF de un cálculo guardado del usuario (solo el dueño puede descargarlo)."""
    calculo = get_object_or_404(
        CalculoAgricola.objects.select_related('potrero__predio', 'potrero__cultivo', 'usuario'),
        pk=pk,
        usuario=request.user,
    )
    tipo = (calculo.entrada or {}).get('calculation_type') or calculo.operacion
    return respuesta_pdf(informe_calculo(calculo), f'agroasesor-calculo-{tipo}-{calculo.pk}.pdf')


def decisiones(request):
    tool = request.GET.get('herramienta', 'llevar')
    if tool not in {'llevar', 'alcanza'}:
        tool = 'llevar'
    return render_page(request, 'decisiones.html', 'decisiones', {'initial_tool': tool})
