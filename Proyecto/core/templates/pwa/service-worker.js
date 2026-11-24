/* AgroAsesor · service worker (lo genera core/pwa.py; la versión cambia en cada despliegue).
 *
 * Qué guarda:   solo archivos estáticos públicos (CSS, JS, imágenes), tipografías e íconos de CDN con versión fija
 *               y la pantalla «sin conexión». Nada de la cuenta de nadie.
 * Qué NO toca:  páginas HTML de la cuenta, /api/, /admin/, inicio de sesión con Google, formularios (POST),
 *               cookies ni cabeceras de autorización. Sus peticiones pasan directo a la red, como sin service worker.
 * Sin conexión: si una página no carga, muestra la pantalla «sin conexión». No promete que los módulos funcionen offline.
 */
'use strict';

const VERSION = '{{ version|escapejs }}';
const CACHE_ESTATICO = 'agro-estatico-' + VERSION; // se borra solo al publicar una versión nueva
const CACHE_TERCEROS = 'agro-terceros-v1'; // tipografías e íconos de CDN (versión fija en la URL)
const OFFLINE_URL = '{{ offline_url|escapejs }}';
const PREFIJO_ESTATICO = '{{ static_url|escapejs }}';
const ESPERA_NAVEGACION_MS = 30000; // red muy lenta o colgada: muestra «sin conexión» con botón para reintentar
const MAX_TERCEROS = 60;

// Rutas de la misma web que el service worker nunca intercepta (ni siquiera para mostrar «sin conexión»):
// inicio y cierre de sesión, Google, recuperación de contraseña, administración y API. Las define core/pwa.py.
const SIN_INTERCEPTAR = [{% for ruta in sin_interceptar %}'{{ ruta|escapejs }}'{% if not forloop.last %}, {% endif %}{% endfor %}];

// Únicos orígenes externos que se guardan: tipografía Outfit y librerías de íconos/mapa con versión fija.
function esTerceroPermitido(url) {
  if (url.protocol !== 'https:') return false;
  if (url.hostname === 'fonts.googleapis.com') return url.pathname.startsWith('/css2');
  if (url.hostname === 'fonts.gstatic.com') return true;
  if (url.hostname === 'unpkg.com') {
    return /^\/(lucide@\d|leaflet@\d|@geoman-io\/leaflet-geoman-free@\d)/.test(url.pathname);
  }
  return false;
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_ESTATICO)
      .then((cache) => cache.add(new Request(OFFLINE_URL, { cache: 'reload' })))
      .then(() => self.skipWaiting())
  );
});

// Borra las copias de versiones anteriores. Se repite en la primera petición de cada arranque del service worker:
// si la versión vieja alcanza a recrear su caché mientras se actualizaba, la limpieza se completa en la siguiente visita.
async function limpiarCachesViejas() {
  const nombres = await caches.keys();
  await Promise.all(
    nombres.filter((n) => n.startsWith('agro-estatico-') && n !== CACHE_ESTATICO).map((n) => caches.delete(n))
  );
}

self.addEventListener('activate', (event) => {
  event.waitUntil(limpiarCachesViejas().then(() => self.clients.claim()));
});

let limpiezaHecha = false;

self.addEventListener('fetch', (event) => {
  if (!limpiezaHecha) {
    limpiezaHecha = true;
    event.waitUntil(limpiarCachesViejas().catch(() => undefined));
  }
  const peticion = event.request;
  if (peticion.method !== 'GET' || peticion.headers.has('range')) return;
  const url = new URL(peticion.url);

  if (peticion.mode === 'navigate') {
    if (url.origin !== self.location.origin) return;
    if (SIN_INTERCEPTAR.some((prefijo) => url.pathname.startsWith(prefijo))) return;
    event.respondWith(navegar(peticion));
    return;
  }
  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith(PREFIJO_ESTATICO)) event.respondWith(estaticoYRenovar(event));
    return; // todo lo demás (JSON, /api/, etc.) va directo a la red
  }
  if (esTerceroPermitido(url)) event.respondWith(terceroYRenovar(event));
});

// Páginas: siempre desde la red (datos al día). Solo si no hay conexión, la pantalla «sin conexión».
async function navegar(peticion) {
  const control = new AbortController();
  const temporizador = setTimeout(() => control.abort(), ESPERA_NAVEGACION_MS);
  try {
    return await fetch(peticion, { signal: control.signal });
  } catch (error) {
    return respuestaSinConexion();
  } finally {
    clearTimeout(temporizador);
  }
}

async function respuestaSinConexion() {
  const cache = await caches.open(CACHE_ESTATICO);
  const guardada = await cache.match(OFFLINE_URL);
  if (guardada) return guardada;
  return new Response(
    '<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
      '<title>Sin conexión · AgroAsesor</title><body style="font-family:sans-serif;padding:32px"><h1>Sin conexión</h1>' +
      '<p>No pudimos cargar AgroAsesor. Revisa tu conexión e inténtalo de nuevo.</p></body></html>',
    { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
  );
}

function sePuedeGuardar(respuesta) {
  const control = (respuesta.headers.get('Cache-Control') || '').toLowerCase();
  return respuesta.ok && !control.includes('no-store') && !control.includes('private');
}

// Archivos propios (/static/): se sirven al instante desde la copia y se renuevan en segundo plano.
// Las direcciones llevan ?v=N, así que una versión nueva del archivo es otra entrada.
async function estaticoYRenovar(event) {
  const cache = await caches.open(CACHE_ESTATICO);
  const guardada = await cache.match(event.request);
  const desdeRed = fetch(event.request).then((respuesta) => {
    if (respuesta.type === 'basic' && sePuedeGuardar(respuesta)) cache.put(event.request, respuesta.clone());
    return respuesta;
  });
  if (guardada) {
    event.waitUntil(desdeRed.catch(() => undefined));
    return guardada;
  }
  return desdeRed;
}

// Tipografías e íconos de CDN: se piden con CORS y sin credenciales, y solo se guardan respuestas correctas.
async function terceroYRenovar(event) {
  const cache = await caches.open(CACHE_TERCEROS);
  const guardada = await cache.match(event.request.url);
  const desdeRed = fetch(event.request.url, { mode: 'cors', credentials: 'omit' }).then(async (respuesta) => {
    if (sePuedeGuardar(respuesta)) {
      await cache.put(event.request.url, respuesta.clone());
      limitar(cache, MAX_TERCEROS);
    }
    return respuesta;
  });
  if (guardada) {
    event.waitUntil(desdeRed.catch(() => undefined));
    return guardada;
  }
  return desdeRed;
}

async function limitar(cache, maximo) {
  const claves = await cache.keys();
  if (claves.length > maximo) await Promise.all(claves.slice(0, claves.length - maximo).map((c) => cache.delete(c)));
}
