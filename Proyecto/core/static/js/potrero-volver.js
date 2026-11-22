/* Registrar potrero · «← Volver al predio» con aviso si hay cambios sin guardar.
 *
 * - Sin cambios: el enlace navega directo al detalle del predio (Django genera su href con {% url %}).
 * - Con cambios: pide confirmación. «Cancelar» deja el formulario y el contorno del mapa tal cual; «Aceptar» sale
 *   al predio sin crear ni modificar nada (es solo una navegación GET).
 *
 * «Cambios» = el estado actual del formulario difiere del estado inicial. Ese estado inicial se toma cuando este script
 * se ejecuta, es decir, después de que predios-map.js terminó de iniciar el mapa y los controles; por eso cargar el
 * mapa no cuenta como modificación. Solo lee el formulario: no guarda nada, no toca el envío y no usa el historial. */
(() => {
  'use strict';

  const link = document.getElementById('back-to-predio');
  const form = document.getElementById('terrain-form');
  if (!link || !form) return;

  const MESSAGE = '¿Quieres salir sin guardar? Los cambios realizados en este potrero se perderán.';

  // Todos los campos con nombre: nombre, cultivo, superficie, ubicación y los ocultos que rellena el mapa (contorno).
  const snapshot = () =>
    JSON.stringify(
      Array.from(new FormData(form).entries())
        .filter(([name]) => name !== 'csrfmiddlewaretoken')
        .map(([name, value]) => [name, String(value).trim()]),
    );
  const initialState = snapshot();

  // Contorno a medio dibujar: aún no cambia ningún campo, pero ya es trabajo de la persona.
  // (Con puntos marcados, «Deshacer» está activo; en «Medir una línea» «Terminar» del perímetro está oculto.)
  function drawingInProgress() {
    const undo = document.getElementById('undo-point');
    const finish = document.getElementById('finish-perimeter');
    return Boolean(undo && finish && !undo.disabled && !finish.hidden);
  }

  // data-unsaved-start: el formulario volvió del servidor con datos enviados que no se guardaron (hubo errores).
  function hasUnsavedChanges() {
    return link.hasAttribute('data-unsaved-start') || drawingInProgress() || snapshot() !== initialState;
  }

  link.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    // Ctrl/Cmd/Mayús/Alt + clic abre otra pestaña o ventana: este formulario sigue abierto, no hay nada que perder.
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (!hasUnsavedChanges()) return;
    if (!window.confirm(MESSAGE)) event.preventDefault();
  });
})();
