/* Eliminar o archivar un predio o un potrero desde el menú «⋯», con una ventana de confirmación.
 *
 * Cada opción «Eliminar…» es un enlace normal a la página de confirmación (funciona sin JavaScript). Aquí se
 * intercepta para mostrar la misma información en una ventana: qué registro es, si se puede eliminar y, si tiene
 * información asociada (actividades, cálculos, temporadas, potreros), por qué no y la opción de archivarlo.
 * El servidor vuelve a revisar todo antes de actuar (core/registros.py).
 */
(function () {
  'use strict';

  let dialog = null;

  function token() {
    const input = document.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (key === 'text') node.textContent = value;
      else node.setAttribute(key, value);
    });
    (children || []).forEach((child) => child && node.append(child));
    return node;
  }

  function ensureDialog() {
    if (dialog) return dialog;
    dialog = el('dialog', { class: 'agro-dialog', 'aria-labelledby': 'registro-dialog-title' });
    // Clic fuera de la ventana: cierra (como «Cancelar»).
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
    document.body.append(dialog);
    return dialog;
  }

  function boton(texto, clase, accion) {
    const button = el('button', { type: 'button', class: clase, text: texto });
    if (accion) button.dataset.accion = accion;
    return button;
  }

  function contenido(info) {
    const tipo = info.tipo === 'predio' ? 'predio' : 'potrero';
    const titulo = info.puede_eliminar
      ? '¿Eliminar el ' + tipo + ' «' + info.nombre + '»?'
      : 'No se puede eliminar «' + info.nombre + '»';
    const cuerpo = [
      el('h2', { id: 'registro-dialog-title', class: 'agro-dialog-title', text: titulo }),
      el('p', { class: 'agro-dialog-record' }, [
        el('span', { class: 'agro-dialog-kicker', text: tipo === 'predio' ? 'Predio' : 'Potrero' }),
        el('strong', { text: info.detalle }),
      ]),
    ];
    if (info.puede_eliminar) {
      cuerpo.push(
        el('p', {
          text:
            'Se eliminará de forma definitiva y no se puede deshacer. El historial del predio se conserva. ' +
            'Si prefieres guardarlo, archívalo: deja de aparecer y puedes restaurarlo cuando quieras.',
        }),
      );
    } else {
      cuerpo.push(
        el('p', { text: 'Tiene información asociada que AgroAsesor protege:' }),
        el('ul', { class: 'agro-dialog-list' }, info.bloqueos.map((texto) => el('li', { text: texto }))),
        el('p', {
          text:
            'Puedes archivarlo: dejará de aparecer en tus listas, mapas, calculadoras y agenda, y toda su ' +
            'información se conserva para restaurarlo desde Mis predios › Archivados.',
        }),
      );
    }
    const error = el('p', { class: 'agro-dialog-error', role: 'alert', hidden: '' });
    const acciones = el('div', { class: 'agro-dialog-actions' }, [
      boton('Cancelar', 'btn-ghost-agro', null),
      info.puede_eliminar
        ? boton('Archivar en su lugar', 'btn-secondary-agro', 'archivar')
        : boton('Archivar ' + tipo, 'btn-primary-agro', 'archivar'),
      info.puede_eliminar ? boton('Eliminar definitivamente', 'btn-danger-agro', 'eliminar') : null,
    ]);
    return { cuerpo: cuerpo.concat([error, acciones]), error };
  }

  async function enviar(url, accion, button, error) {
    const buttons = dialog.querySelectorAll('button');
    buttons.forEach((item) => (item.disabled = true));
    error.hidden = true;
    try {
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': token(), Accept: 'application/json' },
        body: new URLSearchParams({ accion }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.redirect) {
        window.location.assign(data.redirect);
        return;
      }
      error.textContent = data.error || 'No se pudo completar la acción. Intenta de nuevo.';
    } catch (exc) {
      error.textContent = 'No hay conexión con el servidor. Intenta de nuevo.';
    }
    error.hidden = false;
    buttons.forEach((item) => (item.disabled = false));
  }

  async function abrir(link) {
    const url = new URL(link.href, window.location.origin);
    url.searchParams.set('formato', 'json');
    const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error('sin datos');
    const info = await response.json();
    const box = ensureDialog();
    const { cuerpo, error } = contenido(info);
    box.replaceChildren(...cuerpo);
    box.querySelectorAll('button').forEach((button) => {
      button.addEventListener('click', () => {
        if (button.dataset.accion) enviar(info.accion_url, button.dataset.accion, button, error);
        else box.close();
      });
    });
    box.showModal();
    const principal = box.querySelector('.agro-dialog-actions .btn-ghost-agro');
    if (principal) principal.focus(); // el foco parte en «Cancelar»: lo seguro ante una acción destructiva
  }

  document.addEventListener('click', (event) => {
    const link = event.target.closest('[data-registro-eliminar]');
    if (!link || typeof HTMLDialogElement !== 'function') return;
    event.preventDefault();
    abrir(link).catch(() => window.location.assign(link.href)); // sin respuesta: página de confirmación
  });
})();
