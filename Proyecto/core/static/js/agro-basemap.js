/* Mapa base de AgroAsesor: Google Maps (Maps JavaScript API) debajo de las herramientas de dibujo.
 *
 * - Con GOOGLE_MAPS_API_KEY configurada, el fondo del mapa (normal y satélite) es Google Maps real.
 *   Leaflet sigue encima solo para dibujar, editar vértices y mostrar predios y potreros (GeoJSON);
 *   no se usa la Drawing Library de Google (retirada).
 * - Sin clave, o si Google rechaza la clave (restricciones, API no habilitada, facturación), se avisa en
 *   pantalla con el motivo y se usa el mapa de respaldo (OpenStreetMap / Esri) para poder seguir trabajando.
 *
 * Uso:  const base = AgroBasemap.create(map, config, { onNotice, onTileError, onTileLoad });  base.select('satellite');
 */
(() => {
  'use strict';
  const ESRI_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  const ESRI_ATTRIBUTION = '&copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community';
  const MAX_ZOOM = 19;
  const LOAD_TIMEOUT_MS = 15000;
  // Si Google tarda más que esto, el mapa de respaldo se muestra mientras tanto (el fondo no queda vacío); cuando
  // Google llega, lo reemplaza. Solo después de LOAD_TIMEOUT_MS se avisa que Google no respondió.
  const PROVISIONAL_AFTER_MS = 2500;
  const GOOGLE_TYPES = { normal: 'roadmap', satellite: 'hybrid' };
  const authListeners = new Set();
  let authFailed = false;
  let loader = null;

  // Google llama a esta función global cuando la clave no es válida para esta página.
  const previousAuthFailure = window.gm_authFailure;
  window.gm_authFailure = () => {
    authFailed = true;
    authListeners.forEach((listener) => listener());
    if (typeof previousAuthFailure === 'function') previousAuthFailure();
  };

  function loadGoogle(key) {
    if (window.google && window.google.maps && window.google.maps.Map) return Promise.resolve(window.google.maps);
    if (loader) return loader;
    loader = new Promise((resolve, reject) => {
      const callbackName = '__agroGoogleMapsReady';
      const timer = window.setTimeout(() => reject(new Error('timeout')), LOAD_TIMEOUT_MS);
      window[callbackName] = () => {
        window.clearTimeout(timer);
        resolve(window.google.maps);
      };
      const script = document.createElement('script');
      script.src =
        'https://maps.googleapis.com/maps/api/js?key=' +
        encodeURIComponent(key) +
        '&v=weekly&loading=async&language=es&region=CL&callback=' +
        callbackName;
      script.async = true;
      script.onerror = () => {
        window.clearTimeout(timer);
        reject(new Error('network'));
      };
      document.head.appendChild(script);
    });
    return loader;
  }

  // Opciones del mapa de dibujo (Leaflet). Con Google Maps activo, Google maneja TODOS los gestos (rueda, arrastre,
  // dos dedos, doble clic y sus botones + y −) con su animación oficial, y Leaflet solo sigue su cámara para mantener
  // los polígonos en su lugar: los gestos de Leaflet se apagan, así que ningún evento se procesa dos veces.
  // Sin Google (mapa de respaldo) Leaflet maneja los gestos y la rueda avanza un nivel por paso.
  const MAP_OPTIONS = { zoomControl: true, zoomAnimation: false, scrollWheelZoom: false, zoomSnap: 1, zoomDelta: 1 };
  const GESTURES = ['dragging', 'touchZoom', 'doubleClickZoom', 'boxZoom', 'keyboard'];
  const WHEEL_STEP_MS = 70; // tiempo mínimo entre dos niveles de zoom con la rueda (solo mapa de respaldo)
  const WHEEL_STEP_DELTA = 50; // desplazamiento de rueda (px) que equivale a un nivel

  function enableFastWheel(map, isGoogleActive) {
    const container = map.getContainer();
    let pending = 0;
    let lastStep = 0;
    L.DomEvent.on(container, 'wheel', (event) => {
      if (isGoogleActive()) return; // la rueda la maneja Google
      L.DomEvent.preventDefault(event); // la rueda sobre el mapa hace zoom, no desplaza la página
      pending += L.DomEvent.getWheelDelta(event);
      pending = Math.max(-WHEEL_STEP_DELTA, Math.min(WHEEL_STEP_DELTA, pending));
      const now = Date.now();
      if (Math.abs(pending) < WHEEL_STEP_DELTA || now - lastStep < WHEEL_STEP_MS) return;
      const direction = pending > 0 ? 1 : -1;
      pending = 0;
      const target = map.getZoom() + direction;
      if (target < map.getMinZoom() || target > map.getMaxZoom()) return;
      lastStep = now;
      map.setZoomAround(map.mouseEventToContainerPoint(event), target, { animate: false });
    });
  }

  // ¿El punto está dentro del polígono? (para saber qué predio o potrero se tocó sobre el mapa de Google)
  function ringContains(ring, point) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i];
      const b = ring[j];
      if (a.lat > point.lat !== b.lat > point.lat && point.lng < ((b.lng - a.lng) * (point.lat - a.lat)) / (b.lat - a.lat) + a.lng) {
        inside = !inside;
      }
    }
    return inside;
  }
  function outerRings(latlngs) {
    if (!latlngs.length) return [];
    if (!Array.isArray(latlngs[0])) return [latlngs];
    if (!Array.isArray(latlngs[0][0])) return [latlngs[0]];
    return latlngs.map((polygon) => polygon[0]);
  }
  function polygonAt(map, point) {
    let best = null;
    let bestSize = Infinity;
    map.eachLayer((layer) => {
      if (!(layer instanceof L.Polygon) || !layer.listens('click', true)) return;
      if (!outerRings(layer.getLatLngs()).some((ring) => ringContains(ring, point))) return;
      const bounds = layer.getBounds();
      const size = (bounds.getNorth() - bounds.getSouth()) * (bounds.getEast() - bounds.getWest());
      if (size < bestSize) {
        best = layer;
        bestSize = size; // el más chico: un potrero gana sobre el predio que lo contiene
      }
    });
    return best;
  }

  function create(map, config, options) {
    const opts = options || {};
    enableFastWheel(map, () => state.engine === 'google');
    const notice = (message, kind) => {
      if (opts.onNotice) opts.onNotice(message, kind || 'info');
      // Aviso visible sobre el mapa (no se oculta el problema detrás del mapa de respaldo).
      const container = map.getContainer();
      let box = container.querySelector('.map-base-notice');
      if (!box) {
        box = document.createElement('p');
        box.className = 'map-base-notice';
        box.setAttribute('role', 'status');
        container.appendChild(box);
      }
      box.dataset.kind = kind || 'info';
      box.textContent = message;
    };
    const state = {
      view: 'normal',
      engine: 'pending',
      provisional: false, // mapa de respaldo mostrado solo mientras Google termina de cargar
      gmap: null,
      holder: null,
      following: false,
      zoomControl: map.zoomControl,
    };
    const tiles = {
      normal: L.tileLayer(config.tile_url, { attribution: config.attribution, maxZoom: MAX_ZOOM, crossOrigin: true }),
      satellite: L.tileLayer(ESRI_URL, { attribution: ESRI_ATTRIBUTION, maxZoom: MAX_ZOOM, crossOrigin: true }),
    };
    Object.keys(tiles).forEach((view) => {
      tiles[view].on('tileerror', () => {
        if (state.engine === 'tiles' && state.view === view && opts.onTileError) opts.onTileError();
      });
      tiles[view].on('tileload', () => {
        if (state.engine === 'tiles' && state.view === view && opts.onTileLoad) opts.onTileLoad();
      });
    });
    map.setMaxZoom(MAX_ZOOM);

    function apply() {
      if (state.engine === 'google') {
        state.gmap.setMapTypeId(GOOGLE_TYPES[state.view]);
        return;
      }
      if (state.engine !== 'tiles') return;
      Object.keys(tiles).forEach((view) => {
        if (view !== state.view && map.hasLayer(tiles[view])) map.removeLayer(tiles[view]);
      });
      if (!map.hasLayer(tiles[state.view])) tiles[state.view].addTo(map);
    }

    function useTiles(provisional) {
      if (state.holder) state.holder.remove();
      state.holder = null;
      state.gmap = null;
      state.engine = 'tiles';
      state.provisional = Boolean(provisional);
      map.agroGoogleMaster = false;
      map.getContainer().classList.remove('has-google-base', 'is-google-zooming');
      // Leaflet vuelve a manejar los gestos y muestra sus botones + y −.
      GESTURES.forEach((name) => map[name] && map[name].enable());
      map.options.zoomSnap = MAP_OPTIONS.zoomSnap;
      if (state.zoomControl && !state.zoomControl._map) state.zoomControl.addTo(map);
      if (map.attributionControl && !map.attributionControl._map) map.attributionControl.addTo(map);
      apply();
    }

    // Leaflet → Google: solo cuando el programa mueve el mapa (encuadrar un predio, «Mi ubicación», etc.).
    function pushToGoogle() {
      if (!state.gmap || state.following) return;
      try {
        const center = map.getCenter();
        state.gmap.moveCamera({ center: { lat: center.lat, lng: center.lng }, zoom: map.getZoom() });
      } catch (error) {
        /* el mapa todavía no tiene vista (se ajusta al cargar la geometría) */
      }
    }

    // Google → Leaflet: los polígonos siguen la cámara de Google mientras la persona mueve o acerca el mapa.
    function followGoogle() {
      const center = state.gmap && state.gmap.getCenter();
      const zoom = state.gmap && state.gmap.getZoom();
      if (!center || !Number.isFinite(zoom)) return;
      state.following = true;
      try {
        map.setView([center.lat(), center.lng()], zoom, { animate: false });
      } finally {
        state.following = false;
      }
    }

    function useGoogle(maps) {
      const container = map.getContainer();
      // Si el respaldo provisional estaba a la vista, sus teselas se retiran: desde aquí el fondo es Google.
      Object.keys(tiles).forEach((view) => {
        if (map.hasLayer(tiles[view])) map.removeLayer(tiles[view]);
      });
      state.provisional = false;
      const holder = document.createElement('div');
      holder.className = 'agro-google-base';
      container.insertBefore(holder, container.firstChild);
      state.holder = holder;
      let start = { center: { lat: -33.6866, lng: -71.2167 }, zoom: 12 };
      try {
        const center = map.getCenter();
        start = { center: { lat: center.lat, lng: center.lng }, zoom: map.getZoom() };
      } catch (error) {
        /* sin vista todavía */
      }
      const mapOptions = {
        center: start.center,
        zoom: start.zoom,
        mapTypeId: GOOGLE_TYPES[state.view],
        // Experiencia oficial de Google Maps: sus gestos, su animación de zoom y sus botones + y −.
        disableDefaultUI: true,
        zoomControl: true,
        gestureHandling: 'greedy',
        clickableIcons: false,
        maxZoom: MAX_ZOOM,
      };
      if (config.google_map_id) mapOptions.mapId = config.google_map_id; // opcional: mapa vectorial
      state.gmap = new maps.Map(holder, mapOptions);
      state.engine = 'google';
      map.agroGoogleMaster = true;
      container.classList.add('has-google-base');

      // Leaflet deja de procesar gestos y oculta sus botones: solo dibuja encima y sigue a Google.
      GESTURES.forEach((name) => map[name] && map[name].disable());
      map.options.zoomSnap = 0; // acepta el zoom intermedio de la animación de Google
      if (state.zoomControl) state.zoomControl.remove();
      if (map.attributionControl) map.attributionControl.remove(); // la atribución la muestra Google

      state.gmap.addListener('center_changed', followGoogle);
      state.gmap.addListener('zoom_changed', () => {
        // En mapas de teselas Google anima el acercamiento sin informar los pasos intermedios: los polígonos se
        // ocultan ese instante y reaparecen ya alineados (con zoom continuo no hace falta).
        if (Number.isInteger(state.gmap.getZoom())) container.classList.add('is-google-zooming');
        followGoogle();
      });
      state.gmap.addListener('idle', () => {
        container.classList.remove('is-google-zooming');
        followGoogle();
      });
      // Un toque sobre el mapa de Google (no un arrastre) se entrega a las herramientas de dibujo y selección.
      state.gmap.addListener('click', (event) => {
        if (!event.latLng) return;
        const latlng = L.latLng(event.latLng.lat(), event.latLng.lng());
        const data = {
          latlng,
          layerPoint: map.latLngToLayerPoint(latlng),
          containerPoint: map.latLngToContainerPoint(latlng),
          originalEvent: event.domEvent,
          fromGoogle: true,
        };
        const polygon = polygonAt(map, latlng);
        if (polygon) polygon.fire('click', data, true);
        map.fire('click', data);
      });
      map.on('move zoom zoomend moveend resize viewreset', pushToGoogle);
      pushToGoogle();
      apply();
    }

    function googleRejected() {
      if (state.engine === 'tiles' && !state.provisional) return;
      useTiles();
      notice(
        'Google Maps rechazó la clave de API. Revisa en Google Cloud que «Maps JavaScript API» esté habilitada, ' +
          'que la facturación esté activa y que la clave permita este sitio (' +
          window.location.origin +
          '). Mientras tanto se muestra el mapa de respaldo (OpenStreetMap).',
        'error',
      );
    }

    if (!config.google_maps_key) {
      useTiles();
      if (config.debug) {
        notice(
          'Google Maps no está configurado: falta GOOGLE_MAPS_API_KEY en el archivo .env. ' +
            'Se muestra el mapa de respaldo (OpenStreetMap).',
          'warning',
        );
      }
    } else if (authFailed) {
      googleRejected();
    } else {
      authListeners.add(googleRejected);
      const provisionalTimer = window.setTimeout(() => {
        if (state.engine === 'pending') useTiles(true);
      }, PROVISIONAL_AFTER_MS);
      loadGoogle(config.google_maps_key)
        .then((maps) => {
          window.clearTimeout(provisionalTimer);
          if (authFailed) return;
          useGoogle(maps);
        })
        .catch((error) => {
          window.clearTimeout(provisionalTimer);
          useTiles();
          notice(
            (error && error.message === 'timeout'
              ? 'Google Maps tardó demasiado en responder.'
              : 'No se pudo descargar Google Maps (revisa la conexión a internet).') +
              ' Se muestra el mapa de respaldo (OpenStreetMap).',
            'error',
          );
        });
    }

    return {
      select(view) {
        state.view = view === 'satellite' ? 'satellite' : 'normal';
        apply();
      },
      get view() {
        return state.view;
      },
      get engine() {
        return state.engine;
      },
    };
  }

  window.AgroBasemap = { create, MAP_OPTIONS };
})();
