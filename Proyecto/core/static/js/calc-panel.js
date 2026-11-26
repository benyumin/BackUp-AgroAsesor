/* «Cuánto llevar»: calculadora de semillas e insumos.
   - Mientras escribes se muestra una vista previa (superficie × dosis) que no guarda nada.
   - «Calcular ahora» envía los datos a /calcular/ (vista core.views.calculadora.calculate), que valida con las
     reglas del catálogo (core/calculos.py) y guarda el resultado en la cuenta.
   - La tarjeta de resultado se dibuja en calc-resultado.js; los pasos guiados y tarjetas de selección, en
     calc-flow.js. */
(function () {
  'use strict';

  const hub = document.querySelector('.decision-center');
  const root = document.getElementById('tool-llevar');
  if (!hub || !root) return;

  function readJson(id, fallback) {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent) || fallback;
    } catch (error) {
      return fallback;
    }
  }

  const catalog = readJson('agro-catalog', {});
  const farm = readJson('agro-farms', { predios: [], plots: [] });
  const calculateUrl = hub.dataset.calculateUrl;
  const pdfUrlTemplate = hub.dataset.pdfUrl;
  const isStaff = hub.dataset.staff === '1';
  const plots = farm.plots || [];
  const crops = (catalog.cultivos || []).filter((crop) => crop.active !== false);
  const allProducts = catalog.insumos || [];
  const products = allProducts.filter((item) => item.usable !== false && item.active !== false);
  const categoryLabels = catalog.categoryLabels || {};
  const PHYTO = new Set(['herbicide', 'fungicide', 'insecticide']);
  const PHYTO_UNITS = ['kg/ha', 'g/ha', 'L/ha', 'mL/ha'];
  const SEED_UNITS = [
    ['kg/ha', 'kg'],
    ['g/ha', 'g'],
    ['unidades/ha', 'unidades'],
  ];
  const INPUT_UNITS = [
    ['kg/ha', 'kg'],
    ['L/ha', 'L'],
    ['g/ha', 'g'],
    ['mL/ha', 'mL'],
    ['unidades/ha', 'unidades'],
  ];
  const params = new URLSearchParams(window.location.search);
  // «potrero» es el nombre público del parámetro; «plot» se mantiene por compatibilidad con enlaces anteriores.
  if (!params.has('plot') && params.has('potrero')) params.set('plot', params.get('potrero'));

  const $ = (id) => document.getElementById(id);
  const R = window.AgroCalcResultado;
  const L = window.AgroCalcLimites;
  if (!R || !L) return;
  const ui = {
    modes: Array.from(document.querySelectorAll('#calc-modes [data-mode]')),
    form: $('calc-form'),
    title: $('calc-title'),
    hint: $('calc-hint'),
    plot: $('calc-plot'),
    plotHelp: $('calc-plot-help'),
    crop: $('calc-crop'),
    cropHelp: $('calc-crop-help'),
    productField: $('calc-product-field'),
    productLabel: $('calc-product-label'),
    product: $('calc-product'),
    productHelp: $('calc-product-help'),
    targetField: $('calc-target-field'),
    target: $('calc-target'),
    area: $('calc-area'),
    dose: $('calc-dose'),
    doseLabel: $('calc-dose-label'),
    doseHelp: $('calc-dose-help'),
    doseOrigin: $('calc-dose-origin'),
    doseSource: $('calc-dose-source'),
    doseSourceField: $('calc-dose-source-field'),
    unit: $('calc-unit'),
    examples: $('calc-examples'),
    exampleRow: $('calc-example-row'),
    error: $('calc-error'),
    submit: $('calc-submit'),
    pdf: $('calc-pdf'),
    result: $('calc-result'),
    notes: $('calc-notes'),
    productInfo: $('calc-product-info'),
    plan: $('calc-plan'),
    sagSearch: $('calc-sag-search'),
  };
  if (!ui.form) return;

  let mode = 'semillas';
  // Producto pedido por la URL (?insumo=): solo se aplica al abrir la página.
  let requestedProduct = params.get('insumo');

  // ---------- utilidades ----------
  function esc(value) {
    return String(value == null ? '' : value).replace(
      /[&<>"']/g,
      (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char],
    );
  }
  function fmt(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString('es-CL', { maximumFractionDigits: digits === undefined ? 2 : digits });
  }
  function fmtDate(value) {
    if (!value) return '';
    const parts = String(value).split('-');
    return parts.length === 3 ? parts[2] + '/' + parts[1] + '/' + parts[0] : value;
  }
  function positive(value) {
    const number = Number(String(value).replace(',', '.'));
    return Number.isFinite(number) && number > 0 ? number : null;
  }
  function safeUrl(value) {
    if (!value) return '';
    try {
      const url = new URL(value, window.location.origin);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (error) {
      return '';
    }
  }
  function cropById(id) {
    return crops.find((crop) => String(crop.id) === String(id)) || null;
  }
  function plotById(id) {
    return plots.find((plot) => String(plot.id) === String(id)) || null;
  }
  function productById(id) {
    const found = allProducts.find((item) => String(item.id) === String(id));
    return found || (window.AgroCatalogoSAG && window.AgroCatalogoSAG.get(id)) || null;
  }
  // Producto de la lista SAG elegido en el buscador: se suma a la lista de esta calculadora (sin dosis).
  function addSagProduct(item) {
    if (!item || allProducts.some((known) => String(known.id) === String(item.id))) return;
    allProducts.push(item);
    if (item.usable !== false && item.active !== false) products.push(item);
  }
  // Estado en la lista SAG (vigente, histórico o sin registro) con los textos comunes de catalogo-sag.js.
  function sagEstado(item) {
    return window.AgroCatalogoSAG ? window.AgroCatalogoSAG.estado(item) : { key: '', short: '', long: '' };
  }
  function isPhyto(item) {
    return Boolean(item) && PHYTO.has(item.category);
  }
  function belongsToCrop(item, crop) {
    if (!crop) return !item.cropId && !item.crop;
    return !item.cropId ? !item.crop || item.crop === crop.name : String(item.cropId) === String(crop.id);
  }
  function verifiedPhytoFor(item, crop) {
    return (
      item.verifiedUse === true &&
      crop &&
      item.crop === crop.name &&
      Array.isArray(item.authorizedCrops) &&
      item.authorizedCrops.includes(crop.name) &&
      PHYTO_UNITS.includes(item.unit)
    );
  }
  function missingData(item, crop) {
    const missing = [];
    if (item.verificationStatus !== 'verified') missing.push('verificación del producto con fuente');
    if (item.dose === null || item.dose === undefined) missing.push('dosis por hectárea');
    else if (!item.doseVerified) missing.push('verificación de la dosis (fuente, URL y fecha)');
    if (!item.unit) missing.push('unidad de la dosis');
    if (isPhyto(item)) {
      if (!item.sagRegistration) missing.push('número de autorización SAG');
      if (!item.labelUrl) missing.push('etiqueta autorizada');
      if (!crop || !(item.authorizedCrops || []).includes(crop.name)) missing.push('cultivo autorizado en etiqueta');
      if (!(item.controlTargets || []).length) missing.push('objetivo de control');
    }
    return missing;
  }
  function csrfToken() {
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }
  async function calculate(payload) {
    const response = await fetch(calculateUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken(), Accept: 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
      throw new Error(
        response.redirected
          ? 'Tu sesión expiró. Inicia sesión nuevamente.'
          : 'El servidor devolvió una respuesta inesperada.',
      );
    }
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || 'El servidor no pudo validar el cálculo.');
    return data;
  }
  function optionFor(item) {
    const option = new Option(item.label, item.value);
    if (item.disabled) option.disabled = true;
    return option;
  }
  // placeholder === null: sin opción vacía (se elige la primera disponible).
  // keep === false: no se conserva la elección anterior y se vuelve a la opción por defecto.
  function setOptions(select, items, placeholder, keep) {
    const previous = keep === false ? '' : select.value;
    select.replaceChildren();
    if (placeholder !== null && placeholder !== undefined) select.add(new Option(placeholder, ''));
    items.forEach((item) => {
      if (item.group) {
        const group = document.createElement('optgroup');
        group.label = item.group;
        item.items.forEach((child) => group.append(optionFor(child)));
        select.append(group);
      } else {
        select.add(optionFor(item));
      }
    });
    const options = Array.from(select.options);
    if (options.some((option) => option.value === previous && !option.disabled)) {
      select.value = previous;
    } else if (placeholder === null || placeholder === undefined) {
      const first = options.find((option) => !option.disabled);
      if (first) select.value = first.value;
    }
  }
  function hasOption(select, value) {
    return Array.from(select.options).some((option) => option.value === String(value) && !option.disabled);
  }

  // ---------- selección actual ----------
  function selectedPlot() {
    return plotById(ui.plot.value);
  }
  function selectedCrop() {
    return cropById(ui.crop.value);
  }
  function selection() {
    const value = ui.product.value;
    if (mode === 'semillas') {
      if (value.indexOf('p:') === 0) return { kind: 'product', item: productById(value.slice(2)) };
      return { kind: value === 'manual' ? 'manual' : 'crop', item: null };
    }
    if (value === 'manual') return { kind: 'manual', item: null };
    return value ? { kind: 'product', item: productById(value) } : { kind: 'none', item: null };
  }
  function plotLabel() {
    const option = ui.plot.selectedOptions[0];
    return option && option.value ? option.textContent : '';
  }
  // Potrero, predio y su geometría para el resumen visual (terreno-mini.js).
  function plotNames() {
    const plot = selectedPlot();
    if (!plot) return { plotLabel: plotLabel(), plotName: '', farmName: '' };
    const predio = (farm.predios || []).find((item) => String(item.id) === String(plot.predio)) || {};
    return {
      plotLabel: plotLabel(),
      plotName: plot.name,
      farmName: plot.farmName,
      terrain: {
        farm: predio.geojson || null,
        plot: plot.geojson || plot.coordinates || null,
        others: (farm.plots || [])
          .filter((item) => String(item.predio) === String(plot.predio) && String(item.id) !== String(plot.id))
          .map((item) => item.geojson || item.coordinates),
        plotAreaHa: plot.areaHa,
        farmAreaHa: predio.area,
      },
    };
  }
  function doseUnitLabel(unit) {
    return String(unit || 'kg/ha');
  }
  function resultUnit(unit) {
    return doseUnitLabel(unit).split('/')[0];
  }

  // ---------- notas ----------
  function productCard(item, crop) {
    const status =
      { verified: 'Verificado con fuente', restricted: 'Restringido', cancelled: 'Registro cancelado' }[
        item.verificationStatus
      ] || 'Información no verificada';
    const facts = [];
    const add = (label, value) => {
      if (value !== null && value !== undefined && value !== '') facts.push([label, value]);
    };
    add('Categoría', item.categoryLabel || item.type);
    add('Fabricante', item.manufacturer);
    add('Composición', item.nutrientComposition);
    add('Ingrediente activo', item.activeIngredient);
    add('Concentración', item.concentration);
    add('Variedad', item.seedVariety);
    add('Tipo de semilla', item.seedType);
    add(
      'Germinación',
      item.germinationPct !== null && item.germinationPct !== undefined ? fmt(item.germinationPct) + ' %' : '',
    );
    add('Pureza', item.purityPct !== null && item.purityPct !== undefined ? fmt(item.purityPct) + ' %' : '');
    add('Peso de mil semillas', item.thousandSeedWeightG ? fmt(item.thousandSeedWeightG) + ' g' : '');
    add(
      'Dosis registrada',
      item.dose !== null && item.dose !== undefined
        ? fmt(item.dose, 3) +
            ' ' +
            (item.unit || '(sin unidad)') +
            (item.doseVerified ? ' · verificada' : ' · sin verificar')
        : 'No documentada',
    );
    add(
      'Presentación comercial',
      item.packageAmount && item.packageUnit ? fmt(item.packageAmount) + ' ' + item.packageUnit : '',
    );
    add('Fuente', item.doseSource || item.sourceName);
    add('Verificado el', fmtDate(item.verifiedAt));
    if (item.sag) {
      add('Lista SAG orgánica', sagEstado(item).long);
      add('Función declarada (SAG)', item.sag.function);
    }
    const url = safeUrl(item.labelUrl || item.sourceUrl);
    return (
      '<div class="note-card"><h3>' +
      esc(item.name) +
      '</h3>' +
      '<span class="badge' +
      (item.verificationStatus === 'verified' ? '' : ' amber') +
      '">' +
      esc(status) +
      '</span>' +
      (item.description ? '<p>' + esc(item.description) + '</p>' : '') +
      (item.applicationMode ? '<p class="muted">' + esc(item.applicationMode) + '</p>' : '') +
      '<dl class="calc-facts">' +
      facts.map((row) => '<dt>' + esc(row[0]) + '</dt><dd>' + esc(row[1]) + '</dd>').join('') +
      '</dl>' +
      (url
        ? '<a class="text-link" href="' +
          esc(url) +
          '" target="_blank" rel="noopener noreferrer">' +
          (item.labelUrl ? 'Ver etiqueta' : 'Ver ficha técnica') +
          '</a>'
        : '') +
      '</div>'
    );
  }

  // Solo iconografía (no son datos): palabra normalizada del nombre → emoji. Lo demás usa el de su categoría.
  const CROP_EMOJI = {
    maiz: '🌽',
    trigo: '🌾',
    avena: '🌱',
    cebada: '🌾',
    arroz: '🌾',
    centeno: '🌾',
    poroto: '🫘',
    lenteja: '🫘',
    garbanzo: '🫘',
    haba: '🫘',
    arveja: '🌱',
    lupino: '🫘',
    tomate: '🍅',
    cebolla: '🧅',
    lechuga: '🥬',
    repollo: '🥬',
    zapallo: '🎃',
    zanahoria: '🥕',
    ajo: '🧄',
    pimenton: '🫑',
    sandia: '🍉',
    melon: '🍈',
    choclo: '🌽',
    frutilla: '🍓',
    papa: '🥔',
    remolacha: '🌱',
    camote: '🍠',
    alfalfa: '🌿',
    trebol: '🍀',
    ballica: '🌿',
    vid: '🍇',
    uva: '🍇',
    palto: '🥑',
    cerezo: '🍒',
    manzano: '🍎',
    nogal: '🌰',
    arandano: '🫐',
    olivo: '🫒',
    limonero: '🍋',
    naranjo: '🍊',
    kiwi: '🥝',
    durazno: '🍑',
    peral: '🍐',
    ciruelo: '🍑',
    raps: '🌼',
    maravilla: '🌻',
  };
  const CATEGORY_EMOJI = {
    cereales: '🌾',
    leguminosas: '🫘',
    hortalizas: '🥬',
    tuberculos: '🥔',
    forrajes: '🌿',
    frutales: '🍎',
    industriales: '🌻',
    otros: '🌱',
  };
  const groupKeys = new Map((readJson('agro-crop-picker', {}).groups || []).map((group) => [group.name, group.key]));
  function cropEmoji(crop) {
    if (!crop) return '🌱';
    const words = String(crop.name || '')
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z\s]/g, ' ')
      .split(/\s+/)
      .filter(Boolean);
    for (const word of words) if (CROP_EMOJI[word]) return CROP_EMOJI[word];
    return CATEGORY_EMOJI[groupKeys.get(crop.group)] || '🌱';
  }

  // Ficha del producto elegido (fabricante, composición, dosis registrada, fuente) en un desplegable junto al
  // selector. Los datos del potrero y del cultivo ya están a la vista en las tarjetas de selección.
  function renderNotes() {
    const item = selection().item;
    if (!ui.notes || !ui.productInfo) return;
    ui.notes.innerHTML = item ? productCard(item, selectedCrop()) : '';
    ui.productInfo.hidden = !item;
  }

  // ---------- resultado ----------
  // Qué falta para poder calcular (misma regla que validate(), pero por paso, para la interfaz guiada).
  function readiness() {
    const ctx = baseContext();
    const seeds = mode === 'semillas';
    const area = positive(ui.area.value);
    const dose = positive(ui.dose.value);
    const phyto = isPhyto(ctx.sel.item);
    const items = [
      {
        key: 'plot',
        label: 'Potrero',
        ok: Boolean(ctx.plot),
        hint: plots.length ? 'Selecciona un potrero' : 'Registra un potrero en Mis predios y potreros',
      },
      {
        key: 'crop',
        label: 'Cultivo',
        ok: Boolean(ctx.crop),
        hint: crops.length ? 'Selecciona un cultivo' : 'El catálogo no tiene cultivos activos',
      },
      {
        key: 'area',
        label: 'Superficie',
        ok: area !== null && (!ctx.plot || area <= ctx.plot.areaHa + 1e-9),
        hint:
          area === null
            ? 'Ingresa una superficie mayor que 0'
            : 'No puede superar ' + (ctx.plot ? R.superficie(ctx.plot.areaHa) : '') + ' ha',
      },
    ];
    if (!seeds) {
      items.push({
        key: 'product',
        label: 'Producto',
        ok: ctx.sel.kind !== 'none' && (!phyto || Boolean(ui.target.value)),
        hint:
          phyto && ctx.sel.kind === 'product'
            ? 'Elige el objetivo de control'
            : 'Elige un producto o el ingreso manual',
      });
    }
    // Una dosis sobre el máximo documentado tampoco deja calcular (ni descargar el PDF).
    const sobreMaximo = dose !== null && (limites(NaN, dose) || {}).tipo === 'dosis';
    items.push({
      key: 'dose',
      label: seeds ? 'Densidad por hectárea' : 'Dosis por hectárea',
      ok: dose !== null && !sobreMaximo,
      hint: sobreMaximo ? 'Supera la dosis máxima documentada' : 'Ingresa un valor mayor que 0',
    });
    items.push({ key: 'unit', label: 'Unidad', ok: Boolean(ui.unit.value), hint: 'Elige la unidad' });
    return { items, ready: items.every((item) => item.ok) };
  }

  function stateInfo() {
    const info = readiness();
    const shown = ui.result.__spec;
    return {
      mode,
      items: info.items,
      ready: info.ready,
      missing: info.items.filter((item) => !item.ok),
      saved: Boolean(shown && shown.saved),
    };
  }

  // La interfaz guiada (calc-flow.js) escucha este evento para actualizar pasos, estado y botones.
  function notify(extra) {
    root.dispatchEvent(new CustomEvent('calc:update', { detail: Object.assign(stateInfo(), extra || {}) }));
  }

  function checklistHtml(info) {
    return (
      '<ul class="calc-checklist" aria-label="Qué falta para calcular">' +
      info.items
        .map(
          (item) =>
            '<li class="' +
            (item.ok ? 'is-ok' : 'is-todo') +
            '"><span>' +
            esc(item.label) +
            '</span>' +
            (item.ok ? '' : '<small>' + esc(item.hint) + '</small>') +
            '</li>',
        )
        .join('') +
      '</ul>'
    );
  }

  function showEmpty(title, text, bare) {
    const info = readiness();
    ui.result.__spec = null;
    ui.result.innerHTML =
      '<div class="calc-result-top"><p class="result-kicker">Resultado</p><span class="calc-tag is-preview">Pendiente</span></div>' +
      '<p class="result-value is-empty" aria-hidden="true"><strong>—</strong></p>' +
      '<h2 class="calc-empty-title">' +
      esc(title) +
      '</h2><p class="muted calc-empty-text">' +
      text +
      '</p>' +
      (info.ready || bare ? '' : checklistHtml(info));
    notify({ saved: false });
  }

  // Origen de la dosis (misma clasificación que core/calculos.py). Se muestra solo como un dato en «Más detalles».
  function originLabel(data, seeds) {
    const what = seeds ? 'Densidad' : 'Dosis';
    if (data.origin === 'documented' || (data.origin === undefined && data.verified)) {
      return what + ' documentada · ' + (data.source || 'catálogo');
    }
    if (data.origin === 'catalog') return what + ' del catálogo (sin fuente completa)';
    if (data.origin === 'adjusted' || data.adjusted) {
      return what + ' ajustada por ti (catálogo: ' + R.dosis(data.catalog_dose) + ' ' + data.catalog_unit + ')';
    }
    return what + ' ingresada por ti';
  }

  // Dibuja el resumen: válido (R.html) o fuera de límite (R.htmlExceso). Prepara «Planificar actividad».
  function paint(spec) {
    ui.result.innerHTML = spec.problema ? R.htmlExceso(spec) : R.html(spec);
    ui.result.__spec = spec.problema ? null : spec;
    if (ui.plan) {
      const query = new URLSearchParams();
      Object.entries(spec.plan || {}).forEach(([key, value]) => {
        if (value !== '' && value !== null && value !== undefined) query.set(key, value);
      });
      ui.plan.hidden = !(spec.saved && spec.plan);
      ui.plan.href = ui.plan.href.split('?')[0] + (spec.plan ? '?' + query.toString() : '');
    }
    if (ui.pdf) ui.pdf.disabled = Boolean(spec.problema);
    notify({ painted: true });
  }

  // Valor fuera de límite: marca el campo y muestra el resumen en rojo, sin cantidad.
  function paintExceso(problema, area, dose) {
    const ctx = baseContext();
    ui.error.textContent = problema.titulo + '. ' + problema.detalle;
    fieldError(problema.campo === 'area' ? ui.area : ui.dose, problema.titulo + '.');
    paint({
      problema,
      kicker: mode === 'semillas' ? 'Semilla necesaria' : 'Insumo necesario',
      ...plotNames(),
      cropName: ctx.crop ? ctx.crop.name : '',
      area,
      dose,
      doseUnit: ctx.unit,
    });
  }

  function limites(area, dose) {
    const ctx = baseContext();
    return L.revisar({
      area,
      plotArea: ctx.plot ? ctx.plot.areaHa : NaN,
      dose,
      unit: ctx.unit,
      product: ctx.sel.item,
    });
  }

  function baseContext() {
    return {
      plot: selectedPlot(),
      crop: selectedCrop(),
      sel: selection(),
      unit: ui.unit.value || 'kg/ha',
    };
  }


  function previewSpec(area, dose) {
    const ctx = baseContext();
    const amount = area * dose;
    const unit = resultUnit(ctx.unit);
    return {
      saved: false,
      kicker: mode === 'semillas' ? 'Semilla necesaria' : 'Insumo necesario',
      amount,
      unit,
      ...plotNames(),
      cropName: ctx.crop ? ctx.crop.name : '',
      area,
      dose,
      doseUnit: ctx.unit,
      facts: [['Producto', ctx.sel.item ? ctx.sel.item.name : '']],
      availability: {
        plot: ctx.plot ? ctx.plot.id : '',
        crop: ctx.crop ? ctx.crop.id : '',
        product: ctx.sel.item ? ctx.sel.item.id : '',
        area,
        dose,
        unit: ctx.unit,
      },
    };
  }

  // La unidad elegida se muestra junto al campo de dosis para que no haya dudas de qué se está ingresando.
  function syncUnitTag() {
    const tag = document.getElementById('calc-dose-unit');
    if (tag) tag.textContent = ui.unit.value || '';
  }

  function renderPreview() {
    syncUnitTag();
    ui.error.textContent = '';
    fieldError(ui.area, '');
    fieldError(ui.dose, '');
    renderDoseOrigin();
    if (!plots.length) {
      showEmpty(
        'Antes de calcular necesitas un potrero',
        'Registra tu predio y define sus potreros en <a class="text-link" href="/predios/">Mis predios y potreros</a>; con eso podrás calcular cantidades.',
        true,
      );
      return false;
    }
    if (!crops.length) {
      showEmpty(
        'Sin cultivos en el catálogo',
        'Sin cultivos activos no se puede calcular. ' +
          (isStaff ? 'Regístralos desde la administración.' : 'Solicítalo al administrador.'),
        true,
      );
      return false;
    }
    if (ui.area.value === '' || ui.dose.value === '') {
      showEmpty('Aún falta un dato', 'En cuanto lo completes, aquí verás cuánto necesitas.');
      return false;
    }
    const area = positive(ui.area.value);
    const dose = positive(ui.dose.value);
    const plot = selectedPlot();
    if (area === null || dose === null) {
      ui.error.textContent = 'Ingresa superficie y dosis mayores a 0. No se aceptan vacíos ni valores inválidos.';
      if (area === null) fieldError(ui.area, 'La superficie debe ser mayor que 0.');
      if (dose === null)
        fieldError(
          ui.dose,
          mode === 'semillas' ? 'La densidad debe ser mayor que 0.' : 'La dosis debe ser mayor que 0.',
        );
      showEmpty('Revisa los datos', 'Corrige el valor indicado para ver cuánto necesitas.');
      return false;
    }
    const problema = plot ? limites(area, dose) : null;
    if (problema) {
      paintExceso(problema, area, dose);
      return false;
    }
    paint(previewSpec(area, dose));
    return true;
  }

  // ---------- potrero, cultivo y producto ----------
  function syncCropHelp() {
    const plot = selectedPlot();
    const crop = selectedCrop();
    if (!crops.length) {
      ui.cropHelp.textContent =
        'El catálogo no tiene cultivos activos.' +
        (isStaff ? ' Regístralos desde la administración.' : ' Solicítalo al administrador.');
    } else if (plot && crop && plot.cropId && String(plot.cropId) !== String(crop.id)) {
      ui.cropHelp.textContent = 'Otro cultivo: el registro del potrero (' + plot.crop + ') no se modifica.';
    } else if (plot && crop && !plot.cropId) {
      ui.cropHelp.textContent = 'El potrero no tiene cultivo asignado; el cálculo no lo asigna.';
    } else {
      ui.cropHelp.textContent = '';
    }
  }

  function syncPlotHelp() {
    const plot = selectedPlot();
    ui.plotHelp.textContent = plot
      ? 'Superficie disponible: ' +
        fmt(plot.areaHa, 4) +
        ' ha · Cultivo actual: ' +
        (plot.crop || 'sin cultivo') +
        ' · Dosis del catálogo (base de datos)'
      : '';
  }

  // Valor que el catálogo propone para la selección actual (null si no hay): sirve para distinguir un dato
  // «obtenido del catálogo» de uno «ajustado manualmente» o ingresado desde cero (simulación).
  let catalogDose = null;
  function setDose(value, unit, fromCatalog) {
    ui.dose.value = value === null || value === undefined ? '' : value;
    if (unit && hasOption(ui.unit, unit)) ui.unit.value = unit;
    catalogDose =
      fromCatalog && value !== null && value !== undefined
        ? { value: Number(value), unit: unit || '', verified: fromCatalog === 'verified' }
        : null;
  }
  const bareUnit = (unit) =>
    String(unit || '')
      .replace('/ha', '')
      .toLowerCase();

  function renderDoseOrigin() {
    const node = ui.doseOrigin;
    if (!node) return;
    node.className = 'calc-dose-origin';
    node.textContent = '';
    const value = positive(ui.dose.value);
    const documented =
      catalogDose &&
      catalogDose.verified &&
      value !== null &&
      Math.abs(value - catalogDose.value) < 1e-9 &&
      bareUnit(ui.unit.value) === bareUnit(catalogDose.unit);
    // «¿De dónde sale esta dosis?» solo tiene sentido cuando el dato no está documentado.
    if (ui.doseSourceField) ui.doseSourceField.hidden = value === null || documented || isPhyto(selection().item);
    if (value === null) return;
    if (catalogDose) {
      const unchanged =
        Math.abs(value - catalogDose.value) < 1e-9 && bareUnit(ui.unit.value) === bareUnit(catalogDose.unit);
      if (unchanged && catalogDose.verified) {
        node.classList.add('is-catalog');
        node.textContent = 'Dato documentado del catálogo' + (catalogDose.source ? ' · ' + catalogDose.source : '');
      } else if (unchanged) {
        node.classList.add('is-adjusted');
        node.textContent = 'Dato del catálogo (sin respaldo completo)';
      } else {
        node.classList.add('is-adjusted');
        node.textContent =
          'Ajustado manualmente · el catálogo indica ' + fmt(catalogDose.value, 3) + ' ' + catalogDose.unit;
      }
    }
    // Dosis escrita a mano sin valor de catálogo: no se agrega ningún aviso.
  }

  // Error pegado al campo que lo causa (además del mensaje general del formulario).
  function fieldError(input, message) {
    const host = input.closest('.field') || input.parentElement;
    let note = host.querySelector(':scope > .field-error');
    if (!message) {
      if (note) note.remove();
      input.removeAttribute('aria-invalid');
      return;
    }
    if (!note) {
      note = document.createElement('small');
      note.className = 'field-error';
      note.setAttribute('role', 'alert');
      host.append(note);
    }
    note.textContent = message;
    input.setAttribute('aria-invalid', 'true');
  }

  function fillUnits() {
    setOptions(
      ui.unit,
      (mode === 'semillas' ? SEED_UNITS : INPUT_UNITS).map((pair) => ({ value: pair[0], label: pair[1] })),
      null,
    );
  }

  // Modo y cultivo con los que se armó la lista de productos: si cambian, la elección anterior
  // ya no vale (por ejemplo «ingreso manual» que solo existía porque aún no había cultivo).
  let productScope = null;

  function refreshProducts() {
    const crop = selectedCrop();
    const requested = requestedProduct;
    const scope = mode + '|' + (crop ? crop.id : '');
    const keep = scope === productScope;
    productScope = scope;
    if (mode === 'semillas') {
      const seeds = crop ? products.filter((item) => item.category === 'seed' && belongsToCrop(item, crop)) : [];
      const options = [];
      if (crop && crop.density !== null && crop.density !== undefined) {
        options.push({
          value: 'crop',
          label: 'Densidad general del cultivo (catálogo)' + (crop.densitySource ? '' : ' · sin fuente'),
        });
      }
      seeds.forEach((item) =>
        options.push({
          value: 'p:' + item.id,
          label:
            item.name +
            (item.seedVariety ? ' · ' + item.seedVariety : '') +
            (item.doseVerified ? ' · ' + fmt(item.dose, 3) + ' ' + item.unit : ' · sin densidad verificada'),
        }),
      );
      options.push({ value: 'manual', label: 'Ingreso manual' });
      setOptions(ui.product, options, null, keep);
      if (requested && hasOption(ui.product, 'p:' + requested)) ui.product.value = 'p:' + requested;
      ui.productField.hidden = !seeds.length;
      // Las semillas no se mezclan con la lista SAG de fertilizantes y bioestimulantes.
      if (ui.sagSearch) ui.sagSearch.hidden = true;
      ui.productHelp.textContent = crop
        ? seeds.length
          ? seeds.length + ' semilla(s) registrada(s) para ' + crop.name + '.'
          : ''
        : '';
    } else {
      const keys = ['fertilizer', 'biostimulant', 'herbicide', 'fungicide', 'insecticide', 'other'];
      const groups = [];
      keys.forEach((key) => {
        const items = crop
          ? products.filter((item) => item.category === key && item.category !== 'seed' && belongsToCrop(item, crop))
          : [];
        if (!items.length) return;
        groups.push({
          group: categoryLabels[key] || key,
          items: items.map((item) => {
            if (PHYTO.has(key) && !verifiedPhytoFor(item, crop)) {
              return {
                value: String(item.id),
                label: item.name + ' · sin uso verificado (no calculable)',
                disabled: true,
              };
            }
            return {
              value: String(item.id),
              label:
                item.name +
                (item.doseVerified
                  ? ' · ' + fmt(item.dose, 3) + ' ' + item.unit
                  : item.sag
                    ? ' · ' + sagEstado(item).short + ' · sin dosis documentada'
                    : ' · dosis no verificada'),
            };
          }),
        });
      });
      groups.push({ group: 'Otro', items: [{ value: 'manual', label: 'Otro producto (ingreso manual)' }] });
      setOptions(ui.product, groups, crop ? 'Selecciona un producto' : 'Selecciona primero un cultivo', keep);
      if (requested && hasOption(ui.product, requested)) ui.product.value = requested;
      ui.productField.hidden = false;
      if (ui.sagSearch) ui.sagSearch.hidden = !crop;
      const count = crop ? products.filter((item) => item.category !== 'seed' && belongsToCrop(item, crop)).length : 0;
      ui.productHelp.textContent = !crop
        ? ''
        : count
          ? 'Dosis y unidad salen de la ficha del catálogo.'
          : 'No hay productos registrados para ' + crop.name + ' en el catálogo.';
    }
    syncProduct();
  }

  function syncProduct() {
    const crop = selectedCrop();
    const sel = selection();
    const item = sel.item;
    const phyto = isPhyto(item);
    ui.error.textContent = '';
    ui.targetField.hidden = !phyto;
    ui.dose.readOnly = phyto;
    ui.unit.disabled = phyto;
    if (phyto) {
      setOptions(
        ui.target,
        (item.controlTargets || []).map((target) => ({ value: target, label: target })),
        'Selecciona el objetivo de control',
      );
    }
    if (mode === 'semillas') {
      if (sel.kind === 'product' && item && item.doseVerified && item.dose) {
        setDose(item.dose, item.unit || 'kg/ha', 'verified');
        ui.doseHelp.textContent = 'Densidad documentada por: ' + (item.doseSource || item.sourceName) + '.';
      } else if (sel.kind === 'crop' && crop) {
        setDose(
          crop.density,
          (crop.unit || 'kg') + '/ha',
          crop.densitySource && crop.densityUrl ? 'verified' : 'unverified',
        );
        ui.doseHelp.textContent =
          crop.densitySource && crop.densityUrl
            ? 'Densidad general documentada: ' +
              crop.densitySource +
              (crop.densityVerifiedAt ? ' (verificada ' + fmtDate(crop.densityVerifiedAt) + ')' : '') +
              '.'
            : 'La densidad general del catálogo no tiene fuente documentada.';
      } else {
        // Sin densidad documentada no se propone ningún valor: la persona escribe el suyo.
        setDose(null);
        ui.doseHelp.textContent = item
          ? 'Esta semilla no tiene densidad documentada en el catálogo. Escribe la que vas a usar.'
          : 'El catálogo no tiene una densidad documentada. Escribe la que vas a usar.';
      }
    } else if (item && item.doseVerified && item.dose) {
      setDose(item.dose, item.unit, 'verified');
      ui.doseHelp.textContent =
        (phyto ? 'Dosis de etiqueta verificada (no editable): ' : 'Dosis documentada: ') +
        (item.doseSource || item.sourceName) +
        '.';
    } else {
      setDose(null);
      ui.doseHelp.textContent =
        item && item.sag
          ? (sagEstado(item).key === 'historico' ? sagEstado(item).long + '. ' : '') +
            'La lista SAG no informa dosis. Escribe la dosis de la etiqueta o de tu asesor: el resultado queda como simulación (dato manual).'
          : item
            ? 'No hay dosis verificada. Falta: ' +
              missingData(item, crop).join(', ') +
              '. Puedes ingresar la dosis de tu fuente técnica.'
            : sel.kind === 'manual'
              ? 'Ingresa la dosis de tu fuente técnica.'
              : 'Cantidad de producto por cada hectárea.';
    }
    renderNotes();
    renderPreview();
  }

  function syncPlot() {
    const plot = selectedPlot();
    if (plot) {
      ui.area.value = Number(plot.areaHa.toFixed(4));
      // Un potrero sin cultivo no hereda el del potrero anterior: se elige de nuevo (simulación) o se asigna en el potrero.
      if (plot.cropId && cropById(plot.cropId)) ui.crop.value = String(plot.cropId);
      else if (!plot.cropId) ui.crop.value = '';
    }
    syncPlotHelp();
    syncCropHelp();
    refreshProducts();
  }

  function onCropChange() {
    syncCropHelp();
    refreshProducts();
  }

  // Selector único de herramientas (Semillas · Insumos · ¿Me alcanza?): una pestaña está activa cuando
  // coincide la herramienta visible y, en «Cuánto llevar», también el modo de cálculo.
  function syncToolTabs() {
    const center = document.querySelector('.decision-center');
    const tool = (center && center.dataset.tool) || 'llevar';
    document.querySelectorAll('.tool-tabs [data-tool]').forEach((tab) => {
      const active = tab.dataset.tool === tool && (!tab.dataset.mode || tab.dataset.mode === mode);
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
    });
  }
  window.AgroCalcTabs = { sync: syncToolTabs };

  function setMode(next) {
    mode = next === 'insumos' ? 'insumos' : 'semillas';
    const seed = mode === 'semillas';
    syncToolTabs();
    ui.title.textContent = seed ? 'Calcular semillas' : 'Calcular insumos';
    ui.hint.textContent = seed
      ? 'Elige el potrero, revisa la superficie y escribe la dosis por hectárea.'
      : 'Elige el producto del catálogo. La dosis de la ficha se copia sola; en productos fitosanitarios la dosis de etiqueta no se puede modificar.';
    ui.doseLabel.textContent = seed ? 'Dosis o densidad por hectárea' : 'Dosis recomendada por hectárea';
    const doseTitle = $('calc-dose-title');
    const doseIntro = $('calc-dose-intro');
    if (doseTitle)
      doseTitle.textContent = seed ? 'Revisa la dosis por hectárea' : 'Elige el producto y revisa su dosis';
    if (doseIntro)
      doseIntro.textContent = seed
        ? 'Si el catálogo tiene una densidad documentada, se completa sola.'
        : 'La dosis sale de la ficha del producto.';
    ui.productLabel.textContent = seed ? 'Semilla o variedad del catálogo' : 'Insumo del catálogo';
    fillUnits();
    const query = new URLSearchParams(window.location.search);
    query.set('tipo', mode);
    window.history.replaceState(null, '', window.location.pathname + '?' + query.toString());
    refreshProducts();
  }

  // ---------- cálculo en el servidor ----------
  function packageText(data) {
    return data.package
      ? 'Equivale a ' +
          data.package.count +
          ' envase(s) de ' +
          fmt(data.package.size) +
          ' ' +
          data.package.unit +
          ' (presentación registrada).'
      : '';
  }

  function validate() {
    const ctx = baseContext();
    const area = positive(ui.area.value);
    if (!ctx.plot) return 'Selecciona un potrero registrado.';
    if (area === null) {
      fieldError(ui.area, 'La superficie debe ser mayor que 0.');
      return 'La superficie debe ser mayor que 0.';
    }
    if (!ctx.crop) return 'Selecciona un cultivo del catálogo.';
    if (mode === 'insumos' && ctx.sel.kind === 'none')
      return 'Selecciona un producto o la opción de ingreso manual.';
    if (isPhyto(ctx.sel.item) && !ui.target.value) return 'Selecciona el objetivo de control autorizado.';
    const dose = positive(ui.dose.value);
    if (dose === null) {
      const message = mode === 'semillas' ? 'Ingresa una densidad mayor que 0.' : 'Ingresa una dosis mayor que 0.';
      fieldError(ui.dose, message);
      return message;
    }
    const problema = limites(area, dose);
    if (problema) {
      paintExceso(problema, area, dose);
      return problema.titulo + '. ' + problema.detalle;
    }
    return '';
  }

  async function runCalculation() {
    ui.error.textContent = '';
    const problem = validate();
    if (problem) {
      ui.error.textContent = problem;
      return null;
    }
    const ctx = baseContext();
    const item = ctx.sel.item;
    const phyto = isPhyto(item);
    const seeds = mode === 'semillas';
    ui.submit.disabled = true;
    ui.pdf.disabled = true;
    try {
      const data = await calculate({
        operation: 'dose',
        calculation_type: seeds ? 'semillas' : phyto ? 'proteccion' : 'insumos',
        plot_id: ctx.plot.id,
        area: ui.area.value,
        dose: ui.dose.value,
        unit: phyto ? item.unit : ui.unit.value,
        crop: ctx.crop.name,
        product_id: item ? item.id : null,
        target: phyto ? ui.target.value : '',
        dose_source: ui.doseSource && !ui.doseSourceField.hidden ? ui.doseSource.value.trim() : '',
      });
      const notes = [];
      if (ctx.plot.cropId && String(ctx.plot.cropId) !== String(ctx.crop.id)) {
        notes.push('Calculado para un cultivo distinto al registrado en el potrero; el potrero no se modificó.');
      }
      if (phyto) {
        notes.push('Respeta la etiqueta autorizada: carencia, reingreso, equipo de protección y condiciones de aplicación.');
      }
      const productName = item ? item.name : ctx.sel.kind === 'manual' ? 'Producto manual' : '';
      const spec = {
        saved: true,
        calculationId: data.calculation_id,
        kicker: seeds ? 'Semilla necesaria' : 'Insumo necesario',
        amount: data.amount,
        unit: data.unit,
        ...plotNames(),
        cropName: ctx.crop.name,
        area: data.area,
        dose: data.dose,
        doseUnit: data.dose_unit,
        sourceUrl: data.verified ? data.source_url : '',
        facts: [
          ['Tipo de cálculo', 'Cantidad por superficie (superficie × dosis)'],
          ['Origen de la dosis', originLabel(data, seeds)],
          ['Producto', productName],
          ['Lista SAG orgánica', window.AgroCatalogoSAG ? window.AgroCatalogoSAG.resultado(data.sag_status) : ''],
          ['Equivalencia', packageText(data)],
          ['Objetivo de control', phyto ? ui.target.value : ''],
          ['Fecha de verificación', fmtDate(data.source_date)],
          ['Fuente indicada por ti', data.declared_source || ''],
        ],
        notes,
        availability: {
          plot: ctx.plot.id,
          crop: ctx.crop.id,
          product: item ? item.id : '',
          area: data.area,
          dose: data.dose,
          unit: data.dose_unit,
        },
        plan: {
          potrero: ctx.plot.id,
          tipo: seeds ? 'siembra' : phyto ? 'control_fitosanitario' : 'fertilizacion',
          insumo: item ? item.id : '',
          dosis: data.dose,
          unidad: data.dose_unit,
          superficie: data.area,
          objetivo: phyto ? ui.target.value : '',
        },
      };
      paint(spec);
      return spec;
    } catch (err) {
      // El servidor aplica los mismos límites: si rechaza el valor, el resumen tampoco lo muestra como válido.
      const enSuperficie = /superficie/i.test(err.message);
      const enDosis = /dosis|densidad/i.test(err.message);
      if (enSuperficie || enDosis) {
        paintExceso(
          { tipo: enSuperficie ? 'superficie' : 'dosis', campo: enSuperficie ? 'area' : 'dose',
            titulo: 'No es posible calcular', detalle: err.message },
          positive(ui.area.value),
          positive(ui.dose.value),
        );
      } else {
        ui.error.textContent = err.message;
      }
      return null;
    } finally {
      ui.submit.disabled = false;
      ui.pdf.disabled = false;
    }
  }

  // El PDF lo genera el servidor (core/pdf) a partir del cálculo guardado: /calculos/<id>/pdf/.
  function pdfFor(spec) {
    if (!spec.calculationId || !pdfUrlTemplate) {
      ui.error.textContent = 'Calcula y guarda el resultado antes de descargar el PDF.';
      return;
    }
    window.location.assign(pdfUrlTemplate.replace('/0/', '/' + spec.calculationId + '/'));
  }

  // El PDF es del resultado que se ve. Si solo hay una vista previa, primero se valida y guarda.
  async function downloadPdf() {
    const shown = ui.result.__spec;
    const spec = shown && shown.saved ? shown : await runCalculation();
    if (spec) pdfFor(spec);
  }

  // ---------- cálculos avanzados ----------
  function exampleList() {
    const out = [];
    const seenCrops = new Set();
    plots.forEach((plot) => {
      const crop = plot.cropId ? cropById(plot.cropId) : null;
      if (!crop || seenCrops.has(String(crop.id))) return;
      if (crop.density !== null && crop.density !== undefined && out.length < 2) {
        seenCrops.add(String(crop.id));
        const unit = (crop.unit || 'kg') + '/ha';
        out.push({
          mode: 'semillas',
          plot: plot.id,
          crop: crop.id,
          area: plot.areaHa,
          dose: crop.density,
          unit,
          label: crop.name + ' ' + fmt(plot.areaHa, 4) + ' ha × ' + fmt(crop.density) + ' ' + unit,
        });
      }
    });
    // Si los potreros aún no tienen cultivo asignado, se ofrecen ejemplos con los cultivos del
    // catálogo (por orden de registro) y la superficie del primer potrero. Solo al hacer clic se
    // rellena el formulario como vista previa; no se asigna ni se guarda nada.
    if (out.length < 2 && plots.length) {
      const firstPlot = plots[0];
      crops
        .slice()
        .sort((a, b) => Number(a.id) - Number(b.id))
        .forEach((crop) => {
          if (out.length >= 2 || seenCrops.has(String(crop.id))) return;
          if (crop.density === null || crop.density === undefined) return;
          seenCrops.add(String(crop.id));
          const unit = (crop.unit || 'kg') + '/ha';
          out.push({
            mode: 'semillas',
            plot: firstPlot.id,
            crop: crop.id,
            area: firstPlot.areaHa,
            dose: crop.density,
            unit,
            label: crop.name + ' ' + fmt(firstPlot.areaHa, 4) + ' ha × ' + fmt(crop.density) + ' ' + unit,
          });
        });
    }
    for (const plot of plots) {
      const crop = plot.cropId ? cropById(plot.cropId) : null;
      if (!crop) continue;
      const item = products.find(
        (product) =>
          product.category === 'fertilizer' && belongsToCrop(product, crop) && product.doseVerified && product.dose,
      );
      if (item) {
        out.push({
          mode: 'insumos',
          plot: plot.id,
          crop: crop.id,
          product: item.id,
          area: plot.areaHa,
          dose: item.dose,
          unit: item.unit,
          label: item.name + ' ' + fmt(plot.areaHa, 4) + ' ha × ' + fmt(item.dose) + ' ' + item.unit,
        });
        break;
      }
    }
    return out.slice(0, 3);
  }

  const examples = exampleList();
  function renderExamples() {
    if (!examples.length) {
      ui.examples.hidden = true;
      return;
    }
    ui.examples.hidden = false;
    ui.exampleRow.innerHTML = examples
      .map(
        (example, index) =>
          '<button class="example-chip" type="button" data-example="' + index + '">' + esc(example.label) + '</button>',
      )
      .join('');
  }

  function applyExample(example) {
    setMode(example.mode);
    ui.plot.value = String(example.plot);
    syncPlot();
    ui.crop.value = String(example.crop);
    onCropChange();
    if (example.product && hasOption(ui.product, example.product)) {
      ui.product.value = String(example.product);
      syncProduct();
    }
    ui.area.value = Number(Number(example.area).toFixed(4));
    ui.dose.value = example.dose;
    if (hasOption(ui.unit, example.unit)) ui.unit.value = example.unit;
    renderPreview();
  }

  // ---------- entrada desde otras herramientas ----------
  function prefill(info) {
    const result = { product: true };
    if (info.mode) setMode(info.mode);
    if (info.plot && plotById(info.plot)) {
      ui.plot.value = String(info.plot);
      syncPlot();
    }
    if (info.crop && cropById(info.crop)) {
      ui.crop.value = String(info.crop);
      onCropChange();
    }
    if (info.product) {
      const wanted = mode === 'semillas' ? 'p:' + info.product : String(info.product);
      if (hasOption(ui.product, wanted)) {
        ui.product.value = wanted;
        syncProduct();
      } else {
        // El producto no se puede calcular: no se deja seleccionado otro distinto.
        result.product = false;
        if (mode === 'insumos') {
          ui.product.value = '';
          syncProduct();
        }
      }
    }
    return result;
  }
  // Datos de la selección actual para abrir «Si alcanza» con el potrero, cultivo, superficie y dosis ya elegidos.
  function availabilityInfo() {
    const shown = ui.result.__spec;
    if (shown && shown.availability) return shown.availability;
    const ctx = baseContext();
    return {
      plot: ctx.plot ? ctx.plot.id : '',
      crop: ctx.crop ? ctx.crop.id : '',
      product: ctx.sel.item ? ctx.sel.item.id : '',
      area: positive(ui.area.value) || '',
      dose: positive(ui.dose.value) || '',
      unit: ctx.unit,
    };
  }
  window.AgroCalculator = {
    prefill,
    showError: (text) => {
      ui.error.textContent = text;
    },
    state: stateInfo,
    availabilityInfo,
    cropEmoji,
  };

  // ---------- eventos ----------
  ui.modes.forEach((button) => button.addEventListener('click', () => setMode(button.dataset.mode)));
  ui.plot.addEventListener('change', syncPlot);
  ui.crop.addEventListener('change', onCropChange);
  ui.product.addEventListener('change', syncProduct);
  ui.target.addEventListener('change', renderPreview);
  ['area', 'dose', 'unit'].forEach((key) => ui[key].addEventListener('input', renderPreview));
  ui.unit.addEventListener('change', renderPreview);
  ui.exampleRow.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-example]');
    if (chip) applyExample(examples[Number(chip.dataset.example)]);
  });
  ui.form.addEventListener('submit', (event) => {
    event.preventDefault();
    runCalculation();
  });
  ui.pdf.addEventListener('click', downloadPdf);

  // ---------- arranque ----------
  setOptions(
    ui.plot,
    plots.map((plot) => ({ value: String(plot.id), label: plot.farmName + ' · ' + plot.name })),
    plots.length ? null : 'No tienes potreros registrados',
  );
  setOptions(
    ui.crop,
    crops.map((crop) => ({ value: String(crop.id), label: crop.name })),
    crops.length ? 'Selecciona un cultivo' : 'Sin cultivos en el catálogo',
  );
  renderExamples();
  (function start() {
    const requested = productById(requestedProduct);
    const kind = params.get('tipo');
    const initial =
      kind === 'insumos' || kind === 'semillas'
        ? kind
        : requested && requested.category !== 'seed'
          ? 'insumos'
          : 'semillas';
    let wantedPlot = params.get('plot') && plotById(params.get('plot')) ? String(params.get('plot')) : '';
    if (!wantedPlot && params.get('crop')) {
      // Si llegas desde el calendario con un cultivo, se abre el primer potrero que lo tiene.
      const sameCrop = plots.find((item) => String(item.cropId) === String(params.get('crop')));
      if (sameCrop) wantedPlot = String(sameCrop.id);
    }
    if (wantedPlot) ui.plot.value = wantedPlot;
    const plot = selectedPlot();
    if (plot) {
      ui.area.value = Number(plot.areaHa.toFixed(4));
      if (plot.cropId && cropById(plot.cropId)) ui.crop.value = String(plot.cropId);
    }
    if (params.get('crop') && cropById(params.get('crop'))) ui.crop.value = String(params.get('crop'));
    syncPlotHelp();
    syncCropHelp();
    setMode(initial);
    requestedProduct = null;
    // ?insumo= de un producto de la lista SAG (no viene en la página): se trae del servidor y se elige.
    const wanted = params.get('insumo');
    if (wanted && !requested && window.AgroCatalogoSAG) {
      window.AgroCatalogoSAG.byId(wanted).then((item) => {
        if (!item) return;
        addSagProduct(item);
        if (mode !== 'insumos') return;
        refreshProducts();
        if (hasOption(ui.product, String(item.id))) {
          ui.product.value = String(item.id);
          syncProduct();
        }
      });
    }
  })();

  if (window.AgroCatalogoSAG) {
    window.AgroCatalogoSAG.attach(ui.sagSearch, (item) => {
      addSagProduct(item);
      refreshProducts();
      ui.product.value = String(item.id);
      ui.product.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }
})();
