/* Calendario de siembra: mismo funcionamiento que AgroAsesor.
   Los datos salen de las referencias agronómicas de la base de datos (json "calendar-references")
   y del catálogo común (json "agro-catalog"). */
(function () {
  'use strict';

  const MONTHS_SHORT = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC'];
  const MONTHS = [
    'Enero',
    'Febrero',
    'Marzo',
    'Abril',
    'Mayo',
    'Junio',
    'Julio',
    'Agosto',
    'Septiembre',
    'Octubre',
    'Noviembre',
    'Diciembre',
  ];
  const PHASE_LABELS = { sow: 'Siembra', grow: 'Crecimiento', harvest: 'Cosecha', idle: 'Fuera de ventana' };

  const layout = document.querySelector('.cal-layout');
  const board = document.getElementById('cal-board');
  const detail = document.getElementById('cal-detail');
  const zoneSelect = document.getElementById('zone');
  const cropFilter = document.getElementById('crop-filter');
  const yearSelect = document.getElementById('year');
  if (!layout || !board || !detail || !zoneSelect || !cropFilter || !yearSelect) return;

  function readJson(id, fallback) {
    const node = document.getElementById(id);
    if (!node) return fallback;
    try {
      return JSON.parse(node.textContent);
    } catch (error) {
      return fallback;
    }
  }

  const references = readJson('calendar-references', []);
  const catalog = readJson('agro-catalog', {});
  const config = layout.dataset;
  let selectedId = null;
  let preferredVariety = (config.selectedVariety || '').trim().toLowerCase();

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
    });
  }

  function fmt(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString('es-CL', {
      maximumFractionDigits: digits == null ? 2 : digits,
      minimumFractionDigits: 0,
    });
  }

  function monthRange(start, end) {
    if (!start || !end) return '—';
    if (start === end) return MONTHS[start - 1] || '—';
    return (MONTHS[start - 1] || '—') + ' – ' + (MONTHS[end - 1] || '—');
  }

  function inRange(month, start, end) {
    if (!start || !end) return false;
    if (start <= end) return month >= start && month <= end;
    return month >= start || month <= end;
  }

  function phase(row, month) {
    if (inRange(month, row.sowStart, row.sowEnd)) return 'sow';
    if (inRange(month, row.harvestStart, row.harvestEnd)) return 'harvest';
    if (row.sowEnd && row.harvestStart) {
      const afterSow = (row.sowEnd % 12) + 1;
      let beforeHarvest = row.harvestStart - 1;
      if (beforeHarvest < 1) beforeHarvest = 12;
      // Entre el fin de la siembra y el inicio de la cosecha el cultivo está creciendo.
      if (afterSow !== row.harvestStart && inRange(month, afterSow, beforeHarvest)) return 'grow';
    }
    return 'idle';
  }

  function isVerified(ref) {
    return Boolean(ref.sourceVerified);
  }

  function hasWindow(ref) {
    return Boolean((ref.sowingStartMonth && ref.sowingEndMonth) || (ref.harvestStartMonth && ref.harvestEndMonth));
  }

  function referenceKey(ref) {
    return [ref.cropId, ref.zoneId, String(ref.variety || '').toLowerCase()].join('|');
  }

  // Para el año elegido gana la referencia de ese año; si no existe, vale la recurrente (sin año).
  function effectiveReferences(year) {
    const specific = new Set();
    references.forEach(function (ref) {
      if (ref.referenceYear === year) specific.add(referenceKey(ref));
    });
    return references.filter(function (ref) {
      return (
        hasWindow(ref) &&
        (ref.referenceYear === year || (ref.referenceYear == null && !specific.has(referenceKey(ref))))
      );
    });
  }

  function toRow(ref) {
    return {
      id: ref.id,
      ref: ref,
      crop: ref.crop,
      cropId: ref.cropId,
      zone: ref.zone,
      zoneId: ref.zoneId,
      variety: ref.variety,
      sowStart: ref.sowingStartMonth,
      sowEnd: ref.sowingEndMonth,
      harvestStart: ref.harvestStartMonth,
      harvestEnd: ref.harvestEndMonth,
      notes: ref.sowingConditions,
    };
  }

  function currentYear() {
    return Number(yearSelect.value) || new Date().getFullYear();
  }

  function allRows() {
    return effectiveReferences(currentYear()).map(toRow);
  }

  function rows() {
    return allRows().filter(function (row) {
      if (zoneSelect.value && String(row.zoneId) !== zoneSelect.value) return false;
      if (cropFilter.value && String(row.cropId) !== cropFilter.value) return false;
      return true;
    });
  }

  function optionCount(select) {
    return Array.prototype.filter.call(select.options, function (option) {
      return option.value !== '';
    }).length;
  }

  function renderStats() {
    const all = allRows();
    const varieties = new Set(
      all
        .map(function (row) {
          return (row.variety || '').toLowerCase();
        })
        .filter(Boolean),
    );
    document.getElementById('cal-stats').innerHTML = [
      ['Calendarios', all.length],
      ['Zonas', optionCount(zoneSelect)],
      ['Cultivos', optionCount(cropFilter)],
      ['Variedades', varieties.size],
    ]
      .map(function (pair) {
        return '<div class="info-chip"><span>' + pair[0] + '</span><strong>' + pair[1] + '</strong></div>';
      })
      .join('');
  }

  function cropInfo(row) {
    return (
      (catalog.cultivos || []).find(function (item) {
        return String(item.id) === String(row.cropId);
      }) || null
    );
  }

  function cropProducts(row) {
    return (catalog.insumos || [])
      .filter(function (item) {
        return String(item.cropId) === String(row.cropId) && item.usable !== false;
      })
      .slice(0, 4);
  }

  function splitList(text) {
    return String(text || '')
      .split(/[\n;]+/)
      .map(function (item) {
        return item.trim();
      })
      .filter(function (item) {
        return item && item.length <= 120;
      });
  }

  function cropProblems(row) {
    const names = [];
    [row.ref.pests, row.ref.diseases, row.ref.weeds].forEach(function (text) {
      splitList(text).forEach(function (name) {
        names.push(name);
      });
    });
    const seen = new Set();
    return names
      .filter(function (name) {
        const key = String(name).toLowerCase();
        if (!name || seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .slice(0, 6);
  }

  function productUrl(id) {
    return (config.productUrl || '/productos/0/').replace(/0\/$/, encodeURIComponent(id) + '/');
  }

  function formatDate(iso) {
    const parts = String(iso || '').split('-');
    return parts.length === 3 ? parts[2] + '/' + parts[1] + '/' + parts[0] : iso;
  }

  function sourceNote(ref) {
    if (!isVerified(ref)) return '';
    const label = [ref.sourceInstitution, ref.sourceDocument].filter(Boolean).join(' · ');
    const text = esc(label) + (ref.sourceDate ? ' (consultada el ' + esc(formatDate(ref.sourceDate)) + ')' : '');
    const linked =
      ref.sourceUrl && /^https?:\/\//i.test(ref.sourceUrl)
        ? '<a class="text-link" href="' +
          esc(ref.sourceUrl) +
          '" target="_blank" rel="noopener noreferrer">' +
          text +
          '</a>'
        : text;
    return '<p class="muted cal-source">Fuente verificada: ' + linked + '</p>';
  }

  // La calculadora se abre con el mismo cultivo de la ventana elegida.
  function calcLink(ref) {
    const base = config.calcUrl || '/decisiones/?herramienta=llevar&tipo=semillas';
    return ref && ref.cropId
      ? base + (base.indexOf('?') === -1 ? '?' : '&') + 'crop=' + encodeURIComponent(ref.cropId)
      : base;
  }

  function showDetail(row) {
    if (!row) {
      detail.innerHTML =
        '<h2>Elige un cultivo</h2><p class="muted">Toca una franja del calendario para ver variedad, ventana de siembra y manejo.</p>';
      return;
    }
    selectedId = row.id;
    const ref = row.ref;
    const crop = cropInfo(row);
    const products = cropProducts(row);
    const problems = cropProblems(row);
    const density =
      crop && crop.density != null ? fmt(crop.density) + ' ' + (crop.unit || 'kg') + '/ha de semilla' : '';
    const cycle = ref.cycleDays ? 'Ciclo de referencia: ' + ref.cycleDays + ' días' : '';
    const yieldText = ref.yieldTonHa != null ? 'rendimiento ' + fmt(ref.yieldTonHa, 1) + ' t/ha' : '';
    const manage = [cycle, density, yieldText].filter(Boolean).join(' · ');
    const manageText = [ref.management, ref.integratedManagement].filter(Boolean).join('\n');
    const harvestText = [ref.maturityIndicators, ref.harvestConsiderations].filter(Boolean).join('\n');
    detail.innerHTML =
      '<div class="cal-badges"><span class="badge">' +
      esc(row.zone) +
      '</span>' +
      (isVerified(ref) ? '' : '<span class="badge amber">Demostración · sin fuente verificada</span>') +
      '</div>' +
      '<h2>' +
      esc(row.crop) +
      '</h2>' +
      (row.variety ? '<p class="cal-variety">' + esc(row.variety) + '</p>' : '') +
      '<div class="metric-grid">' +
      '<div class="metric"><span>Siembra</span><strong>' +
      monthRange(row.sowStart, row.sowEnd) +
      '</strong></div>' +
      '<div class="metric"><span>Cosecha</span><strong>' +
      monthRange(row.harvestStart, row.harvestEnd) +
      '</strong></div>' +
      '</div>' +
      (crop && crop.description ? '<p>' + esc(crop.description) + '</p>' : '') +
      (row.notes ? '<p>' + esc(row.notes) + '</p>' : '') +
      (manageText || manage
        ? '<div class="note-card"><h3>Manejo</h3>' +
          (manageText ? '<p>' + esc(manageText).replace(/\n/g, '<br>') + '</p>' : '') +
          (manage ? '<p class="muted">' + esc(manage) + '.</p>' : '') +
          '</div>'
        : '') +
      (harvestText
        ? '<div class="note-card"><h3>Cosecha</h3><p>' + esc(harvestText).replace(/\n/g, '<br>') + '</p></div>'
        : '') +
      (problems.length
        ? '<h3>Problemas frecuentes</h3><ul class="help-list">' +
          problems
            .map(function (name) {
              return '<li>' + esc(name) + '</li>';
            })
            .join('') +
          '</ul>'
        : '') +
      (products.length
        ? '<h3>Insumos del catálogo</h3><ul class="help-list">' +
          products
            .map(function (item) {
              return (
                '<li><a class="text-link" href="' +
                productUrl(item.id) +
                '">' +
                esc(item.name) +
                '</a> · ' +
                esc(item.type) +
                '</li>'
              );
            })
            .join('') +
          '</ul>'
        : '') +
      sourceNote(ref) +
      '<div class="toolbar"><button class="button" type="button" id="cal-pdf">Descargar PDF de esta ventana</button>' +
      '<a class="button secondary" href="' +
      esc(calcLink(ref)) +
      '">Calcular semilla</a></div>';
  }

  function emptyCatalog() {
    const admin = config.adminUrl
      ? '<a class="button secondary" href="' + esc(config.adminUrl) + '">Administrar referencias agronómicas</a>'
      : '<p class="muted">Pide al administrador del catálogo que registre las referencias.</p>';
    return (
      '<div class="cal-empty"><h2>El catálogo todavía no tiene ventanas de siembra</h2>' +
      '<p class="muted">AgroAsesor no inventa datos: las ventanas salen de referencias guardadas en la base de datos, con su cultivo, zona y fuente.</p>' +
      admin +
      '</div>'
    );
  }

  function render(restoreFocus) {
    renderStats();
    const data = rows();
    if (!data.length) {
      board.innerHTML = references.length
        ? '<p class="muted">No hay calendarios para esta zona y cultivo. Prueba otra zona.</p>'
        : emptyCatalog();
      showDetail(null);
      return;
    }
    board.innerHTML =
      '<div class="cal-grid-head"><span>Cultivo</span>' +
      MONTHS_SHORT.map(function (month) {
        return '<span>' + month + '</span>';
      }).join('') +
      '</div>' +
      data
        .map(function (row) {
          const active = String(row.id) === String(selectedId) ? ' is-selected' : '';
          const label =
            row.crop +
            (row.variety ? ' · ' + row.variety : '') +
            ': siembra ' +
            monthRange(row.sowStart, row.sowEnd) +
            '; cosecha ' +
            monthRange(row.harvestStart, row.harvestEnd);
          return (
            '<button type="button" class="cal-row' +
            active +
            '" data-id="' +
            row.id +
            '" aria-pressed="' +
            (active ? 'true' : 'false') +
            '" aria-label="' +
            esc(label) +
            '">' +
            '<span class="cal-name"><strong>' +
            esc(row.crop) +
            '</strong><small>' +
            esc(row.variety || row.zone) +
            '</small></span>' +
            MONTHS_SHORT.map(function (_, index) {
              const kind = phase(row, index + 1);
              return (
                '<span class="cal-cell ' + kind + '" title="' + PHASE_LABELS[kind] + ' · ' + MONTHS[index] + '"></span>'
              );
            }).join('') +
            '</button>'
          );
        })
        .join('');
    let current = data.find(function (row) {
      return String(row.id) === String(selectedId);
    });
    if (!current && preferredVariety) {
      current = data.find(function (row) {
        return (row.variety || '').toLowerCase() === preferredVariety;
      });
      preferredVariety = '';
    }
    current = current || data[0];
    selectedId = current.id;
    board.querySelectorAll('.cal-row').forEach(function (button) {
      const active = String(button.dataset.id) === String(selectedId);
      button.classList.toggle('is-selected', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      if (active && restoreFocus) button.focus({ preventScroll: true });
    });
    showDetail(current);
  }

  board.addEventListener('click', function (event) {
    const button = event.target.closest('[data-id]');
    if (!button) return;
    selectedId = button.dataset.id;
    render(true);
  });

  // El PDF lo genera el servidor (core/pdf) con la referencia elegida y el año que se está mirando.
  detail.addEventListener('click', function (event) {
    if (!event.target.closest('#cal-pdf')) return;
    const data = rows();
    const row =
      data.find(function (item) {
        return String(item.id) === String(selectedId);
      }) || data[0];
    if (!row || !detail.dataset.pdfUrl) return;
    window.location.assign(
      detail.dataset.pdfUrl.replace('/0/', '/' + row.id + '/') + '?anio=' + encodeURIComponent(yearSelect.value),
    );
  });

  cropFilter.addEventListener('change', function () {
    render(false);
  });
  zoneSelect.addEventListener('change', function () {
    render(false);
  });
  yearSelect.addEventListener('change', function () {
    render(false);
  });
  render(false);
})();
