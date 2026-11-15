/* Mini visualización del terreno (SVG liviano, sin mapa ni librerías).
 *
 * Dibuja de forma esquemática:
 *   · el contorno del predio (línea gris punteada),
 *   · los otros potreros del predio (gris claro),
 *   · el potrero seleccionado (verde),
 *   · la superficie aplicada cuando es menor que la del potrero (trama verde oscuro). Como el sistema no guarda
 *     dónde está esa parte dentro del potrero, se dibuja proporcional y se rotula como referencial;
 *   · con ``exceso: 'superficie'`` el potrero se marca en rojo y se dibuja, punteado, el tamaño ingresado para que se
 *     vea que no cabe; con ``exceso: 'dosis'`` el potrero se marca en rojo.
 *
 * Usa la geometría que ya entrega la página (agro-farms: geojson del predio y perímetro de cada potrero, en
 * [longitud, latitud]). Si no hay contornos dibujados, muestra un esquema proporcional a las superficies.
 *
 * Uso:  AgroTerrenoMini.svg({ farm, plot, others, areaHa, plotAreaHa, farmAreaHa, exceso })  → texto SVG
 */
(function () {
  'use strict';

  const W = 320;
  const H = 180;
  const PAD = 12;

  // Anillo [[lng, lat], ...] desde un GeoJSON (Polygon o Feature) o una lista de coordenadas.
  function ring(value) {
    if (!value) return null;
    let coords = value;
    if (value.type === 'Feature') coords = value.geometry;
    if (coords && coords.type === 'Polygon') coords = (coords.coordinates || [])[0];
    if (!Array.isArray(coords)) return null;
    const points = coords.filter(
      (p) => Array.isArray(p) && Number.isFinite(Number(p[0])) && Number.isFinite(Number(p[1])),
    );
    return points.length >= 3 ? points.map((p) => [Number(p[0]), Number(p[1])]) : null;
  }

  // Proyección simple: longitud corregida por el coseno de la latitud, ajustada al cuadro con márgenes.
  function projector(rings) {
    const all = rings.flat();
    const lat0 = (all.reduce((sum, p) => sum + p[1], 0) / all.length) * (Math.PI / 180);
    const xs = all.map((p) => p[0] * Math.cos(lat0));
    const ys = all.map((p) => p[1]);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const scale = Math.min((W - 2 * PAD) / (maxX - minX || 1e-9), (H - 2 * PAD) / (maxY - minY || 1e-9));
    const offX = (W - (maxX - minX) * scale) / 2;
    const offY = (H - (maxY - minY) * scale) / 2;
    return (p) => [offX + (p[0] * Math.cos(lat0) - minX) * scale, H - (offY + (p[1] - minY) * scale)];
  }

  function path(points) {
    return points.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ') + ' Z';
  }

  function bbox(points) {
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  }

  // Sin contornos: rectángulos proporcionales a las superficies registradas.
  function schematic(opts) {
    const farmArea = Number(opts.farmAreaHa) || Number(opts.plotAreaHa) || 1;
    const share = Math.min(1, (Number(opts.plotAreaHa) || farmArea) / farmArea);
    const fw = W - 2 * PAD - 40;
    const fh = H - 2 * PAD;
    const side = Math.sqrt(share);
    const pw = Math.max(26, fw * side);
    const ph = Math.max(20, fh * side);
    const farm = [[PAD + 20, PAD], [PAD + 20 + fw, PAD], [PAD + 20 + fw, PAD + fh], [PAD + 20, PAD + fh]];
    const x0 = PAD + 20 + (fw - pw) / 2;
    const y0 = PAD + (fh - ph) / 2;
    const plot = [[x0, y0], [x0 + pw, y0], [x0 + pw, y0 + ph], [x0, y0 + ph]];
    return { farm, plot, others: [], schematic: true };
  }

  function svg(opts) {
    const farmRing = ring(opts.farm);
    const plotRing = ring(opts.plot);
    const otherRings = (opts.others || []).map(ring).filter(Boolean);
    let shapes;
    if (plotRing) {
      const project = projector([farmRing, plotRing].concat(otherRings).filter(Boolean));
      shapes = {
        farm: farmRing ? farmRing.map(project) : null,
        plot: plotRing ? plotRing.map(project) : null,
        others: otherRings.map((r) => r.map(project)),
        schematic: false,
      };
    } else {
      shapes = schematic(opts);
    }
    const area = Number(opts.areaHa);
    const plotArea = Number(opts.plotAreaHa);
    const fraction = plotArea > 0 && area > 0 ? Math.min(1, area / plotArea) : 1;
    const partial = shapes.plot && fraction < 0.999 && !opts.exceso;
    const id = 'tm' + Math.random().toString(36).slice(2, 8);
    let applied = '';
    if (opts.exceso === 'superficie' && shapes.plot && plotArea > 0 && area > plotArea) {
      // Tamaño ingresado: el mismo contorno agrandado en proporción a la superficie (punteado rojo).
      const box = bbox(shapes.plot);
      const cx = box.x + box.w / 2;
      const cy = box.y + box.h / 2;
      const k = Math.min(2.4, Math.sqrt(area / plotArea));
      const grande = shapes.plot.map((p) => [cx + (p[0] - cx) * k, cy + (p[1] - cy) * k]);
      applied = '<path class="tm-exceso" d="' + path(grande) + '"/>';
    } else if (partial) {
      // Parte aplicada: franja proporcional del potrero (desde la izquierda), recortada a su contorno.
      const box = bbox(shapes.plot);
      applied =
        '<clipPath id="' + id + '-c"><path d="' + path(shapes.plot) + '"/></clipPath>' +
        '<rect class="tm-applied" clip-path="url(#' + id + '-c)" x="' + box.x.toFixed(1) + '" y="' +
        box.y.toFixed(1) + '" width="' + (box.w * fraction).toFixed(1) + '" height="' + box.h.toFixed(1) +
        '" fill="url(#' + id + '-h)"/>';
    }
    return (
      '<svg class="terreno-mini" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' +
      (opts.label || 'Esquema del terreno') + '">' +
      '<defs><pattern id="' + id + '-h" width="6" height="6" patternUnits="userSpaceOnUse" ' +
      'patternTransform="rotate(45)"><rect width="6" height="6" fill="#0b6b4b" fill-opacity="0.55"/>' +
      '<line x1="0" y1="0" x2="0" y2="6" stroke="#0b6b4b" stroke-width="3"/></pattern></defs>' +
      (shapes.farm ? '<path class="tm-farm" d="' + path(shapes.farm) + '"/>' : '') +
      shapes.others.map((p) => '<path class="tm-other" d="' + path(p) + '"/>').join('') +
      (shapes.plot
        ? '<path class="tm-plot' + (opts.exceso ? ' is-error' : '') + '" d="' + path(shapes.plot) + '"/>'
        : '') +
      applied +
      '</svg>'
    );
  }

  // Leyenda corta que acompaña al dibujo.
  function legend(opts) {
    const area = Number(opts.areaHa);
    const plotArea = Number(opts.plotAreaHa);
    const partial = plotArea > 0 && area > 0 && area / plotArea < 0.999 && !opts.exceso;
    return (
      '<ul class="terreno-mini-legend">' +
      '<li><span class="tm-key is-farm"></span>Predio</li>' +
      '<li><span class="tm-key is-plot' + (opts.exceso ? ' is-error' : '') + '"></span>Potrero</li>' +
      (partial ? '<li><span class="tm-key is-applied"></span>Superficie aplicada (referencial)</li>' : '') +
      (opts.exceso === 'superficie'
        ? '<li><span class="tm-key is-exceso"></span>Superficie ingresada (no cabe en el potrero)</li>'
        : '') +
      (ring(opts.plot) ? '' : '<li class="tm-note">Esquema proporcional: el potrero no tiene contorno dibujado</li>') +
      '</ul>'
    );
  }

  window.AgroTerrenoMini = { svg, legend, ring };
})();
