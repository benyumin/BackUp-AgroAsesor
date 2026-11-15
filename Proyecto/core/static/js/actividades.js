(function () {
  'use strict';
  const form = document.getElementById('activity-form');
  if (!form) return;

  function readJson(id, fallback) {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent) || fallback;
    } catch (error) {
      return fallback;
    }
  }
  const plots = readJson('activity-plots', []);
  const guidance = readJson('activity-guidance', {});
  const catalog = readJson('agro-catalog', {});
  const products = catalog.insumos || [];
  const WITH_INPUT = new Set(['siembra', 'fertilizacion', 'control_fitosanitario']);
  const PHYTO = new Set(['herbicide', 'fungicide', 'insecticide']);
  const CATEGORY_BY_TYPE = {
    siembra: (item) => item.category === 'seed',
    fertilizacion: (item) => item.category === 'fertilizer' || item.category === 'other',
    control_fitosanitario: (item) => PHYTO.has(item.category),
  };

  const f = form.elements;
  const inputSection = document.getElementById('input-section');
  const productFields = document.getElementById('product-fields');
  const inputState = document.getElementById('input-state');
  const inputCallout = document.getElementById('input-callout');
  const plotContext = document.getElementById('plot-context');
  const guidanceCard = document.getElementById('guidance');
  const doseSummary = document.getElementById('dose-summary');
  const productContext = document.getElementById('product-context');
  const seasonOptions = [...f.temporada.options].map((option) => ({ value: option.value, text: option.text }));
  const productOptions = [...f.insumo.options].map((option) => ({ value: option.value, text: option.text }));
  let suggestedName = f.nombre.value;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }
  function fmt(value, digits) {
    const number = Number(value);
    return Number.isFinite(number)
      ? number.toLocaleString('es-CL', { maximumFractionDigits: digits === undefined ? 2 : digits })
      : '—';
  }
  function fmtDate(value) {
    if (!value) return 'Sin fecha';
    const [y, m, d] = String(value).split('-');
    return d + '/' + m + '/' + y;
  }
  function localIso(date) {
    const d = date || new Date();
    return (
      d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
    );
  }
  function addDays(iso, days) {
    const date = new Date(iso + 'T12:00:00');
    date.setDate(date.getDate() + days);
    return localIso(date);
  }
  function selectedType() {
    const checked = form.querySelector('input[name=tipo]:checked');
    return checked ? checked.value : '';
  }
  function selectedPlot() {
    return plots.find((plot) => String(plot.id) === f.potrero.value) || null;
  }
  function selectedSeason(plot) {
    if (!plot) return null;
    return (plot.seasons || []).find((season) => String(season.id) === f.temporada.value) || null;
  }
  function selectedProduct() {
    return products.find((item) => String(item.id) === f.insumo.value) || null;
  }
  function cropName() {
    const plot = selectedPlot();
    const season = selectedSeason(plot);
    return season ? season.crop : plot ? plot.crop : '';
  }
  function carenciaDays(text) {
    const match = String(text || '').match(/\d+/);
    return match ? Number(match[0]) : null;
  }

  function renderGuidance() {
    const type = selectedType();
    const info = guidance[type];
    guidanceCard.replaceChildren(el('h2', '', 'Buenas prácticas'));
    if (!info) {
      guidanceCard.append(
        el('p', 'muted', 'Selecciona un tipo de labor para ver recomendaciones de manejo y registro.'),
      );
      return;
    }
    guidanceCard.append(el('p', 'guidance-title', info.title));
    const list = el('ul', 'check-list');
    info.tips.forEach((tip) => list.append(el('li', '', tip)));
    guidanceCard.append(list);
    guidanceCard.append(
      el(
        'p',
        'muted small',
        'Pautas generales de Buenas Prácticas Agrícolas. Ante dudas, consulta a tu asesor técnico.',
      ),
    );
  }

  function renderPlot() {
    const plot = selectedPlot();
    plotContext.replaceChildren(el('h2', '', 'Potrero seleccionado'));
    document.querySelectorAll('#plot-agenda li[data-plot]').forEach((item, index) => {
      item.hidden = plot ? item.dataset.plot !== String(plot.id) : index >= 6;
    });
    if (!plot) {
      plotContext.append(el('p', 'muted', 'Elige un potrero para ver su cultivo, temporada y superficie.'));
      return;
    }
    const season = selectedSeason(plot);
    const list = el('dl', 'facts');
    const add = (label, value) => list.append(el('dt', '', label), el('dd', '', value));
    add('Predio', plot.farm);
    add('Potrero', plot.name);
    add('Superficie', fmt(plot.areaHa, 4) + ' ha');
    add(
      'Cultivo',
      season ? season.crop + (season.variety ? ' · ' + season.variety : '') : plot.crop || 'Sin cultivo asignado',
    );
    if (season && season.season) add('Temporada', season.season + ' · ' + season.statusLabel);
    add('Siembra', fmtDate(season ? season.sowingDate : plot.sowing));
    add('Cosecha prevista', fmtDate(season ? season.harvestDate : plot.harvest));
    plotContext.append(list);
    if (!plot.crop && !season) {
      const link = el('a', 'text-link', 'Asignar cultivo al potrero');
      link.href = '/predios/?potrero=' + plot.id;
      plotContext.append(link);
    }
  }

  function filterSeasons(keepCurrent) {
    const plot = selectedPlot();
    const allowed = new Set((plot ? plot.seasons : []).map((season) => String(season.id)));
    const current = f.temporada.value;
    f.temporada.replaceChildren();
    seasonOptions.forEach((option) => {
      if (option.value === '' || allowed.has(option.value)) f.temporada.add(new Option(option.text, option.value));
    });
    if (keepCurrent && allowed.has(current)) f.temporada.value = current;
    else f.temporada.value = plot && plot.currentSeasonId ? String(plot.currentSeasonId) : '';
  }

  function filterProducts() {
    const type = selectedType();
    const crop = cropName();
    const matcher = CATEGORY_BY_TYPE[type];
    const current = f.insumo.value;
    f.insumo.replaceChildren();
    let count = 0;
    productOptions.forEach((option) => {
      if (option.value === '') {
        f.insumo.add(new Option(option.text, ''));
        return;
      }
      const item = products.find((product) => String(product.id) === option.value);
      if (!item || (matcher && !matcher(item))) return;
      if (item.crop && crop && item.crop !== crop) return;
      const label =
        option.text +
        (PHYTO.has(item.category) && !(item.verifiedUse && (item.authorizedCrops || []).includes(crop))
          ? ' · sin uso verificado para este cultivo'
          : '');
      f.insumo.add(new Option(label, option.value));
      count += 1;
    });
    if ([...f.insumo.options].some((option) => option.value === current)) f.insumo.value = current;
    f.insumo.closest('.field').querySelector('.help')?.remove();
    const help = el(
      'small',
      'help',
      count
        ? count + ' producto(s) del catálogo compatibles con la labor y el cultivo.'
        : products.length
          ? 'No hay productos del catálogo para esta labor y cultivo. Escribe el producto en «Otro producto».'
          : 'El catálogo de productos todavía está vacío. Escribe el producto en «Otro producto».',
    );
    f.insumo.after(help);
  }

  function renderProduct() {
    const item = selectedProduct();
    const crop = cropName();
    productContext.replaceChildren();
    if (!item) return;
    if (item.unit && !f.unidad_dosis.value) f.unidad_dosis.value = item.unit;
    if (item.doseVerified && item.dose && !f.dosis.value) f.dosis.value = item.dose;
    const box = el('div', 'product-box');
    box.append(el('strong', '', item.name));
    box.append(
      el(
        'span',
        'badge ' + (item.verified ? '' : 'amber'),
        item.verified ? 'Verificado con fuente' : 'Información no verificada',
      ),
    );
    const list = el('dl', 'facts');
    const add = (label, value) => {
      if (value) list.append(el('dt', '', label), el('dd', '', value));
    };
    add(
      'Dosis registrada',
      item.dose
        ? fmt(item.dose, 3) + ' ' + (item.unit || '') + (item.doseVerified ? ' (verificada)' : ' (sin verificar)')
        : 'No documentada',
    );
    add('Ingrediente / composición', item.activeIngredient || item.nutrientComposition);
    add('Modo de aplicación', item.applicationMode);
    add('Período de carencia', item.preharvestInterval);
    add('Tiempo de reingreso', item.reentryInterval);
    add('Autorización SAG', item.sagRegistration);
    box.append(list);
    if (PHYTO.has(item.category) && !(item.verifiedUse && (item.authorizedCrops || []).includes(crop))) {
      box.append(
        el(
          'p',
          'warn',
          'Este producto no tiene un uso verificado para ' +
            (crop || 'el cultivo') +
            ' en el catálogo. Revisa la etiqueta autorizada antes de aplicarlo.',
        ),
      );
    }
    const days = carenciaDays(item.preharvestInterval);
    const plot = selectedPlot();
    const season = selectedSeason(plot);
    const harvest = season ? season.harvestDate : plot ? plot.harvest : '';
    const base = f.fecha_realizada.value || f.fecha_programada.value;
    if (days !== null && base) {
      const end = addDays(base, days);
      const conflict = harvest && harvest < end;
      box.append(
        el(
          'p',
          conflict ? 'warn' : 'ok',
          'La carencia termina el ' +
            fmtDate(end) +
            '.' +
            (harvest
              ? conflict
                ? ' La cosecha registrada (' +
                  fmtDate(harvest) +
                  ') es ANTERIOR: ajusta la fecha de aplicación o de cosecha.'
                : ' La cosecha registrada (' + fmtDate(harvest) + ') es posterior.'
              : ''),
        ),
      );
    }
    if (item.labelUrl) {
      const link = el('a', 'text-link', 'Ver etiqueta');
      link.href = item.labelUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      box.append(link);
    }
    productContext.append(box);
  }

  function renderDose() {
    const dose = Number(f.dosis.value);
    const area = Number(f.superficie_ha.value);
    const unit = f.unidad_dosis.value;
    doseSummary.replaceChildren();
    if (dose > 0 && area > 0 && unit) {
      const total = dose * area;
      doseSummary.append(
        el('span', '', 'Cantidad total estimada'),
        el('strong', '', fmt(total, 2) + ' ' + unit.replace('/ha', '')),
        el('small', '', fmt(dose, 3) + ' ' + unit + ' × ' + fmt(area, 4) + ' ha'),
      );
    }
  }

  function syncType(fromUser) {
    const type = selectedType();
    const needsInput = WITH_INPUT.has(type);
    const state = !type ? 'pending' : needsInput ? 'active' : 'na';
    inputSection.dataset.state = state;
    // Los campos de producto solo se ven (y se envían) cuando la labor los usa; no se dejan grises.
    productFields.hidden = !needsInput;
    productFields.querySelectorAll('input, select').forEach((input) => {
      input.disabled = !needsInput;
    });
    const typeLabel = ((form.querySelector('input[name=tipo]:checked') || {}).nextElementSibling || {}).textContent;
    const texts = {
      pending: [
        'Primero elige el tipo de labor',
        'Elige el tipo de labor en el paso 1. Si es siembra, fertilización o control fitosanitario, aquí podrás indicar el producto y la dosis.',
      ],
      na: [
        'No necesitas completar esto',
        'La labor «' +
          ((typeLabel || '').trim().toLowerCase() || 'elegida') +
          '» no usa productos. Si quieres, indica la superficie trabajada y el objetivo; el resto se guarda solo.',
      ],
      active: [
        'Completa si aplicaste algo',
        'Elige un producto del catálogo o escribe el tuyo en «Otro producto». La cantidad total se calcula sola con la dosis y la superficie.',
      ],
    };
    inputState.textContent = texts[state][0];
    inputCallout.textContent = texts[state][1];
    const info = guidance[type];
    if (info && fromUser && (!f.nombre.value || f.nombre.value === suggestedName)) {
      const crop = cropName();
      suggestedName = info.title + (crop ? ' · ' + crop : '');
      f.nombre.value = suggestedName;
    }
    form
      .querySelectorAll('.type-option')
      .forEach((label) => label.classList.toggle('is-checked', label.querySelector('input').checked));
    renderGuidance();
    filterProducts();
    renderProduct();
  }

  function syncState() {
    const done = f.estado.value === 'completada';
    form.querySelectorAll('[data-done-only]').forEach((node) => node.classList.toggle('is-optional', !done));
    if (done && !f.fecha_realizada.value) f.fecha_realizada.value = localIso();
  }

  form.querySelectorAll('input[name=tipo]').forEach((radio) => radio.addEventListener('change', () => syncType(true)));
  f.potrero.addEventListener('change', () => {
    filterSeasons(false);
    const plot = selectedPlot();
    if (plot && !f.superficie_ha.value) f.superficie_ha.value = Number(plot.areaHa.toFixed(4));
    renderPlot();
    filterProducts();
    renderProduct();
  });
  f.temporada.addEventListener('change', () => {
    renderPlot();
    filterProducts();
    renderProduct();
  });
  f.insumo.addEventListener('change', () => {
    renderProduct();
    renderDose();
  });
  ['dosis', 'superficie_ha', 'unidad_dosis'].forEach((name) => f[name].addEventListener('input', renderDose));
  f.unidad_dosis.addEventListener('change', renderDose);
  ['fecha_programada', 'fecha_realizada'].forEach((name) => f[name].addEventListener('change', renderProduct));
  f.estado.addEventListener('change', syncState);
  f.superficie_ha.addEventListener('change', () => {
    const plot = selectedPlot();
    const help = f.superficie_ha.closest('.field').querySelector('.help') || el('small', 'help');
    help.textContent =
      plot && Number(f.superficie_ha.value) > plot.areaHa
        ? 'Supera las ' + fmt(plot.areaHa, 4) + ' ha del potrero.'
        : '';
    f.superficie_ha.after(help);
  });

  filterSeasons(true);
  const plot = selectedPlot();
  if (plot && !f.superficie_ha.value) f.superficie_ha.value = Number(plot.areaHa.toFixed(4));
  renderPlot();
  syncType(false);
  syncState();
  renderDose();
})();
