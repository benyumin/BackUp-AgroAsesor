/* Consejos de ayuda plegables (partials/ayuda.html).
 * Llegan plegados (una línea «¿Cómo…?»). La primera vez que la persona entra a una sección el consejo se abre;
 * «Entendido» o plegarlo lo recuerda en este navegador (localStorage, por usuario) y desde entonces queda plegado,
 * aunque se puede abrir cuando se quiera. No se guarda nada en el servidor. En «Mi perfil» se pueden volver a mostrar. */
(function () {
  'use strict';

  var usuario = '';
  try {
    var dato = document.getElementById('agro-user-id');
    usuario = dato ? String(JSON.parse(dato.textContent)) : '';
  } catch (e) {
    usuario = '';
  }
  var CLAVE = 'agroasesor.ayudas.plegadas.' + usuario;

  function leer() {
    try {
      var lista = JSON.parse(window.localStorage.getItem(CLAVE) || '[]');
      return Array.isArray(lista) ? lista : [];
    } catch (e) {
      return [];
    }
  }

  function guardar(lista) {
    try {
      window.localStorage.setItem(CLAVE, JSON.stringify(lista));
    } catch (e) {
      /* sin almacenamiento: el consejo se abrirá de nuevo la próxima vez */
    }
  }

  function recordar(clave) {
    var lista = leer();
    if (lista.indexOf(clave) === -1) {
      lista.push(clave);
      guardar(lista);
    }
  }

  var plegadas = leer();
  document.querySelectorAll('details.ayuda').forEach(function (details) {
    var clave = details.dataset.ayuda;
    if (plegadas.indexOf(clave) === -1) details.open = true; // primera vez: se muestra abierto
    details.addEventListener('toggle', function () {
      if (!details.open) recordar(clave);
    });
  });

  document.addEventListener('click', function (event) {
    var boton = event.target.closest('.ayuda-cerrar');
    var details = boton && boton.closest('details.ayuda');
    if (details) details.open = false; // el evento «toggle» lo recuerda
  });

  // «Mi perfil»: cuántos consejos plegó la persona y cómo volver a mostrarlos.
  var estado = document.querySelector('[data-ayudas-estado]');
  var mostrar = document.querySelector('[data-ayudas-mostrar]');

  function pintarPerfil() {
    if (!estado || !mostrar) return;
    var n = leer().length;
    estado.textContent = n
      ? 'Plegaste ' + n + ' consejo' + (n === 1 ? '' : 's') + ' en este navegador'
      : 'Se muestran en cada sección';
    mostrar.style.display = n ? '' : 'none';
  }

  if (mostrar) {
    mostrar.addEventListener('click', function () {
      guardar([]);
      pintarPerfil();
    });
  }
  pintarPerfil();
})();
