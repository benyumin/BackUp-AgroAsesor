/* Límites del cálculo: decide si la superficie y la dosis ingresadas se pueden usar.
 *
 * Lo usa calc-panel.js antes de mostrar un resultado (vista previa) y antes de enviarlo al servidor. El servidor
 * (core/calculos.py) aplica las mismas reglas, así que un valor fuera de límite nunca se guarda como válido.
 *
 * Reglas:
 *   · superficie: no puede superar la registrada del potrero;
 *   · dosis: no puede superar la dosis máxima documentada del producto (solo si el catálogo la tiene con fuente y en
 *     la misma unidad). Sin máximo documentado no se inventa un límite.
 *
 * AgroCalcLimites.revisar({...}) → null si todo está bien, o un objeto { tipo, campo, titulo, detalle }.
 */
(function () {
  'use strict';

  const TOLERANCIA = 1e-9;

  function numero(value, digits) {
    return Number(value).toLocaleString('es-CL', { maximumFractionDigits: digits });
  }

  function mismaUnidad(a, b) {
    const limpia = (unit) => String(unit || '').replace(/\/ha$/i, '').trim().toLowerCase();
    return limpia(a) === limpia(b);
  }

  function revisar({ area, plotArea, dose, unit, product }) {
    if (Number.isFinite(area) && Number.isFinite(plotArea) && area > plotArea + TOLERANCIA) {
      return {
        tipo: 'superficie',
        campo: 'area',
        titulo: 'Excede la superficie del potrero',
        detalle: 'Ingresaste ' + numero(area, 4) + ' ha y el potrero tiene ' + numero(plotArea, 4) + ' ha registradas.',
      };
    }
    const maximo = product && product.maxDose !== null && product.maxDose !== undefined ? Number(product.maxDose) : NaN;
    const unidadMaxima = (product && (product.maxDoseUnit || product.unit)) || '';
    if (
      product &&
      product.doseVerified &&
      Number.isFinite(maximo) &&
      Number.isFinite(dose) &&
      mismaUnidad(unidadMaxima, unit) &&
      dose > maximo + TOLERANCIA
    ) {
      return {
        tipo: 'dosis',
        campo: 'dose',
        titulo: 'Excede la dosis máxima permitida',
        detalle: 'El máximo documentado para ' + product.name + ' es ' + numero(maximo, 3) + ' ' + unidadMaxima + '.',
      };
    }
    return null;
  }

  window.AgroCalcLimites = { revisar };
})();
