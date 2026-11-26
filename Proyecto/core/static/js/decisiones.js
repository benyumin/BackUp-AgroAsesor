(function () {
  'use strict';
  const root = document.querySelector('.decision-center');
  if (!root) return;

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
  const calculateUrl = root.dataset.calculateUrl;
  const pdfUrlTemplate = root.dataset.pdfUrl;
  const isStaff = root.dataset.staff === '1';
  const crops = (catalog.cultivos || []).filter((crop) => crop.active !== false);
  const products = (catalog.insumos || []).filter((item) => item.usable !== false && item.active !== false);
  const allProducts = catalog.insumos || [];
  const categoryLabels = catalog.categoryLabels || {};
  const PHYTO = new Set(['herbicide', 'fungicide', 'insecticide']);
  const PHYTO_UNITS = ['kg/ha', 'g/ha', 'L/ha', 'mL/ha'];
  const params = new URLSearchParams(window.location.search);
  // «potrero» es el nombre público del parámetro; «plot» se mantiene por compatibilidad con enlaces anteriores.
  if (!params.has('plot') && params.has('potrero')) params.set('plot', params.get('potrero'));
  const lastResults = {};

  // ---------- utilidades ----------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function fmt(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString('es-CL', { maximumFractionDigits: digits === undefined ? 2 : digits });
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
    return (farm.plots || []).find((plot) => String(plot.id) === String(id)) || null;
  }
  function productById(id) {
    const found = allProducts.find((item) => String(item.id) === String(id));
    return found || (window.AgroCatalogoSAG && window.AgroCatalogoSAG.get(id)) || null;
  }
  // Producto de la lista SAG traído bajo demanda (catalogo-sag.js): se suma a las listas de esta página.
  function addSagProduct(item) {
    if (!item || allProducts.some((known) => String(known.id) === String(item.id))) return;
    allProducts.push(item);
    if (item.usable !== false && item.active !== false) products.push(item);
  }
  // Estado en la lista SAG (vigente, histórico o sin registro) con los textos comunes de catalogo-sag.js.
  function sagEstado(item) {
    return window.AgroCatalogoSAG ? window.AgroCatalogoSAG.estado(item) : { key: '', short: '', long: '' };
  }
  // Unidades que el servidor convierte entre sí (core/calculos.py): kg ↔ g y L ↔ mL.
  const UNIT_DIMENSION = { kg: 'masa', g: 'masa', l: 'volumen', ml: 'volumen', unidades: 'conteo' };
  function dimension(unit) {
    return UNIT_DIMENSION[String(unit || '').replace('/ha', '').toLowerCase()] || '';
  }
  function farmOf(plot) {
    return (farm.predios || []).find((item) => String(item.id) === String(plot.predio)) || null;
  }
  function isPhyto(item) {
    return PHYTO.has(item.category);
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
  function setOptions(select, items, placeholder) {
    const previous = select.value;
    select.replaceChildren();
    if (placeholder !== null) select.add(new Option(placeholder, ''));
    items.forEach((item) => {
      const option = new Option(item.label, item.value);
      if (item.disabled) option.disabled = true;
      select.add(option);
    });
    if ([...select.options].some((option) => option.value === previous && !option.disabled)) select.value = previous;
  }

  // ---------- pestañas ----------
  const tabs = [...document.querySelectorAll('.tool-tabs [data-tool]')];
  function showTool(name, focus) {
    if (!['llevar', 'alcanza'].includes(name)) name = 'llevar';
    root.dataset.tool = name;
    // Semillas e Insumos comparten panel: calc-panel.js decide cuál de las dos pestañas queda activa.
    if (window.AgroCalcTabs) window.AgroCalcTabs.sync();
    else
      tabs.forEach((tab) => {
        const active = tab.dataset.tool === name && (!tab.dataset.mode || tab.dataset.mode === 'semillas');
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', String(active));
      });
    document.querySelectorAll('.tool-panel').forEach((panel) => {
      panel.hidden = panel.id !== 'tool-' + name;
    });
    const next = new URLSearchParams(window.location.search);
    next.set('herramienta', name);
    window.history.replaceState(null, '', window.location.pathname + '?' + next.toString());
    if (focus) document.getElementById('tool-' + name).scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  tabs.forEach((tab) => tab.addEventListener('click', () => showTool(tab.dataset.tool)));

  document.querySelectorAll('[data-go-tool]').forEach((link) =>
    link.addEventListener('click', (event) => {
      event.preventDefault();
      showTool(link.dataset.goTool);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }),
  );

  // ---------- potrero y cultivo comunes ----------
  function selectedPlot(form) {
    return plotById(form.elements.plot.value);
  }
  function selectedCrop(form) {
    return cropById(form.elements.crop.value);
  }

  function setupPlotCrop(form, onChange) {
    const plotSelect = form.elements.plot;
    const cropSelect = form.elements.crop;
    setOptions(
      plotSelect,
      (farm.plots || []).map((plot) => ({
        value: String(plot.id),
        label: plot.farmName + ' · ' + plot.name + ' (' + fmt(plot.areaHa, 4) + ' ha)',
      })),
      (farm.plots || []).length ? 'Selecciona un potrero' : 'No tienes potreros registrados',
    );
    setOptions(
      cropSelect,
      crops.map((crop) => ({ value: String(crop.id), label: crop.name })),
      crops.length ? 'Selecciona un cultivo' : 'Sin cultivos en el catálogo',
    );
    const plotHelp = form.querySelector('[data-role=plot-help]');
    const cropHelp = form.querySelector('[data-role=crop-help]');
    function syncCropHelp() {
      const plot = selectedPlot(form);
      const crop = selectedCrop(form);
      if (!cropHelp) return;
      if (!crops.length) {
        cropHelp.textContent =
          'El catálogo no tiene cultivos activos.' +
          (isStaff ? ' Regístralos desde la administración.' : ' Solicítalo al administrador.');
      } else if (plot && crop && plot.cropId && String(plot.cropId) !== String(crop.id)) {
        cropHelp.textContent =
          'Simulación con otro cultivo: el registro del potrero (' + plot.crop + ') no se modifica.';
      } else if (plot && crop && !plot.cropId) {
        cropHelp.textContent = 'El potrero no tiene cultivo asignado; el cálculo no lo asigna.';
      } else {
        cropHelp.textContent = '';
      }
    }
    plotSelect.addEventListener('change', () => {
      const plot = selectedPlot(form);
      if (plot) {
        form.elements.area.value = Number(plot.areaHa.toFixed(4));
        form.elements.area.max = plot.areaHa;
        if (plot.cropId && cropById(plot.cropId)) cropSelect.value = String(plot.cropId);
        else if (!plot.cropId) cropSelect.value = ''; // no se arrastra el cultivo del potrero anterior
        if (plotHelp)
          plotHelp.textContent =
            'Superficie registrada: ' +
            fmt(plot.areaHa, 4) +
            ' ha · cultivo actual: ' +
            (plot.crop || 'sin asignar') +
            '.';
      } else if (plotHelp) {
        plotHelp.textContent = '';
      }
      syncCropHelp();
      onChange();
    });
    cropSelect.addEventListener('change', () => {
      syncCropHelp();
      onChange();
    });
    return syncCropHelp;
  }

  function validateBase(form) {
    const plot = selectedPlot(form);
    const area = Number(form.elements.area.value);
    if (!plot) return 'Selecciona un potrero registrado.';
    if (!(area > 0)) return 'La superficie debe ser mayor que 0.';
    if (area > plot.areaHa + 1e-9)
      return 'La superficie no puede superar las ' + fmt(plot.areaHa, 4) + ' ha registradas del potrero.';
    return '';
  }

  // ---------- resultados ----------
  function resultPanel(form) {
    return document.querySelector('[data-result-for="' + form.id + '"]');
  }

  function emptyResult(panel, title, text) {
    panel.replaceChildren(el('p', 'result-kicker', title), el('p', 'muted', text));
  }

  function renderResult(form, spec) {
    const panel = resultPanel(form);
    panel.replaceChildren();
    const top = el('div', 'calc-result-top');
    top.append(el('p', 'result-kicker', spec.kicker));
    if (spec.badge) top.append(el('span', 'badge ' + (spec.badge.ok ? '' : 'amber'), spec.badge.text));
    panel.append(top);
    const value = el('p', 'result-value');
    value.append(el('strong', '', spec.value), el('span', '', spec.unit));
    panel.append(value);
    if (spec.bar !== undefined) {
      if (spec.barLabel) {
        const caption = el('p', 'cover-bar-caption');
        caption.append(el('span', '', spec.barLabel[0]), el('strong', '', spec.barLabel[1]));
        panel.append(caption);
      }
      const track = el('div', 'cover-bar');
      track.setAttribute('role', 'progressbar');
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', '100');
      const pct = Math.max(0, Math.min(100, Number(spec.bar) || 0));
      track.setAttribute('aria-valuenow', String(Math.round(pct)));
      const fill = el('span', 'cover-bar-fill' + (pct >= 100 ? ' is-full' : ''));
      fill.style.width = pct + '%';
      track.append(fill);
      panel.append(track);
    }
    if (spec.inputs) {
      // Datos con los que se hizo la comprobación: superficie, cantidad disponible y dosis.
      const used = el('div', 'cover-inputs');
      spec.inputs.forEach(([label, text]) => {
        const cell = el('div', 'cover-input');
        cell.append(el('span', '', label), el('strong', '', text));
        used.append(cell);
      });
      panel.append(used);
    }
    if (spec.figures) {
      const grid = el('div', 'cover-figures');
      spec.figures.forEach(([label, text, tone]) => {
        const cell = el('div', 'cover-figure' + (tone ? ' is-' + tone : ''));
        cell.append(el('span', '', label), el('strong', '', text));
        grid.append(cell);
      });
      panel.append(grid);
    }
    if (spec.message)
      panel.append(el('p', 'cover-message' + (spec.messageTone ? ' is-' + spec.messageTone : ''), spec.message));
    const repeated = (spec.inputs || []).map((row) => row[0]); // ya se muestran como indicadores
    const rows = spec.rows.filter(
      (row) => row[1] !== '' && row[1] !== null && row[1] !== undefined && repeated.indexOf(row[0]) < 0,
    );
    const list = el('dl', 'calc-ctx');
    rows.forEach(([label, text]) => {
      const item = el('div', 'calc-ctx-item' + (label === 'Potrero' || label === 'Fuente técnica' ? ' is-wide' : ''));
      item.append(el('dt', '', label), el('dd', '', text));
      list.append(item);
    });
    panel.append(list);
    if (spec.secondary) {
      const box = el('div', 'formula-box');
      box.append(el('p', 'formula-line', spec.formulaTitle || 'Resumen'), el('p', '', spec.secondary));
      panel.append(box);
    }
    if (spec.sourceUrl) {
      const url = safeUrl(spec.sourceUrl);
      if (url) {
        const source = el('p', 'calc-source');
        const link = el('a', 'text-link', 'Ver fuente técnica');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        source.append(link);
        panel.append(source);
      }
    }
    (spec.warnings || []).forEach((warning) => panel.append(el('p', 'alert', warning)));
    panel.append(el('p', 'callout', 'Cálculo guardado en tu cuenta; también aparece en Reporte.'));
    const actions = el('div', 'calc-result-actions');
    const pdf = el('button', 'btn-primary-agro btn-sm calc-pdf-main', 'Descargar PDF');
    pdf.type = 'button';
    pdf.addEventListener('click', () => downloadPdf(spec));
    actions.append(pdf);
    const report = el('a', 'btn-secondary-agro btn-sm', 'Ver en Reportes');
    report.href = '/reportes/?tipo=calculos';
    actions.append(report);
    if (spec.plan) {
      const plan = el('a', 'btn-secondary-agro btn-sm', 'Planificar como actividad');
      const query = new URLSearchParams();
      Object.entries(spec.plan).forEach(([key, val]) => {
        if (val !== '' && val !== null && val !== undefined) query.set(key, val);
      });
      plan.href = '/actividades/nueva/?' + query.toString();
      actions.append(plan);
    }
    if (spec.availability) {
      const avail = el('button', 'btn-ghost-agro btn-sm', 'Ir a «¿Me alcanza?»');
      avail.type = 'button';
      avail.addEventListener('click', () => goToAvailability(spec.availability));
      actions.append(avail);
    }
    panel.append(actions);
    lastResults[form.id] = spec;
    // La capa guiada (tool-flow.js) marca el paso final y el estado como completados.
    form.dispatchEvent(new CustomEvent('flow:result', { detail: { simulation: spec.badge ? !spec.badge.ok : false } }));
  }

  // El PDF lo genera el servidor (core/pdf) con el cálculo guardado, con el mismo diseño que los demás informes.
  function downloadPdf(spec) {
    if (!spec.calculationId || !pdfUrlTemplate) {
      alert('Calcula y guarda el resultado antes de descargar el PDF.');
      return;
    }
    window.location.assign(pdfUrlTemplate.replace('/0/', '/' + spec.calculationId + '/'));
  }

  function goToAvailability(info) {
    const form = document.getElementById('cover-form');
    // Un producto de la lista SAG elegido en «Cuánto llevar» se suma antes de armar la lista del cultivo.
    if (info.product && window.AgroCatalogoSAG) addSagProduct(window.AgroCatalogoSAG.get(info.product));
    form.elements.plot.value = info.plot || '';
    form.elements.plot.dispatchEvent(new Event('change'));
    if (info.crop) {
      form.elements.crop.value = info.crop;
      form.elements.crop.dispatchEvent(new Event('change'));
    }
    if (info.product) {
      form.elements.product.value = info.product;
      form.elements.product.dispatchEvent(new Event('change'));
    }
    if (info.area) form.elements.area.value = info.area;
    if (info.dose && !form.elements.dose.readOnly) form.elements.dose.value = info.dose;
    if (info.unit) {
      form.elements.unit.value = info.unit;
      form.elements.unit.dispatchEvent(new Event('change'));
    }
    form.dispatchEvent(new CustomEvent('flow:refresh'));
    showTool('alcanza', true);
    form.elements.stock.focus({ preventScroll: true });
  }

  // «Cuánto llevar» (semillas, insumos y cálculos avanzados) vive en calc-panel.js.

  // ---------- SI ALCANZA ----------
  (function setupCoverage() {
    const form = document.getElementById('cover-form');
    const f = form.elements;
    const error = form.querySelector('[data-role=error]');
    const productHelp = form.querySelector('[data-role=product-help]');
    const doseHelp = form.querySelector('[data-role=dose-help]');
    const targetField = form.querySelector('[data-role=target-field]');
    const sagHost = form.querySelector('[data-role=sag-search]');
    // La dosis que puso la calculadora (catálogo) se borra si cambias a un producto sin dosis: no se arrastra.
    let autoDose = false;
    let stockUnitTouched = false;
    function refresh() {
      const crop = selectedCrop(form);
      const items = crop ? products.filter((item) => belongsToCrop(item, crop) || (!item.cropId && !item.crop)) : [];
      const options = [];
      // Semilla sin producto registrado: la densidad de siembra del cultivo, si el catálogo la tiene.
      if (crop && crop.density !== null && crop.density !== undefined) {
        options.push({
          value: 'crop-density',
          label: 'Semilla de ' + crop.name + ' · densidad del catálogo' + (crop.densitySource ? '' : ' (sin fuente)'),
        });
      }
      items.forEach((item) =>
        options.push(
          isPhyto(item) && !verifiedPhytoFor(item, crop)
            ? { value: item.id, label: item.categoryLabel + ' · ' + item.name + ' · sin uso verificado', disabled: true }
            : {
                value: item.id,
                label:
                  item.categoryLabel + ' · ' + item.name + (item.sag ? ' · ' + sagEstado(item).short + ' (sin dosis)' : ''),
              },
        ),
      );
      options.push({ value: 'manual', label: 'Producto no registrado (simulación)' });
      setOptions(f.product, options, crop ? 'Selecciona un producto' : 'Selecciona primero un cultivo');
      if (sagHost) sagHost.hidden = !crop;
      sync();
    }
    function setCoverDose(value, unit) {
      f.dose.value = value;
      if (unit && [...f.unit.options].some((o) => o.value === unit)) f.unit.value = unit;
      autoDose = true;
    }
    function sync() {
      const crop = selectedCrop(form);
      const item = productById(f.product.value);
      const phyto = item && isPhyto(item);
      const density = f.product.value === 'crop-density' && crop;
      targetField.hidden = !phyto;
      if (phyto)
        setOptions(
          f.target,
          (item.controlTargets || []).map((t) => ({ value: t, label: t })),
          'Selecciona el objetivo',
        );
      f.dose.readOnly = Boolean(phyto);
      f.unit.disabled = Boolean(phyto);
      if (item && item.doseVerified && item.dose) {
        setCoverDose(item.dose, item.unit);
        doseHelp.textContent = 'Dosis documentada: ' + (item.doseSource || item.sourceName) + '.';
      } else if (density) {
        setCoverDose(crop.density, (crop.unit || 'kg') + '/ha');
        doseHelp.textContent = crop.densitySource
          ? 'Densidad de siembra documentada: ' + crop.densitySource + '.'
          : 'Densidad del catálogo sin fuente documentada: el resultado queda como dato de catálogo.';
      } else {
        if (autoDose) f.dose.value = '';
        autoDose = false;
        if (item && item.sag) {
          doseHelp.textContent =
            (sagEstado(item).key === 'historico' ? sagEstado(item).long + '. ' : '') +
            'La lista SAG no informa dosis. Escribe la de la etiqueta o de tu asesor: el resultado queda como simulación (dato manual).';
        } else if (item) {
          doseHelp.textContent =
            'Sin dosis verificada (falta: ' +
            missingData(item, crop).join(', ') +
            '). Lo que ingreses será una simulación.';
        } else {
          doseHelp.textContent = f.product.value === 'manual' ? 'Escribe la dosis de tu fuente técnica (simulación).' : '';
        }
      }
      productHelp.textContent =
        item || density
          ? 'Registrado en catálogo; la cantidad disponible la indicas tú (no hay inventario registrado).'
          : '';
    }
    const syncHelp = setupPlotCrop(form, refresh);
    // Unidades visibles junto a cada campo: se actualizan con la unidad elegida.
    // La cantidad disponible puede ir en otra unidad compatible con la dosis (500 g con una dosis en kg/ha):
    // solo se ofrecen las compatibles y el servidor la convierte antes de comparar.
    function syncUnits() {
      const base = (f.unit.value || '').replace('/ha', '');
      if (f.stock_unit) {
        [...f.stock_unit.options].forEach((option) => {
          option.disabled = dimension(option.value) !== dimension(f.unit.value);
        });
        const current = f.stock_unit.selectedOptions[0];
        if (!stockUnitTouched || !current || current.disabled) f.stock_unit.value = base;
      }
      const stockTag = form.querySelector('[data-unit-tag="stock"]');
      const doseTag = form.querySelector('[data-unit-tag="dose"]');
      if (stockTag) stockTag.textContent = f.stock_unit ? f.stock_unit.value : base;
      if (doseTag) doseTag.textContent = f.unit.value || '';
    }
    f.unit.addEventListener('change', syncUnits);
    f.dose.addEventListener('input', () => {
      autoDose = false; // la persona escribió su dosis: ya no es la del catálogo
    });
    if (f.stock_unit) {
      f.stock_unit.addEventListener('change', () => {
        stockUnitTouched = true;
        syncUnits();
      });
    }
    f.product.addEventListener('change', () => {
      sync();
      syncUnits();
    });
    if (window.AgroCatalogoSAG) {
      window.AgroCatalogoSAG.attach(sagHost, (item) => {
        addSagProduct(item);
        refresh();
        f.product.value = String(item.id);
        f.product.dispatchEvent(new Event('change', { bubbles: true }));
      });
    }
    form.addEventListener('flow:refresh', syncUnits);
    syncUnits();
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      error.textContent = '';
      const plot = selectedPlot(form);
      const crop = selectedCrop(form);
      const item = productById(f.product.value);
      const density = f.product.value === 'crop-density';
      const base = validateBase(form);
      if (base) {
        error.textContent = base;
        return;
      }
      if (!crop) {
        error.textContent = 'Selecciona un cultivo.';
        return;
      }
      if (!item && !density && f.product.value !== 'manual') {
        error.textContent = 'Selecciona un producto.';
        return;
      }
      if (f.stock.value === '' || Number(f.stock.value) < 0) {
        error.textContent = 'La cantidad disponible debe ser 0 o mayor.';
        return;
      }
      if (!(Number(f.dose.value) > 0)) {
        error.textContent = 'Ingresa una dosis o requerimiento mayor que 0.';
        return;
      }
      if (f.stock_unit && dimension(f.stock_unit.value) !== dimension(f.unit.value)) {
        error.textContent = 'La unidad de lo que tienes no es compatible con la unidad de la dosis.';
        return;
      }
      const phyto = item && isPhyto(item);
      if (phyto && !f.target.value) {
        error.textContent = 'Selecciona el objetivo de control.';
        return;
      }
      const button = event.submitter;
      if (button) button.disabled = true;
      try {
        const unit = phyto ? item.unit : f.unit.value;
        const data = await calculate({
          operation: 'coverage',
          calculation_type: phyto ? 'proteccion' : 'coverage',
          plot_id: plot.id,
          area: f.area.value,
          dose: f.dose.value,
          unit,
          stock: f.stock.value,
          stock_unit: f.stock_unit ? f.stock_unit.value : '',
          dose_basis: density ? 'crop_density' : '',
          crop: crop.name,
          product_id: item ? item.id : null,
          target: phyto ? f.target.value : '',
        });
        const enough = data.missingQty <= 0;
        const entered =
          data.stockUnit && data.stockUnit !== data.unit
            ? ' (ingresaste ' + fmt(data.stockEntered, 3) + ' ' + data.stockUnit + ')'
            : '';
        const surplus = Math.max(0, data.stock - data.needed);
        const shortfall = Math.max(0, data.needed - data.stock);
        const unitText = data.unit;
        renderResult(form, {
          calculationId: data.calculation_id,
          kicker: enough ? 'Cantidad suficiente' : 'Cantidad insuficiente',
          formulaTitle: 'Cantidad necesaria = superficie × dosis',
          value: fmt(Math.min(100, Math.max(0, data.percent)), 0) + ' %',
          unit: 'de la superficie cubierta',
          bar: data.percent,
          barLabel: ['Cobertura', fmt(data.coveredHa, 4) + ' de ' + fmt(data.area, 4) + ' ha'],
          inputs: [
            ['Superficie del potrero', fmt(plot.areaHa, 4) + ' ha'],
            ['Superficie a cubrir', fmt(data.area, 4) + ' ha'],
            ['Dosis', fmt(data.dose, 3) + ' ' + data.dose_unit],
          ],
          figures: [
            ['Necesario', fmt(data.needed, 2) + ' ' + unitText],
            ['Disponible', fmt(data.stock, 2) + ' ' + unitText + entered],
            enough
              ? ['Sobrante', fmt(surplus, 2) + ' ' + unitText, 'ok']
              : ['Faltante', fmt(shortfall, 2) + ' ' + unitText, 'warn'],
          ],
          message: enough
            ? surplus > 1e-9
              ? 'Tu cantidad disponible supera lo necesario.'
              : 'Cantidad suficiente: te alcanza justo para toda la superficie.'
            : 'Con lo que tienes cubres ' +
              fmt(data.coveredHa, 4) +
              ' de ' +
              fmt(data.area, 4) +
              ' ha; te faltan ' +
              fmt(shortfall, 2) +
              ' ' +
              unitText +
              ' para cubrir ' +
              fmt(data.missingHa, 4) +
              ' ha.',
          messageTone: enough ? 'ok' : 'warn',
          badge: {
            ok: data.verified,
            text: data.verified
              ? 'Dosis documentada'
              : data.origin === 'catalog'
                ? 'Dato de catálogo'
                : data.adjusted
                  ? 'Ajustado manualmente'
                  : 'Simulación',
          },
          rows: [
            ['Potrero', plot.farmName + ' · ' + plot.name],
            ['Cultivo', crop.name],
            ['Producto', item ? item.name : density ? 'Semilla de ' + crop.name : 'Producto manual'],
            ['Lista SAG orgánica', window.AgroCatalogoSAG ? window.AgroCatalogoSAG.resultado(data.sag_status) : ''],
            ['Superficie que se puede cubrir', fmt(data.coveredHa, 4) + ' de ' + fmt(data.area, 4) + ' ha'],
            [
              'Hectáreas que alcanzarías con lo que tienes',
              data.coverableHa !== undefined ? fmt(data.coverableHa, 4) + ' ha' : '',
            ],
            ['Superficie sin cubrir', data.missingHa > 1e-9 ? fmt(data.missingHa, 4) + ' ha' : ''],
            ['Dosis', fmt(data.dose, 3) + ' ' + data.dose_unit],
            ['Fuente técnica', data.source || ''],
          ],
          sourceUrl: data.source_url,
          warnings: (data.above_max
            ? ['⚠ Dosis superior a la referencia registrada (' + fmt(data.max_dose, 3) + ' ' + data.max_unit + ').']
            : []
          )
            .concat(['Simulación de disponibilidad: no se descontó ninguna existencia ni se registró inventario.'])
            .concat(data.simulation ? ['La dosis usada no está documentada con fuente verificada.'] : []),
        });
      } catch (err) {
        error.textContent = err.message;
      } finally {
        if (button) button.disabled = false;
      }
    });
    emptyResult(
      resultPanel(form),
      'Resultado',
      'Indica cuánto producto tienes y comprueba qué superficie alcanzas a cubrir.',
    );
    syncHelp();
  })();

  // ---------- estado inicial desde la URL ----------
  function applyInitialSelection() {
    const plotId = params.get('plot');
    const cropId = params.get('crop');
    const form = document.getElementById('cover-form');
    if (plotId && plotById(plotId)) {
      form.elements.plot.value = plotId;
      form.elements.plot.dispatchEvent(new Event('change'));
    }
    if (cropId && cropById(cropId)) {
      form.elements.crop.value = cropId;
      form.elements.crop.dispatchEvent(new Event('change'));
    }
    // ?insumo= (desde la ficha del producto): se elige en «¿Me alcanza?». Si es de la lista SAG se trae del servidor.
    const productId = params.get('insumo');
    const choose = () => {
      if (![...form.elements.product.options].some((o) => o.value === String(productId) && !o.disabled)) return;
      form.elements.product.value = String(productId);
      form.elements.product.dispatchEvent(new Event('change', { bubbles: true }));
    };
    if (productId && productById(productId)) choose();
    else if (productId && window.AgroCatalogoSAG) {
      window.AgroCatalogoSAG.byId(productId).then((item) => {
        if (!item) return;
        addSagProduct(item);
        form.elements.crop.dispatchEvent(new Event('change')); // vuelve a armar la lista de productos
        choose();
      });
    }
    form.dispatchEvent(new CustomEvent('flow:refresh'));
    showTool(params.get('herramienta') || 'llevar');
  }
  window.AgroDecisiones = { showTool, goToAvailability };
  applyInitialSelection();
})();
