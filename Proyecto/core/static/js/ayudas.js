/* Consejos de ayuda plegables (partials/ayuda.html).
 * «Entendido» o plegar el consejo lo guarda en el servidor sin recargar: la próxima vez se ve solo la línea
 * «¿Cómo…?», que se puede abrir cuando se quiera. Abrirlo de nuevo no cambia lo guardado. */
(function () {
  'use strict';

  function guardar(details) {
    if (details.dataset.guardado) return;
    const form = details.querySelector('.ayuda-form');
    if (!form) return;
    details.dataset.guardado = '1';
    fetch(form.action, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      body: new FormData(form),
    }).catch(() => {
      delete details.dataset.guardado; // sin conexión: se intentará de nuevo la próxima vez
    });
  }

  document.addEventListener('submit', (event) => {
    const form = event.target.closest('.ayuda-form');
    if (!form) return;
    event.preventDefault();
    const details = form.closest('details.ayuda');
    guardar(details);
    details.open = false;
  });

  document.querySelectorAll('details.ayuda').forEach((details) => {
    if (!details.open) details.dataset.guardado = '1'; // ya estaba plegado: no hay nada que guardar
    details.addEventListener('toggle', () => {
      if (!details.open) guardar(details);
    });
  });
})();
