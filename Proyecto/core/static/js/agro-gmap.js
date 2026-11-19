/* AgroAsesor · mapa sobre Google Maps JavaScript API (un solo motor cartográfico).
 *
 * Fase 0 de la migración: cargador con aviso de «mapa no disponible», vistas normal y satélite, polígonos a
 * partir de GeoJSON ([lng, lat]), marcadores avanzados y un editor de vértices con dos modos:
 *   - «native»: el editor integrado de Google (editable: true) más gestos propios para agregar y eliminar puntos;
 *   - «custom»: tiradores propios de 44 px, pensados para el dedo.
 * Las validaciones (sin autointersección y, si se pide, dentro del predio) se aplican en ambos modos.
 * No usa la Drawing Library de Google (retirada) ni mapa de respaldo: si Google no responde, se avisa en pantalla
 * y el resto de la aplicación (listado, datos agrícolas, hectáreas) sigue funcionando.
 *
 * Uso:
 *   AgroGMap.create(elemento, config, { onUnavailable }).then((ctrl) => {
 *     const forma = ctrl.addPolygon(AgroGMap.geo.ringFromGeoJSON(geojson));
 *     const editor = ctrl.edit(forma, { mode: 'native', validate, onChange, onReject, onSelect });
 *   });
 */
(() => {
  'use strict';

  const API_VERSION = 'weekly';
  const LOAD_TIMEOUT_MS = 15000;
  const MAX_ZOOM = 19;
  const DEMO_MAP_ID = 'DEMO_MAP_ID'; // solo pruebas: en producción se define GOOGLE_MAPS_MAP_ID (Map ID propio)
  const DEFAULT_CENTER = { lat: -33.6866, lng: -71.2167 }; // Melipilla
  const GOOGLE_TYPES = { normal: 'roadmap', satellite: 'hybrid' };
  const MIN_VERTICES = 3;
  const RESTORE_DELAY_MS = 350;
  const INVALID_STYLE = { strokeColor: '#b42318', fillColor: '#f04438' };
  const UNAVAILABLE_TEXT =
    'El mapa no está disponible temporalmente. El listado de predios, los datos agrícolas y las hectáreas ' +
    'registradas siguen funcionando. Inténtalo de nuevo en unos minutos.';
  const SELF_CROSS_TEXT = 'El contorno no puede cruzarse consigo mismo.';
  const MIN_POINTS_TEXT = 'Un contorno necesita al menos 3 puntos.';
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

  // ---------- Geometría de los contornos (GeoJSON [lng, lat]; mismas reglas que predios-map.js) ----------

  function orientation(a, b, c) {
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  }

  function segmentsIntersect(a, b, c, d) {
    const epsilon = 1e-12;
    const onSegment = (first, second, point) =>
      Math.min(first[0], second[0]) - epsilon <= point[0] &&
      point[0] <= Math.max(first[0], second[0]) + epsilon &&
      Math.min(first[1], second[1]) - epsilon <= point[1] &&
      point[1] <= Math.max(first[1], second[1]) + epsilon;
    const values = [orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)];
    if (values[0] * values[1] < 0 && values[2] * values[3] < 0) return true;
    const points = [c, d, a, b];
    return values.some(
      (value, index) => Math.abs(value) < epsilon && onSegment(index < 2 ? a : c, index < 2 ? b : d, points[index]),
    );
  }

  function hasSelfIntersection(points) {
    const edges = points.map((point, index) => [point, points[(index + 1) % points.length]]);
    for (let first = 0; first < edges.length; first += 1) {
      for (let second = first + 1; second < edges.length; second += 1) {
        if (second === first + 1 || (first === 0 && second === edges.length - 1)) continue;
        if (segmentsIntersect(...edges[first], ...edges[second])) return true;
      }
    }
    return false;
  }

  function pointInRing(point, ring) {
    let inside = false;
    for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index, index += 1) {
      const [x, y] = point;
      const [x1, y1] = ring[index];
      const [x2, y2] = ring[previous];
      const cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1);
      if (
        Math.abs(cross) < 1e-12 &&
        x >= Math.min(x1, x2) - 1e-12 &&
        x <= Math.max(x1, x2) + 1e-12 &&
        y >= Math.min(y1, y2) - 1e-12 &&
        y <= Math.max(y1, y2) + 1e-12
      )
        return true;
      if (y1 > y !== y2 > y && x < ((x2 - x1) * (y - y1)) / (y2 - y1) + x1) {
        inside = !inside;
      }
    }
    return inside;
  }

  // ¿El contorno `points` queda dentro de `boundary` (el predio) sin cruzar su límite?
  function ringInside(points, boundary) {
    if (boundary.length < 3 || points.some((point) => !pointInRing(point, boundary))) return false;
    for (let index = 0; index < points.length; index += 1) {
      const first = points[index];
      const second = points[(index + 1) % points.length];
      for (let edge = 0; edge < boundary.length; edge += 1) {
        const third = boundary[edge];
        const fourth = boundary[(edge + 1) % boundary.length];
        const crossesBoundary =
          orientation(first, second, third) * orientation(first, second, fourth) < 0 &&
          orientation(third, fourth, first) * orientation(third, fourth, second) < 0;
        if (crossesBoundary) return false;
      }
    }
    return true;
  }

  const geo = {
    orientation,
    segmentsIntersect,
    hasSelfIntersection,
    pointInRing,
    ringInside,
    // GeoJSON (Feature o Polygon) → anillo abierto [[lng, lat], …] sin repetir el primer punto.
    ringFromGeoJSON(value) {
      if (!value) return [];
      const geometry = value.type === 'Feature' ? value.geometry : value;
      if (!geometry || geometry.type !== 'Polygon' || !Array.isArray(geometry.coordinates)) return [];
      const ring = (geometry.coordinates[0] || []).map((point) => point.slice());
      if (ring.length > 1 && JSON.stringify(ring[0]) === JSON.stringify(ring[ring.length - 1])) ring.pop();
      return ring;
    },
    // Anillo → Feature con el mismo formato que se guarda hoy (Polygon cerrado, propiedades vacías).
    featureFromRing(ring) {
      return {
        type: 'Feature',
        properties: {},
        geometry: { type: 'Polygon', coordinates: [[...ring.map((point) => point.slice()), ring[0].slice()]] },
      };
    },
    toLatLngs(ring) {
      return ring.map(([lng, lat]) => ({ lat, lng }));
    },
    fromPath(path) {
      const ring = [];
      path.forEach((latLng) => ring.push([latLng.lng(), latLng.lat()]));
      return ring;
    },
    midpoint(a, b) {
      return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    },
  };

  // Sin autointersección, y las reglas extra que pida quien llama (por ejemplo «dentro del predio»).
  // Devuelve true o el texto del problema.
  function verdictFor(ring, extra) {
    if (hasSelfIntersection(ring)) return SELF_CROSS_TEXT;
    return extra ? extra(ring) : true;
  }

  // ---------- Carga de Google Maps y aviso cuando no está disponible ----------

  function loadGoogle(key) {
    if (loader) return loader;
    loader = new Promise((resolve, reject) => {
      if (window.google && window.google.maps && window.google.maps.importLibrary) {
        resolve(window.google.maps);
        return;
      }
      const callbackName = '__agroGMapReady';
      const timer = window.setTimeout(() => reject(new Error('timeout')), LOAD_TIMEOUT_MS);
      window[callbackName] = () => {
        window.clearTimeout(timer);
        resolve(window.google.maps);
      };
      const script = document.createElement('script');
      script.src =
        'https://maps.googleapis.com/maps/api/js?key=' +
        encodeURIComponent(key) +
        '&v=' +
        API_VERSION +
        '&loading=async&language=es&region=CL&callback=' +
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

  function showNotice(element, message, kind) {
    let box = element.querySelector('.map-base-notice');
    if (!box) {
      if (window.getComputedStyle(element).position === 'static') element.style.position = 'relative';
      box = document.createElement('p');
      box.className = 'map-base-notice';
      box.setAttribute('role', 'status');
      element.appendChild(box);
    }
    box.dataset.kind = kind || 'info';
    box.textContent = message;
  }

  function adminDetail(reason) {
    const details = {
      'no-key': 'falta GOOGLE_MAPS_API_KEY en el archivo .env.',
      auth:
        'Google rechazó la clave: revisa que «Maps JavaScript API» esté habilitada, que la facturación esté activa ' +
        'y que la clave permita este sitio (' +
        window.location.origin +
        ').',
      timeout: 'Google Maps tardó demasiado en responder.',
      network: 'no se pudo descargar Google Maps (revisa la conexión a internet).',
    };
    return details[reason] || reason;
  }

  // ---------- Tiradores propios (modo «custom») y anillo de selección (modo «native») ----------

  function paintHandle(wrapper, selected) {
    const dot = wrapper.firstChild;
    dot.style.borderColor = selected ? '#c9a227' : '#255b37';
    dot.style.background = selected ? '#fde68a' : '#fff';
  }

  // Un AdvancedMarker se ancla abajo al centro: translateY(50%) deja el centro del círculo sobre el punto.
  function handleElement(kind, selected) {
    const wrapper = document.createElement('div');
    wrapper.style.cssText =
      'width:44px;height:44px;display:grid;place-items:center;transform:translateY(50%);touch-action:none;cursor:grab';
    const dot = document.createElement('div');
    const isMid = kind === 'mid';
    const size = isMid ? 20 : 26;
    dot.style.cssText =
      'box-sizing:border-box;border-radius:50%;border:3px solid #255b37;background:#fff;' +
      'box-shadow:0 1px 4px rgba(0,0,0,.45);width:' +
      size +
      'px;height:' +
      size +
      'px;' +
      (isMid ? 'display:grid;place-items:center;font:700 15px/1 sans-serif;color:#255b37;opacity:.9;cursor:pointer' : '');
    if (isMid) dot.textContent = '+';
    wrapper.appendChild(dot);
    paintHandle(wrapper, selected);
    return wrapper;
  }

  function selectionRing() {
    const ring = document.createElement('div');
    ring.style.cssText =
      'width:40px;height:40px;box-sizing:border-box;border:4px solid #c9a227;border-radius:50%;' +
      'background:rgba(253,230,138,.35);pointer-events:none;transform:translateY(50%)';
    return ring;
  }

  // ---------- Editor nativo de Google (editable: true) + gestos propios ----------
  // Mover vértices y agregar puntos (tirador del punto medio) los hace Google. Eliminar un vértice no tiene gesto
  // táctil nativo: clic derecho (escritorio), o tocar el vértice y pulsar «eliminar» (táctil). Tocar el tirador del
  // punto medio también agrega un punto.

  function editNative(ctrl, shape, o) {
    const { maps } = ctrl;
    const path = shape.getPath();
    const minVertices = o.minVertices || MIN_VERTICES;
    const normal = { strokeColor: shape.get('strokeColor'), fillColor: shape.get('fillColor') };
    const emit = (name, detail) => {
      if (o.onEvent) o.onEvent(name, detail);
    };
    const reject = (message) => {
      if (o.onReject) o.onReject(message);
    };
    let lastValid = geo.fromPath(path);
    let guard = false;
    let timer = null;
    let selected = null;
    let ringMarker = null;

    function setSelected(index) {
      selected = index;
      if (ringMarker) {
        ringMarker.map = null;
        ringMarker = null;
      }
      if (index !== null && index < path.getLength()) {
        ringMarker = new ctrl.AdvancedMarkerElement({
          map: ctrl.map,
          position: path.getAt(index),
          content: selectionRing(),
          zIndex: 5,
        });
      }
      if (o.onSelect) o.onSelect(index);
    }

    function restore() {
      guard = true;
      try {
        path.clear();
        lastValid.forEach(([lng, lat]) => path.push(new maps.LatLng(lat, lng)));
      } finally {
        guard = false;
      }
      shape.setOptions(normal);
      setSelected(null);
      if (o.onChange) o.onChange(lastValid.map((point) => point.slice()));
    }

    // Se valida en cada cambio. Un contorno inválido se pinta en rojo y, si sigue así, vuelve al último válido.
    function validateNow() {
      if (guard) return;
      const ring = geo.fromPath(path);
      const verdict = verdictFor(ring, o.validate);
      window.clearTimeout(timer);
      if (verdict === true) {
        lastValid = ring;
        shape.setOptions(normal);
        if (o.onChange) o.onChange(ring.map((point) => point.slice()));
        return;
      }
      shape.setOptions(INVALID_STYLE);
      reject(verdict);
      timer = window.setTimeout(restore, RESTORE_DELAY_MS);
    }

    function removeVertex(index) {
      if (path.getLength() <= minVertices) {
        reject(MIN_POINTS_TEXT);
        return false;
      }
      const ring = geo.fromPath(path);
      ring.splice(index, 1);
      const verdict = verdictFor(ring, o.validate);
      if (verdict !== true) {
        reject(verdict);
        return false;
      }
      setSelected(null);
      path.removeAt(index);
      return true;
    }

    function addAtEdge(edge) {
      const first = path.getAt(edge);
      const second = path.getAt((edge + 1) % path.getLength());
      path.insertAt(
        edge + 1,
        new maps.LatLng((first.lat() + second.lat()) / 2, (first.lng() + second.lng()) / 2),
      );
      setSelected(edge + 1);
    }

    const describe = (event) => 'vertex:' + event.vertex + ' edge:' + event.edge;
    const listeners = [
      path.addListener('set_at', (index) => {
        emit('set_at', index);
        if (index === selected && ringMarker) ringMarker.position = path.getAt(index);
        validateNow();
      }),
      path.addListener('insert_at', (index) => {
        emit('insert_at', index);
        validateNow();
      }),
      path.addListener('remove_at', (index) => {
        emit('remove_at', index);
        validateNow();
      }),
      shape.addListener('click', (event) => {
        emit('click', describe(event));
        if (event.vertex !== undefined) setSelected(event.vertex);
        else if (event.edge !== undefined) addAtEdge(event.edge);
        else setSelected(null);
      }),
      shape.addListener('contextmenu', (event) => {
        emit('contextmenu', describe(event));
        if (event.vertex !== undefined) removeVertex(event.vertex);
      }),
      shape.addListener('mouseup', (event) => emit('mouseup', describe(event))),
    ];

    shape.setOptions({ editable: true, draggable: false });

    return {
      mode: 'native',
      get selected() {
        return selected;
      },
      deleteSelected() {
        return selected === null ? false : removeVertex(selected);
      },
      stop() {
        listeners.forEach((listener) => maps.event.removeListener(listener));
        window.clearTimeout(timer);
        setSelected(null);
        shape.setOptions(Object.assign({ editable: false }, normal));
      },
    };
  }

  // ---------- Editor propio: tiradores grandes arrastrables (+ en cada punto medio para agregar) ----------

  function editCustom(ctrl, shape, o) {
    const { AdvancedMarkerElement } = ctrl;
    const minVertices = o.minVertices || MIN_VERTICES;
    const normal = { strokeColor: shape.get('strokeColor'), fillColor: shape.get('fillColor') };
    const emit = (name, detail) => {
      if (o.onEvent) o.onEvent(name, detail);
    };
    const reject = (message) => {
      if (o.onReject) o.onReject(message);
    };
    const copy = (ring) => ring.map((point) => point.slice());
    let ring = geo.fromPath(shape.getPath());
    let lastValid = copy(ring);
    let selected = null;
    let vertexMarkers = [];
    let midMarkers = [];

    const position = (point) => ({ lat: point[1], lng: point[0] });
    const midOf = (index) => geo.midpoint(ring[index], ring[(index + 1) % ring.length]);
    const redraw = () => shape.setPath(geo.toLatLngs(ring));

    function commit() {
      lastValid = copy(ring);
      shape.setOptions(normal);
      if (o.onChange) o.onChange(copy(lastValid));
    }

    function select(index) {
      selected = index;
      vertexMarkers.forEach((marker, i) => paintHandle(marker.content, i === selected));
      if (o.onSelect) o.onSelect(index);
    }

    function removeVertex(index) {
      if (ring.length <= minVertices) {
        reject(MIN_POINTS_TEXT);
        return false;
      }
      const next = copy(ring);
      next.splice(index, 1);
      const verdict = verdictFor(next, o.validate);
      if (verdict !== true) {
        reject(verdict);
        return false;
      }
      ring = next;
      selected = null;
      redraw();
      commit();
      render();
      if (o.onSelect) o.onSelect(null);
      return true;
    }

    function addAfter(index) {
      ring.splice(index + 1, 0, midOf(index));
      selected = index + 1;
      redraw();
      commit();
      render();
      if (o.onSelect) o.onSelect(selected);
    }

    function clearHandles() {
      vertexMarkers.concat(midMarkers).forEach((marker) => {
        marker.map = null;
      });
      vertexMarkers = [];
      midMarkers = [];
    }

    function render() {
      clearHandles();
      ring.forEach((point, index) => {
        const marker = new AdvancedMarkerElement({
          map: ctrl.map,
          position: position(point),
          content: handleElement('vertex', index === selected),
          gmpDraggable: true,
          gmpClickable: true,
          zIndex: 20,
          title: 'Punto ' + (index + 1),
        });
        marker.addListener('dragstart', () => {
          selected = index;
          emit('dragstart', 'vertex:' + index);
          if (o.onSelect) o.onSelect(index);
        });
        marker.addListener('drag', (event) => {
          if (!event.latLng) return;
          ring[index] = [event.latLng.lng(), event.latLng.lat()];
          redraw();
          midMarkers.forEach((mid, i) => {
            mid.position = position(midOf(i));
          });
          const verdict = verdictFor(ring, o.validate);
          shape.setOptions(verdict === true ? normal : INVALID_STYLE);
          if (verdict !== true) reject(verdict);
        });
        marker.addListener('dragend', () => {
          emit('dragend', 'vertex:' + index);
          if (verdictFor(ring, o.validate) === true) {
            commit();
          } else {
            ring = copy(lastValid);
            redraw();
            shape.setOptions(normal);
            render();
          }
        });
        marker.addListener('click', () => {
          emit('click', 'vertex:' + index);
          select(index);
        });
        marker.content.addEventListener('contextmenu', (event) => {
          event.preventDefault();
          emit('contextmenu', 'vertex:' + index);
          removeVertex(index);
        });
        vertexMarkers.push(marker);
      });
      ring.forEach((point, index) => {
        const marker = new AdvancedMarkerElement({
          map: ctrl.map,
          position: position(midOf(index)),
          content: handleElement('mid', false),
          gmpClickable: true,
          zIndex: 10,
          title: 'Agregar un punto aquí',
        });
        marker.addListener('click', () => {
          emit('click', 'mid:' + index);
          addAfter(index);
        });
        midMarkers.push(marker);
      });
    }

    shape.setOptions({ editable: false, draggable: false });
    render();

    return {
      mode: 'custom',
      get selected() {
        return selected;
      },
      deleteSelected() {
        return selected === null ? false : removeVertex(selected);
      },
      stop() {
        clearHandles();
        shape.setOptions(normal);
      },
    };
  }

  // ---------- Mapa ----------

  async function create(element, config, options) {
    const cfg = config || {};
    const opts = options || {};
    const fail = (reason) => {
      let text = UNAVAILABLE_TEXT;
      if (cfg.debug) text += ' [Detalle para administradores: ' + adminDetail(reason) + ']';
      showNotice(element, text, 'error');
      if (opts.onUnavailable) opts.onUnavailable(reason);
      const error = new Error('agro-gmap: ' + reason);
      error.reason = reason;
      return error;
    };

    if (!cfg.google_maps_key) throw fail('no-key');
    if (authFailed) throw fail('auth');
    let maps;
    let libraries;
    try {
      maps = await loadGoogle(cfg.google_maps_key);
      libraries = await Promise.all([maps.importLibrary('maps'), maps.importLibrary('marker')]);
    } catch (error) {
      throw fail(error && error.message === 'timeout' ? 'timeout' : 'network');
    }
    if (authFailed) throw fail('auth');

    const GoogleMap = libraries[0].Map;
    const Polygon = libraries[0].Polygon;
    const AdvancedMarkerElement = libraries[1].AdvancedMarkerElement;
    const usingDemoMapId = !cfg.google_map_id;
    if (usingDemoMapId) {
      console.warn('AgroAsesor: el mapa usa DEMO_MAP_ID (solo pruebas). Define GOOGLE_MAPS_MAP_ID con tu Map ID de Google Cloud.');
    }
    const gmap = new GoogleMap(element, {
      center: DEFAULT_CENTER,
      zoom: 12,
      mapId: cfg.google_map_id || DEMO_MAP_ID,
      mapTypeId: GOOGLE_TYPES.normal,
      disableDefaultUI: true,
      zoomControl: true,
      gestureHandling: 'greedy',
      clickableIcons: false,
      maxZoom: MAX_ZOOM,
    });
    authListeners.add(() => fail('auth'));

    const ctrl = {
      map: gmap,
      maps,
      AdvancedMarkerElement,
      usingDemoMapId,
      view: 'normal',
      setView(view) {
        ctrl.view = view === 'satellite' ? 'satellite' : 'normal';
        gmap.setMapTypeId(GOOGLE_TYPES[ctrl.view]);
      },
      addPolygon(ring, style) {
        return new Polygon(
          Object.assign(
            {
              map: gmap,
              paths: geo.toLatLngs(ring),
              strokeColor: '#0f7a47',
              strokeOpacity: 1,
              strokeWeight: 3,
              fillColor: '#34a36b',
              fillOpacity: 0.3,
              clickable: true,
            },
            style || {},
          ),
        );
      },
      addMarker(lngLat, markerOptions) {
        const opt = markerOptions || {};
        const settings = {
          map: gmap,
          position: { lat: lngLat[1], lng: lngLat[0] },
          title: opt.title || '',
          gmpDraggable: Boolean(opt.draggable),
        };
        if (opt.content) settings.content = opt.content;
        return new AdvancedMarkerElement(settings);
      },
      // Encuadra uno o varios anillos [[lng, lat], …] con margen, sin acercarse más de `maxZoom`.
      fit(rings, fitOptions) {
        const opt = fitOptions || {};
        const bounds = new maps.LatLngBounds();
        rings.forEach((ring) => ring.forEach(([lng, lat]) => bounds.extend({ lat, lng })));
        if (bounds.isEmpty()) return;
        const release = () => gmap.setOptions({ maxZoom: MAX_ZOOM });
        gmap.setOptions({ maxZoom: opt.maxZoom || MAX_ZOOM });
        maps.event.addListenerOnce(gmap, 'idle', release);
        window.setTimeout(release, 2000);
        gmap.fitBounds(bounds, opt.padding === undefined ? 40 : opt.padding);
      },
      edit(shape, editOptions) {
        const opt = editOptions || {};
        return (opt.mode === 'custom' ? editCustom : editNative)(ctrl, shape, opt);
      },
    };
    return ctrl;
  }

  window.AgroGMap = { create, geo, DEMO_MAP_ID };
})();
