/* Resumen meteorológico de «Mis predios y potreros».
   Pide el pronóstico al endpoint propio de Django (/api/predios/<id>/clima/); el navegador nunca habla con Open-Meteo.
   Sigue al predio seleccionado en el listado (sin tocar el código del mapa) y no muestra nada que el servidor no haya
   entregado: si falta un dato escribe «—» y si la consulta falla lo dice. */
(function () {
  'use strict';

  var card = document.getElementById('weather-card');
  if (!card) return;
  var template = card.getAttribute('data-weather-url') || '';
  var statusEl = document.getElementById('weather-status');
  var placeEl = document.getElementById('weather-place');
  var content = document.getElementById('weather-content');
  var retry = document.getElementById('weather-retry');
  var list = document.getElementById('farm-picker-list');
  var TIMEOUT_MS = 12000;

  var currentId = null;
  var sequence = 0;
  var controller = null;
  var timer = null;
  var debounce = null;
  var oneDecimal = new Intl.NumberFormat('es-CL', { maximumFractionDigits: 1 });
  var dayFormat = new Intl.DateTimeFormat('es-CL', { weekday: 'short', day: 'numeric', month: 'short' });
  var weekdayFormat = new Intl.DateTimeFormat('es-CL', { weekday: 'short' });
  var integer = new Intl.NumberFormat('es-CL');

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function icon(name) {
    var node = document.createElement('i');
    node.setAttribute('data-lucide', /^[a-z-]+$/.test(name || '') ? name : 'cloud');
    node.setAttribute('aria-hidden', 'true');
    return node;
  }

  function value(number, unit, formatter) {
    if (typeof number !== 'number' || !isFinite(number)) return '—';
    return (formatter || oneDecimal).format(number) + (unit ? ' ' + unit : '');
  }

  function degrees(number) {
    return typeof number === 'number' && isFinite(number) ? oneDecimal.format(number) + '°' : '—';
  }

  function shortTime(iso) {
    var match = /^\d{4}-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(iso || '');
    return match ? match[2] + '-' + match[1] + ' ' + match[3] : '—';
  }

  function weekdayLabel(isoDate) {
    var parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate || '');
    if (!parts) return '—';
    return weekdayFormat.format(new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))).replace('.', '');
  }

  function dayLabel(isoDate) {
    var parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate || '');
    if (!parts) return '—';
    return dayFormat.format(new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])));
  }

  function selectedId() {
    var node = document.querySelector('.farm-card.is-selected');
    return node ? node.getAttribute('data-farm-card') : null;
  }

  function firstId() {
    var node = document.querySelector('.farm-card[data-farm-card]');
    return node ? node.getAttribute('data-farm-card') : null;
  }

  function farmName(id) {
    var title = document.querySelector('.farm-card[data-farm-card="' + id + '"] .entity-title');
    return title ? title.textContent.trim() : 'el predio';
  }

  function status(text, isError) {
    statusEl.hidden = !text;
    statusEl.textContent = text || '';
    statusEl.classList.toggle('alert', Boolean(isError));
    retry.hidden = !isError;
  }

  function showIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
  }

  function clearContent() {
    while (content.firstChild) content.removeChild(content.firstChild);
    content.hidden = true;
    placeEl.hidden = true;
  }

  function renderNow(data) {
    var now = data.actual;
    var units = data.unidades || {};
    var box = el('div', 'weather-now');
    var main = el('div', 'weather-now-main');
    main.appendChild(icon(now.icono));
    var text = el('div');
    text.appendChild(el('span', 'weather-temp', value(now.temperatura, units.temperatura)));
    text.appendChild(el('span', 'weather-desc', now.descripcion || 'Sin descripción'));
    main.appendChild(text);
    box.appendChild(main);
    var metrics = el('dl', 'weather-metrics');
    [
      ['Humedad', value(now.humedad, units.humedad)],
      ['Viento', value(now.viento, units.viento)],
      ['Lluvia', value(now.precipitacion, units.precipitacion)]
    ].forEach(function (pair) {
      var item = el('div', 'weather-metric');
      item.appendChild(el('dt', null, pair[0]));
      item.appendChild(el('dd', null, pair[1]));
      metrics.appendChild(item);
    });
    box.appendChild(metrics);
    box.appendChild(el('p', 'weather-note', 'Ahora: estimado por modelo (no es una estación) · ' + shortTime(now.hora)));
    return box;
  }

  function dayDetail(day, units, today) {
    var name = day.fecha === today ? 'Hoy' : dayLabel(day.fecha);
    return name + ': ' + (day.descripcion || 'Sin descripción') +
      ' · máx. ' + degrees(day.temp_max) + ' · mín. ' + degrees(day.temp_min) +
      ' · prob. de lluvia ' + value(day.prob_precipitacion, units.prob_precipitacion || '%', integer) +
      ' · lluvia ' + value(day.precipitacion, units.precipitacion) +
      ' · viento máx. ' + value(day.viento_max, units.viento);
  }

  function renderForecast(data, today) {
    var units = data.unidades || {};
    var block = el('div');
    var title = el('h3', 'weather-block-title', 'Próximos días');
    title.appendChild(el('span', 'state-chip is-neutral', 'Previsto'));
    block.appendChild(title);
    var strip = el('ul', 'weather-days');
    data.pronostico.forEach(function (day) {
      var item = el('li', 'weather-day');
      item.title = dayDetail(day, units, today);
      item.appendChild(el('span', 'weather-day-name', day.fecha === today ? 'Hoy' : weekdayLabel(day.fecha)));
      item.appendChild(icon(day.icono));
      var temps = el('span', 'weather-day-temp');
      temps.appendChild(el('strong', null, degrees(day.temp_max)));
      temps.appendChild(el('span', null, degrees(day.temp_min)));
      item.appendChild(temps);
      var rain = el('span', 'weather-day-rain');
      rain.appendChild(icon('droplets'));
      rain.appendChild(document.createTextNode(value(day.prob_precipitacion, units.prob_precipitacion || '%', integer)));
      item.appendChild(rain);
      strip.appendChild(item);
    });
    block.appendChild(strip);
    return block;
  }

  function render(data, id) {
    clearContent();
    placeEl.textContent = '«' + farmName(id) + '»';
    placeEl.hidden = false;
    var today = data.actual && data.actual.hora ? data.actual.hora.slice(0, 10) : null;
    if (data.actual) content.appendChild(renderNow(data));
    if (data.pronostico && data.pronostico.length) {
      content.appendChild(renderForecast(data, today));
    }
    content.hidden = false;
    status('', false);
    showIcons();
  }

  function fail(message) {
    clearContent();
    status(message, true);
  }

  function finish() {
    if (timer) window.clearTimeout(timer);
    timer = null;
    controller = null;
  }

  function load(id) {
    if (!id || !template) {
      clearContent();
      status('Selecciona un predio para ver su pronóstico.', false);
      return;
    }
    if (controller) controller.abort();
    if (timer) window.clearTimeout(timer);
    var mine = ++sequence;
    currentId = id;
    var request = new AbortController();
    controller = request;
    timer = window.setTimeout(function () { request.abort(); }, TIMEOUT_MS);
    clearContent();
    status('Consultando el pronóstico de «' + farmName(id) + '»…', false);
    fetch(template.replace('/0/', '/' + encodeURIComponent(id) + '/'), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: request.signal
    }).then(function (response) {
      return response.json().then(
        function (data) { return { status: response.status, data: data }; },
        function () { return { status: response.status, data: null }; }
      );
    }).then(function (result) {
      if (mine !== sequence) return;
      finish();
      if (result.data && result.data.ok) return render(result.data, id);
      if (!result.data) return fail('No pudimos leer la respuesta. Si tu sesión terminó, vuelve a iniciar sesión.');
      var error = result.data.error || {};
      fail(error.mensaje || 'No se pudo obtener el pronóstico en este momento.');
    }).catch(function () {
      if (mine !== sequence) return;
      finish();
      fail('No se pudo obtener el pronóstico ahora. El resto de AgroAsesor funciona con normalidad.');
    });
  }

  retry.addEventListener('click', function () { load(currentId || selectedId() || firstId()); });

  if (list && 'MutationObserver' in window) {
    new MutationObserver(function () {
      window.clearTimeout(debounce);
      debounce = window.setTimeout(function () {
        var id = selectedId();
        if (id && id !== currentId) load(id);
      }, 200);
    }).observe(list, { attributes: true, attributeFilter: ['class'], subtree: true });
  }

  load(selectedId() || firstId());
})();
