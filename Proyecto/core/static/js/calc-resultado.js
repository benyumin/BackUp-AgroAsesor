/* Resumen visual del cálculo (columna derecha de la calculadora de semillas e insumos).
 *
 * Solo arma HTML a partir de un objeto «spec» que prepara calc-panel.js; no calcula ni guarda nada.
 *
 * html(spec)       resultado válido, de arriba hacia abajo:
 *                  cantidad grande → dibujo del terreno → datos (predio, potrero, cultivo, superficie, dosis)
 *                  → fórmula breve → «Más detalles» plegado (tipo de cálculo, producto, origen de la dosis, notas).
 * htmlExceso(spec) valor fuera de límite: el resultado no se muestra como válido; el dibujo y el dato que falla se
 *                  marcan en rojo y se explica en una línea qué límite se superó.
 *
 * Formato de números: superficie con hasta 4 decimales (la precisión con que se guarda el potrero), dosis con hasta 3
 * y cantidad con hasta 2 (3 si es menor que 1). Los cálculos usan siempre el valor completo.
 */
(function () {
  'use strict';

  function esc(value) {
    return String(value == null ? '' : value).replace(
      /[&<>"']/g,
      (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char],
    );
  }

  function numero(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString('es-CL', { maximumFractionDigits: digits });
  }
  const superficie = (value) => numero(value, 4);
  const dosis = (value) => numero(value, 3);
  const cantidad = (value) => numero(value, Math.abs(Number(value)) < 1 ? 3 : 2);

  function safeUrl(value) {
    if (!value) return '';
    try {
      const url = new URL(value, window.location.origin);
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : '';
    } catch (error) {
      return '';
    }
  }

  // «0,1496 ha × 100 kg/ha = 14,96 kg» en una línea de texto (se usa en «Más detalles»).
  function formula(area, dose, doseUnit, amount, unit) {
    return (
      esc(superficie(area)) +
      ' ha × ' +
      esc(dosis(dose)) +
      ' ' +
      esc(doseUnit) +
      ' = <strong>' +
      esc(cantidad(amount)) +
      ' ' +
      esc(unit) +
      '</strong>'
    );
  }

  function filas(rows, className, marcar) {
    const shown = (rows || []).filter((row) => row[1] !== '' && row[1] !== null && row[1] !== undefined);
    if (!shown.length) return '';
    return (
      '<dl class="' +
      className +
      '">' +
      shown
        .map(
          (row) =>
            '<div' +
            (marcar && marcar === row[2] ? ' class="is-error"' : '') +
            '><dt>' +
            esc(row[0]) +
            '</dt><dd>' +
            esc(row[1]) +
            '</dd></div>',
        )
        .join('') +
      '</dl>'
    );
  }

  // Dibujo del terreno (terreno-mini.js): predio, potrero y superficie aplicada o excedida.
  function terreno(spec, exceso) {
    const T = window.AgroTerrenoMini;
    if (!T || !spec.terrain) return '';
    const opts = Object.assign(
      { areaHa: spec.area, exceso, label: 'Terreno del cálculo: ' + (spec.plotName || '') },
      spec.terrain,
    );
    return (
      '<figure class="calc-terreno' +
      (exceso ? ' is-error' : '') +
      '">' +
      T.svg(opts) +
      '<figcaption>' +
      T.legend(opts) +
      '</figcaption></figure>'
    );
  }

  function contexto(spec, marcar) {
    return filas(
      [
        ['Predio', spec.farmName],
        ['Potrero', spec.plotName],
        ['Cultivo', spec.cropName],
        ['Superficie usada', superficie(spec.area) + ' ha', 'area'],
        [spec.doseLabel || 'Dosis aplicada', dosis(spec.dose) + ' ' + spec.doseUnit, 'dose'],
      ],
      'calc-ctx-grid',
      marcar,
    );
  }

  // Fórmula breve: [superficie] × [dosis] = resultado.
  function formulaCorta(spec) {
    return (
      '<p class="calc-formula-chip" aria-label="Fórmula del cálculo"><span>' +
      esc(superficie(spec.area)) +
      ' ha</span><b aria-hidden="true">×</b><span>' +
      esc(dosis(spec.dose)) +
      ' ' +
      esc(spec.doseUnit) +
      '</span><b aria-hidden="true">=</b><strong>' +
      esc(cantidad(spec.amount)) +
      ' ' +
      esc(spec.unit) +
      '</strong></p>'
    );
  }

  function detalles(spec) {
    const contenido =
      (spec.formulaTitle
        ? '<div class="formula-box"><p class="formula-line">' +
          esc(spec.formulaTitle) +
          '</p><p>' +
          spec.formulaHtml +
          '</p></div>'
        : '') +
      filas(spec.facts, 'calc-facts') +
      (spec.sourceUrl && safeUrl(spec.sourceUrl)
        ? '<p class="calc-note"><a class="text-link" href="' +
          esc(safeUrl(spec.sourceUrl)) +
          '" target="_blank" rel="noopener noreferrer">Ver fuente técnica</a></p>'
        : '') +
      (spec.notes || []).map((note) => '<p class="muted calc-note">' + esc(note) + '</p>').join('');
    return contenido ? '<details class="calc-details"><summary>Más detalles</summary>' + contenido + '</details>' : '';
  }

  function encabezado(kicker, etiqueta) {
    return '<div class="calc-result-top"><p class="result-kicker">' + esc(kicker) + '</p>' + etiqueta + '</div>';
  }

  function html(spec) {
    // Una sola etiqueta: «Vista previa» mientras no se guarda; «Guardado» después.
    const etiqueta = spec.saved
      ? '<span class="calc-tag is-ok">Guardado</span>'
      : '<span class="calc-tag is-preview">Vista previa</span>';
    return (
      encabezado(spec.kicker, etiqueta) +
      '<p class="result-value"><strong>' +
      esc(cantidad(spec.amount)) +
      '</strong><span>' +
      esc(spec.unit) +
      '</span></p>' +
      terreno(spec) +
      contexto(spec) +
      formulaCorta(spec) +
      detalles(spec)
    );
  }

  // Valor fuera de límite: no se muestra una cantidad como si fuera válida.
  function htmlExceso(spec) {
    const problema = spec.problema;
    return (
      encabezado(spec.kicker, '<span class="calc-tag is-error">No calculado</span>') +
      '<div class="calc-exceso" role="alert"><strong>' +
      esc(problema.titulo) +
      '</strong><span>' +
      esc(problema.detalle) +
      '</span><span>No es posible calcular porque supera el límite. Corrige el valor marcado.</span></div>' +
      terreno(spec, problema.tipo) +
      contexto(spec, problema.campo)
    );
  }

  window.AgroCalcResultado = { html, htmlExceso, formula, esc, safeUrl, superficie, dosis, cantidad };
})();
