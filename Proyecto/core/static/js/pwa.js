// Aplicación instalable (PWA): registra el service worker, muestra el botón «Instalar» del menú de perfil, un aviso
// discreto en pantallas chicas, la hoja de pasos para iPhone y el aviso de versión nueva. Ver core/pwa.py.
// No guarda datos de la cuenta: solo la fecha en que se cerró el aviso (localStorage).
(function () {
  const script = document.currentScript;
  const urlServiceWorker = script?.dataset.sw;
  const urlIcono = script?.dataset.icono;
  const CLAVE_AVISO = 'agro-pwa-aviso-cerrado';
  const DIAS_SIN_AVISO = 30;

  const instalada = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const esIphone = /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let eventoInstalar = null;

  function avisoCerradoHacePoco() {
    try {
      const fecha = Number(localStorage.getItem(CLAVE_AVISO));
      return Boolean(fecha) && Date.now() - fecha < DIAS_SIN_AVISO * 86400000;
    } catch (error) {
      return false;
    }
  }

  function recordarAvisoCerrado() {
    try {
      localStorage.setItem(CLAVE_AVISO, String(Date.now()));
    } catch (error) {
      // Sin almacenamiento (modo privado): el aviso vuelve a aparecer en la próxima visita.
    }
  }

  function crear(html) {
    const plantilla = document.createElement('template');
    plantilla.innerHTML = html.trim();
    return plantilla.content.firstElementChild;
  }

  function botonesInstalar() {
    return document.querySelectorAll('[data-pwa-install]');
  }

  function mostrarBotonesInstalar(visible) {
    botonesInstalar().forEach((boton) => {
      boton.hidden = !visible;
    });
  }

  // ── Hoja de pasos para iPhone: Safari no ofrece botón de instalar, se agrega desde «Compartir». ──
  let hoja = null;

  function abrirHojaIphone() {
    if (!hoja) {
      hoja = crear(`
        <div class="pwa-hoja-fondo" hidden>
          <div class="pwa-hoja" role="dialog" aria-modal="true" aria-labelledby="pwa-hoja-titulo">
            <h2 id="pwa-hoja-titulo">Instalar AgroAsesor</h2>
            <p>Agrégala a tu pantalla de inicio para abrirla como una aplicación.</p>
            <ol class="pwa-pasos">
              <li><span>Toca <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-label="Compartir"><path d="M12 3v12"/><path d="m8 7 4-4 4 4"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg> Compartir en la barra de Safari.</span></li>
              <li><span>Elige «Agregar a inicio».</span></li>
              <li><span>Toca «Agregar».</span></li>
            </ol>
            <button class="pwa-boton" type="button" data-pwa-cerrar-hoja>Entendido</button>
          </div>
        </div>`);
      document.body.appendChild(hoja);
      hoja.addEventListener('click', (evento) => {
        if (evento.target === hoja || evento.target.closest('[data-pwa-cerrar-hoja]')) cerrarHojaIphone();
      });
      document.addEventListener('keydown', (evento) => {
        if (evento.key === 'Escape' && hoja && !hoja.hidden) cerrarHojaIphone();
      });
    }
    hoja.hidden = false;
    hoja.querySelector('[data-pwa-cerrar-hoja]').focus();
  }

  function cerrarHojaIphone() {
    if (hoja) hoja.hidden = true;
  }

  async function instalar() {
    if (eventoInstalar) {
      eventoInstalar.prompt();
      await eventoInstalar.userChoice.catch(() => null);
      eventoInstalar = null;
      mostrarBotonesInstalar(false);
      ocultarAviso();
    } else if (esIphone) {
      abrirHojaIphone();
    }
  }

  // ── Aviso inferior (solo en pantallas chicas; el CSS lo oculta en escritorio). ──
  let aviso = null;

  function mostrarAviso() {
    if (instalada || avisoCerradoHacePoco() || aviso) return;
    const icono = urlIcono ? `<img src="${urlIcono}" alt="" width="40" height="40">` : '';
    aviso = crear(`
      <div class="pwa-aviso" role="region" aria-label="Instalar AgroAsesor">
        <span class="pwa-aviso-icono" aria-hidden="true">${icono}</span>
        <div class="pwa-aviso-texto">
          <strong>Instala AgroAsesor</strong>
          <span>Ábrela desde tu pantalla de inicio.</span>
        </div>
        <button class="pwa-boton" type="button" data-pwa-aviso-instalar>Instalar</button>
        <button class="pwa-cerrar" type="button" aria-label="Cerrar aviso" data-pwa-aviso-cerrar>&times;</button>
      </div>`);
    document.body.appendChild(aviso);
    aviso.querySelector('[data-pwa-aviso-instalar]').addEventListener('click', instalar);
    aviso.querySelector('[data-pwa-aviso-cerrar]').addEventListener('click', () => {
      recordarAvisoCerrado();
      ocultarAviso();
    });
  }

  function ocultarAviso() {
    aviso?.remove();
    aviso = null;
  }

  // ── Aviso de versión nueva: el service worker nuevo toma el control y basta con recargar. ──
  function mostrarActualizacion() {
    if (document.querySelector('.pwa-actualizacion')) return;
    const panel = crear(`
      <div class="pwa-actualizacion" role="status">
        <div class="pwa-aviso-texto">
          <strong>Hay una versión nueva</strong>
          <span>Recarga para usar la última versión.</span>
        </div>
        <button class="pwa-boton" type="button">Actualizar</button>
      </div>`);
    panel.querySelector('button').addEventListener('click', () => window.location.reload());
    document.body.appendChild(panel);
  }

  botonesInstalar().forEach((boton) => boton.addEventListener('click', instalar));

  if (!instalada) {
    window.addEventListener('beforeinstallprompt', (evento) => {
      evento.preventDefault();
      eventoInstalar = evento;
      mostrarBotonesInstalar(true);
      mostrarAviso();
    });
    if (esIphone) {
      mostrarBotonesInstalar(true);
      mostrarAviso();
    }
  }

  window.addEventListener('appinstalled', () => {
    eventoInstalar = null;
    mostrarBotonesInstalar(false);
    ocultarAviso();
  });

  if ('serviceWorker' in navigator && urlServiceWorker) {
    const habiaControlador = Boolean(navigator.serviceWorker.controller);
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (habiaControlador) mostrarActualizacion();
    });
    window.addEventListener('load', () => {
      navigator.serviceWorker.register(urlServiceWorker, { scope: '/' }).catch(() => {
        // Sin service worker la página sigue funcionando igual, solo que no se puede instalar.
      });
    });
  }
})();
