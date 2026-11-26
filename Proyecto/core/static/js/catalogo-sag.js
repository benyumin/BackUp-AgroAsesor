/* Productos de la lista SAG (fertilizantes y bioestimulantes para agricultura orgánica) bajo demanda.
   No vienen en #agro-catalog para no cargar ~750 registros en cada página: se buscan en /productos/buscar/ y
   los que la persona elige quedan en este registro compartido, que leen calc-panel.js, decisiones.js y
   tool-flow.js (window.AgroCatalogoSAG.get). Debe cargarse antes que ellos. */
(function () {
  'use strict';

  const hub = document.querySelector('.decision-center');
  const searchUrl = hub ? hub.dataset.sagSearchUrl : '';
  const known = new Map();

  function remember(items) {
    (items || []).forEach((item) => known.set(String(item.id), item));
    return items || [];
  }

  async function request(params) {
    if (!searchUrl) return { results: [], more: false };
    const response = await fetch(searchUrl + '?' + new URLSearchParams(params).toString(), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok || !(response.headers.get('Content-Type') || '').includes('application/json')) {
      throw new Error('No se pudo consultar la lista SAG.');
    }
    const data = await response.json();
    remember(data.results);
    return data;
  }

  // Trae un producto por su id (enlaces ?insumo= desde la ficha). null si no existe o no es de la lista.
  async function byId(id) {
    if (!/^\d+$/.test(String(id || ''))) return null;
    if (known.has(String(id))) return known.get(String(id));
    try {
      const data = await request({ id: String(id) });
      return data.results[0] || null;
    } catch (error) {
      return null;
    }
  }

  // Estado de un producto en la lista SAG, con el mismo texto en todas las calculadoras:
  // «vigente» (figura en la última lista importada), «historico» (figuró y ya no) o '' (sin registro SAG).
  function estado(item) {
    if (!item || !item.sag) return { key: '', short: '', long: '' };
    if (item.sag.listed) {
      const parts = String(item.sag.listDate || '').split('-');
      const date = parts.length === 3 ? parts[2] + '/' + parts[1] + '/' + parts[0] : '';
      return {
        key: 'vigente',
        short: 'lista SAG vigente',
        long: 'Incluido en la lista SAG orgánica vigente' + (date ? ' (lista del ' + date + ')' : ''),
      };
    }
    return {
      key: 'historico',
      short: 'registro SAG histórico',
      long: 'Registro histórico: ya no figura en la lista SAG orgánica vigente',
    };
  }

  // Texto del resultado guardado según «sag_status» del servidor (core/calculos.py: estado_sag). '' si no aplica.
  function resultado(status) {
    if (status === 'vigente') return 'Lista SAG orgánica vigente (sin dosis documentada)';
    if (status === 'historico') return 'Registro SAG histórico: ya no figura en la lista vigente';
    return '';
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // Buscador dentro de un formulario. host: contenedor vacío; onPick(item): la calculadora agrega y elige el producto.
  function attach(host, onPick) {
    if (!host || !searchUrl) return null;
    host.replaceChildren();
    host.classList.add('sag-search');
    const label = el('label', 'field');
    label.append(el('span', null, 'Buscar en la lista SAG de fertilizantes y bioestimulantes'));
    const input = el('input');
    input.type = 'search';
    input.autocomplete = 'off';
    input.placeholder = 'Nombre comercial, fabricante o sustancia (mín. 2 letras)';
    label.append(input);
    // Escribir aquí no es un dato del cálculo: no debe llegar a los oyentes del formulario (vista previa, pasos).
    ['input', 'change'].forEach((name) => input.addEventListener(name, (event) => event.stopPropagation()));
    const note = el(
      'small',
      'help',
      'Lista oficial para agricultura orgánica. No informa dosis: al elegir un producto escribes tu dosis y el cálculo queda como simulación.',
    );
    label.append(note);
    const list = el('ul', 'sag-results');
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    const status = el('p', 'sag-status muted small');
    status.setAttribute('aria-live', 'polite');
    host.append(label, status, list);

    let timer = null;
    let sequence = 0;
    async function run() {
      const query = input.value.trim();
      list.replaceChildren();
      list.hidden = true;
      if (query.length < 2) {
        status.textContent = '';
        return;
      }
      const mine = ++sequence;
      status.textContent = 'Buscando…';
      try {
        const data = await request({ q: query });
        if (mine !== sequence) return; // llegó una búsqueda más nueva
        status.textContent = data.results.length
          ? data.results.length + (data.more ? '+ resultados (escribe más para acotar)' : ' resultado(s)')
          : 'Sin resultados en la lista SAG.';
        data.results.forEach((item) => {
          const row = el('li');
          const button = el('button', 'sag-result');
          button.type = 'button';
          button.setAttribute('role', 'option');
          button.append(
            el('strong', null, item.name),
            el(
              'small',
              null,
              [item.categoryLabel, item.manufacturer, item.sag && item.sag.function, estado(item).short]
                .filter(Boolean)
                .join(' · '),
            ),
          );
          button.addEventListener('click', () => {
            list.hidden = true;
            list.replaceChildren();
            input.value = '';
            status.textContent = 'Elegiste «' + item.name + '» (' + estado(item).short + ', sin dosis documentada).';
            onPick(item);
          });
          row.append(button);
          list.append(row);
        });
        list.hidden = !data.results.length;
      } catch (error) {
        if (mine === sequence) status.textContent = error.message;
      }
    }
    input.addEventListener('input', () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(run, 300);
    });
    // Enter no envía el formulario de la calculadora: busca de inmediato.
    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      window.clearTimeout(timer);
      run();
    });
    return { input };
  }

  window.AgroCatalogoSAG = {
    get: (id) => known.get(String(id)) || null,
    byId,
    attach,
    estado,
    resultado,
  };
})();
