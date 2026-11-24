(() => {
  'use strict';
  // Pestañas de la ficha (hoy solo Información): funcionan aunque el predio no tenga geometría (sin mapa).
  const tabs = [
    [document.getElementById('tab-information'), document.getElementById('predio-information')],
    [document.getElementById('tab-history'), document.getElementById('predio-history')],
  ];
  const activateTab = (button) => {
    tabs.forEach(([otherButton, otherPanel]) => {
      if (!otherButton || !otherPanel) return;
      const selected = otherButton === button;
      otherButton.classList.toggle('is-active', selected);
      otherButton.setAttribute('aria-selected', String(selected));
      otherPanel.hidden = !selected;
    });
  };
  tabs.forEach(([button, panel]) => {
    if (!button || !panel) return;
    button.addEventListener('click', () => activateTab(button));
  });
  // Enlaces antiguos con #predio-history: sin esa pestaña, Información sigue visible.
  const historyTab = document.getElementById('tab-history');
  if (window.location.hash === '#predio-history' && historyTab) {
    activateTab(historyTab);
  }

  const geometryNode = document.getElementById('predio-detail-geometry');
  const mapElement = document.getElementById('predio-detail-map');
  const configNode = document.getElementById('maps-config');
  if (!geometryNode || !mapElement || !configNode || !window.L || !window.AgroBasemap) return;

  const geometry = JSON.parse(geometryNode.textContent);
  const config = JSON.parse(configNode.textContent);
  const map = L.map(mapElement, window.AgroBasemap.MAP_OPTIONS);
  const status = document.getElementById('predio-map-status');
  function showMapStatus(message) {
    if (status) status.textContent = message;
  }
  // Fondo del mapa: Google Maps si hay clave configurada; si no (o si Google la rechaza) el mapa de respaldo.
  const base = window.AgroBasemap.create(map, config, {
    onTileError: () => showMapStatus('No se cargaron algunas teselas del mapa. Comprueba la conexión o cambia la vista.'),
    onTileLoad: () => showMapStatus(''),
  });
  document.querySelectorAll('[data-map-layer]').forEach((button) => {
    button.addEventListener('click', () => {
      base.select(button.dataset.mapLayer === 'satellite' ? 'satellite' : 'normal');
      document.querySelectorAll('[data-map-layer]').forEach((item) => {
        const selected = item === button;
        item.classList.toggle('is-active', selected);
        item.setAttribute('aria-pressed', String(selected));
      });
    });
  });
  const polygon = L.geoJSON(geometry, {
    style: { color: '#0b6b4b', weight: 3, fillColor: '#34b27b', fillOpacity: 0.26 },
  }).addTo(map);
  if (polygon.getBounds().isValid()) map.fitBounds(polygon.getBounds(), { padding: [24, 24] });

  const rawGeometry = geometry.type === 'Feature' ? geometry.geometry : geometry;
  const ring = rawGeometry.coordinates[0] || [];
  const vertices =
    ring.length > 1 && JSON.stringify(ring[0]) === JSON.stringify(ring[ring.length - 1]) ? ring.slice(0, -1) : ring;
  vertices.forEach(([longitude, latitude]) => {
    L.circleMarker([latitude, longitude], {
      radius: 4,
      color: '#0b6b4b',
      fillColor: '#fff',
      fillOpacity: 1,
      weight: 2,
    }).addTo(map);
  });
  const vertexList = document.getElementById('predio-vertices');
  if (vertexList) {
    vertices.forEach(([longitude, latitude], index) => {
      const item = document.createElement('li');
      item.textContent = index + 1 + '. ' + latitude.toFixed(6) + ', ' + longitude.toFixed(6);
      vertexList.appendChild(item);
    });
  }

  // Indicador con el nivel de zoom real del mapa.
  const zoomChip = document.getElementById('predio-map-zoom');
  if (zoomChip) {
    const showZoom = () => {
      zoomChip.textContent = 'Zoom ' + Math.round(map.getZoom());
      zoomChip.hidden = false;
    };
    map.on('zoomend', showZoom);
    showZoom();
  }
  window.setTimeout(() => map.invalidateSize(), 0);
})();
