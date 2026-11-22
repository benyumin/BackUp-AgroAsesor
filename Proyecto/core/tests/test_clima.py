"""Clima del predio (Open-Meteo): servicio ``core.clima`` y endpoint ``/api/predios/<id>/clima/``.

Ninguna prueba sale a internet: ``core.clima._abrir`` (la única función que lo hace) se sustituye siempre por una
respuesta simulada. Tampoco escriben nada fuera de la base de pruebas de Django (usa SQLite temporal, nunca Supabase).
"""

import copy
import http.client
import json
import re
import socket
import urllib.error
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase

from core import clima, clima_compartido

RAIZ = Path(__file__).resolve().parent.parent.parent
CSS = RAIZ / 'core' / 'static' / 'css' / 'clima.css'
JS = RAIZ / 'core' / 'static' / 'js' / 'predio-clima.js'

RESPUESTA = {
    'latitude': -33.7,
    'longitude': -71.2,
    'generationtime_ms': 0.31,
    'utc_offset_seconds': -10800,
    'timezone': 'America/Santiago',
    'timezone_abbreviation': 'GMT-3',
    'elevation': 120.0,
    'current_units': {
        'time': 'iso8601', 'interval': 'seconds', 'temperature_2m': '°C', 'relative_humidity_2m': '%',
        'wind_speed_10m': 'km/h', 'precipitation': 'mm', 'weather_code': 'wmo code',
    },
    'current': {
        'time': '2026-11-21T20:45', 'interval': 900, 'temperature_2m': 18.24, 'relative_humidity_2m': 55,
        'wind_speed_10m': 12.3, 'precipitation': 0.0, 'weather_code': 2,
    },
    'daily_units': {
        'time': 'iso8601', 'weather_code': 'wmo code', 'temperature_2m_max': '°C', 'temperature_2m_min': '°C',
        'precipitation_probability_max': '%', 'precipitation_sum': 'mm', 'wind_speed_10m_max': 'km/h',
    },
    'daily': {
        'time': ['2026-11-21', '2026-11-22', '2026-11-23', '2026-11-24', '2026-11-25'],
        'weather_code': [2, 61, 63, 3, 0],
        'temperature_2m_max': [24.1, 21.0, 19.4, 22.2, 26.8],
        'temperature_2m_min': [9.5, 10.2, 11.0, 8.7, 7.9],
        'precipitation_probability_max': [5, 60, 85, 20, 0],
        'precipitation_sum': [0.0, 2.4, 9.8, 0.1, 0.0],
        'wind_speed_10m_max': [18.2, 25.0, 31.4, 15.0, 12.1],
    },
}

CUADRADO = {
    'type': 'Feature', 'properties': {},
    'geometry': {'type': 'Polygon', 'coordinates': [[
        [-71.21, -33.71], [-71.20, -33.71], [-71.20, -33.70], [-71.21, -33.70], [-71.21, -33.71],
    ]]},
}


def en_c(x, y, escala=0.001, base=(-71.2, -33.7)):
    return [round(base[0] + x * escala, 7), round(base[1] + y * escala, 7)]


# Predio en forma de «C»: su centroide cae en el hueco, fuera del polígono.
FORMA_C = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 8), (10, 8), (10, 10), (0, 10)]
C_GEOJSON = {
    'type': 'Feature', 'properties': {},
    'geometry': {'type': 'Polygon', 'coordinates': [[*(en_c(x, y) for x, y in FORMA_C), en_c(*FORMA_C[0])]]},
}


def cuerpo(datos):
    return 200, json.dumps(datos).encode('utf-8')


class ClimaServicioTests(SimpleTestCase):
    """Lógica pura de ``core.clima``: sin base de datos y sin red."""

    def test_url_usa_https_coordenadas_redondeadas_y_unidades_explicitas(self):
        url = clima.construir_url(-33.70512345, -71.2049876)
        self.assertTrue(url.startswith('https://api.open-meteo.com/v1/forecast?'))
        for texto in ('latitude=-33.705', 'longitude=-71.205', 'timezone=auto', 'forecast_days=5',
                      'temperature_unit=celsius', 'wind_speed_unit=kmh', 'precipitation_unit=mm',
                      'current=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,weather_code',
                      'precipitation_probability_max'):
            self.assertIn(texto, url)
        self.assertNotRegex(url, r'(?i)apikey|api_key|token')  # el servicio gratuito no usa clave

    def test_centro_de_un_poligono_es_su_centroide_y_no_toca_la_geometria(self):
        original = copy.deepcopy(CUADRADO)
        centro = clima.centro_representativo(CUADRADO, None, None)
        self.assertEqual(centro['origen'], 'poligono')
        self.assertAlmostEqual(centro['latitud'], -33.705, places=6)
        self.assertAlmostEqual(centro['longitud'], -71.205, places=6)
        self.assertEqual(CUADRADO, original)

    def test_el_poligono_manda_sobre_las_coordenadas_guardadas(self):
        centro = clima.centro_representativo(CUADRADO, -10.0, -50.0)
        self.assertEqual(centro['origen'], 'poligono')
        self.assertAlmostEqual(centro['latitud'], -33.705, places=6)

    def test_poligono_concavo_devuelve_un_punto_dentro_del_predio(self):
        from core.geometry import coordinates_from_geojson, point_in_polygon

        centro = clima.centro_representativo(C_GEOJSON, None, None)
        puntos = [list(p) for p in coordinates_from_geojson(C_GEOJSON)]
        self.assertEqual(centro['origen'], 'poligono')
        self.assertIn('interior', centro['metodo'])
        self.assertTrue(point_in_polygon((centro['longitud'], centro['latitud']), puntos))

    def test_el_sentido_de_los_vertices_no_cambia_el_centro(self):
        horario = copy.deepcopy(CUADRADO)
        anillo = horario['geometry']['coordinates'][0]
        horario['geometry']['coordinates'][0] = anillo[::-1]
        a, b = clima.centro_representativo(CUADRADO, None, None), clima.centro_representativo(horario, None, None)
        self.assertAlmostEqual(a['latitud'], b['latitud'], places=9)
        self.assertAlmostEqual(a['longitud'], b['longitud'], places=9)

    def test_sin_poligono_usa_coordenadas_guardadas_y_sin_nada_no_inventa(self):
        guardadas = clima.centro_representativo(None, -33.5, -70.6)
        self.assertEqual(guardadas['origen'], 'coordenadas_guardadas')
        self.assertEqual((guardadas['latitud'], guardadas['longitud']), (-33.5, -70.6))
        invalido = {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 1]]]}
        self.assertEqual(clima.centro_representativo(invalido, -33.5, -70.6)['origen'], 'coordenadas_guardadas')
        for geojson, lat, lng in ((None, None, None), (None, -33.5, None), (None, None, -70.6),
                                  (None, 95, -70.6), (None, float('nan'), -70.6), ({}, None, None)):
            self.assertIsNone(clima.centro_representativo(geojson, lat, lng), (geojson, lat, lng))

    def test_normaliza_una_respuesta_valida(self):
        datos = clima.normalizar(copy.deepcopy(RESPUESTA))
        actual = datos['actual']
        self.assertEqual(actual['tipo'], 'estimado_por_modelo')
        self.assertEqual(actual['hora'], '2026-11-21T20:45')
        self.assertEqual(actual['intervalo_min'], 15)
        self.assertEqual((actual['temperatura'], actual['humedad'], actual['viento'], actual['precipitacion']),
                         (18.2, 55, 12.3, 0.0))
        self.assertEqual(actual['descripcion'], 'Parcialmente nublado')
        self.assertEqual(len(datos['pronostico']), 5)
        self.assertTrue(all(dia['tipo'] == 'pronostico' for dia in datos['pronostico']))
        manana = datos['pronostico'][1]
        self.assertEqual((manana['fecha'], manana['temp_max'], manana['temp_min'], manana['prob_precipitacion']),
                         ('2026-11-22', 21.0, 10.2, 60))
        self.assertEqual(manana['descripcion'], 'Lluvia ligera')
        self.assertEqual(datos['zona_horaria'], 'America/Santiago')
        self.assertEqual(datos['unidades'], {
            'temperatura': '°C', 'humedad': '%', 'viento': 'km/h', 'precipitacion': 'mm', 'prob_precipitacion': '%',
        })

    def test_informa_las_unidades_que_entrega_el_proveedor(self):
        datos = copy.deepcopy(RESPUESTA)
        datos['current_units']['temperature_2m'] = '°F'
        self.assertEqual(clima.normalizar(datos)['unidades']['temperatura'], '°F')

    def test_los_valores_dudosos_quedan_en_none_y_nunca_se_reemplazan(self):
        datos = copy.deepcopy(RESPUESTA)
        datos['current'].update({'temperature_2m': None, 'relative_humidity_2m': 150, 'wind_speed_10m': float('nan'),
                                 'precipitation': '3', 'weather_code': 1234})
        datos['daily']['temperature_2m_max'][0] = True
        datos['daily']['precipitation_probability_max'][1] = -5
        datos['daily']['wind_speed_10m_max'] = datos['daily']['wind_speed_10m_max'][:2]  # serie más corta
        resultado = clima.normalizar(datos)
        self.assertIsNone(resultado['actual'])  # ningún valor actual utilizable
        self.assertEqual(len(resultado['pronostico']), 5)
        self.assertIsNone(resultado['pronostico'][0]['temp_max'])
        self.assertIsNone(resultado['pronostico'][1]['prob_precipitacion'])
        self.assertIsNone(resultado['pronostico'][4]['viento_max'])
        self.assertEqual(resultado['pronostico'][4]['temp_max'], 26.8)

    def test_valor_actual_parcial_conserva_solo_lo_que_es_valido(self):
        datos = copy.deepcopy(RESPUESTA)
        datos['current']['relative_humidity_2m'] = None
        actual = clima.normalizar(datos)['actual']
        self.assertIsNone(actual['humedad'])
        self.assertEqual(actual['temperatura'], 18.2)

    def test_respuestas_inutilizables_lanzan_error_en_vez_de_inventar(self):
        casos = {
            'error del proveedor': ({'error': True, 'reason': 'Parámetro inválido'}, 'proveedor_error'),
            'no es un objeto': ([1, 2, 3], 'proveedor_error'),
            'vacía': ({}, 'respuesta_invalida'),
            'sin datos': ({'current': {'time': 'ayer'}, 'daily': {'time': ['x']}}, 'respuesta_invalida'),
        }
        for nombre, (datos, codigo) in casos.items():
            with self.subTest(nombre), self.assertRaises(clima.ClimaError) as ctx:
                clima.normalizar(datos)
            self.assertEqual(ctx.exception.codigo, codigo)

    def test_sin_bloque_actual_aun_sirve_el_pronostico(self):
        datos = copy.deepcopy(RESPUESTA)
        del datos['current']
        resultado = clima.normalizar(datos)
        self.assertIsNone(resultado['actual'])
        self.assertEqual(len(resultado['pronostico']), 5)

    def test_consulta_devuelve_el_json_del_proveedor(self):
        with mock.patch.object(clima, '_abrir', return_value=cuerpo(RESPUESTA)) as abrir:
            self.assertEqual(clima.consultar(-33.7, -71.2), RESPUESTA)
        self.assertEqual(abrir.call_count, 1)

    def test_errores_de_red_y_tiempo_de_espera_se_traducen_sin_inventar_datos(self):
        casos = [
            ('timeout', TimeoutError(), 'tiempo_agotado', 504),
            ('timeout de socket', socket.timeout(), 'tiempo_agotado', 504),
            ('timeout al conectar', urllib.error.URLError(socket.timeout()), 'tiempo_agotado', 504),
            ('sin dns', urllib.error.URLError('Name or service not known'), 'proveedor_no_disponible', 502),
            ('conexión rechazada', ConnectionRefusedError(), 'proveedor_no_disponible', 502),
            ('lectura incompleta', http.client.IncompleteRead(b'x'), 'proveedor_no_disponible', 502),
            ('http 500', urllib.error.HTTPError('u', 500, 'Error', {}, None), 'proveedor_error', 502),
            ('http 400', urllib.error.HTTPError('u', 400, 'Bad', {}, None), 'proveedor_error', 502),
            ('http 429', urllib.error.HTTPError('u', 429, 'Many', {}, None), 'proveedor_limite', 503),
        ]
        for nombre, excepcion, codigo, estado in casos:
            with self.subTest(nombre), mock.patch.object(clima, '_abrir', side_effect=excepcion):
                with self.assertRaises(clima.ClimaError) as ctx:
                    clima.pronostico(-33.7, -71.2)
            self.assertEqual((ctx.exception.codigo, ctx.exception.estado), (codigo, estado))

    def test_cuerpos_invalidos_se_rechazan(self):
        casos = {
            'json roto': (200, b'{no es json'),
            'no es utf-8': (200, b'\xff\xfe\x00'),
            'estado distinto de 200': (204, b''),
            'demasiado grande': (200, b'{"a": "' + b'x' * (clima.MAX_BYTES_RESPUESTA + 1) + b'"}'),
        }
        for nombre, respuesta in casos.items():
            with self.subTest(nombre), mock.patch.object(clima, '_abrir', return_value=respuesta):
                with self.assertRaises(clima.ClimaError) as ctx:
                    clima.consultar(-33.7, -71.2)
            self.assertEqual(ctx.exception.codigo, 'respuesta_invalida')

    def test_pronostico_registra_cuando_se_consulto(self):
        with mock.patch.object(clima, '_abrir', return_value=cuerpo(RESPUESTA)):
            datos = clima.pronostico(-33.7, -71.2)
        self.assertRegex(datos['obtenido_en'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$')

    def test_la_clave_de_cache_depende_de_la_ubicacion(self):
        self.assertEqual(clima.clave_cache(-33.7001, -71.2001), clima.clave_cache(-33.7004, -71.2004))
        self.assertNotEqual(clima.clave_cache(-33.70, -71.20), clima.clave_cache(-33.80, -71.20))

    def test_la_tabla_de_codigos_esta_en_espanol_y_con_iconos(self):
        for codigo, (texto, icono) in clima.CODIGOS_TIEMPO.items():
            self.assertTrue(texto and icono and re.fullmatch(r'[a-z-]+', icono), codigo)


class ClimaEndpointTests(TestCase):
    """Permisos, caché y errores del endpoint. Escriben solo en la base de pruebas de Django."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        from core.models import Predio

        usuarios = get_user_model()
        cls.ana = usuarios.objects.create_user('ana', email='ana@example.com', password='Campo-Seguro-2026!')
        cls.beto = usuarios.objects.create_user('beto', email='beto@example.com', password='Campo-Seguro-2026!')
        cls.predio = Predio.objects.create(
            propietario=cls.ana, nombre='Campo Norte', ubicacion='Melipilla', superficie_ha=1, geojson=CUADRADO
        )
        cls.ajeno = Predio.objects.create(
            propietario=cls.beto, nombre='Campo de Beto', ubicacion='Talagante', superficie_ha=1, geojson=CUADRADO
        )

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        clima_compartido.reiniciar()
        self.addCleanup(cache.clear)
        self.addCleanup(clima_compartido.reiniciar)
        self.client.force_login(self.ana)
        self.url = f'/api/predios/{self.predio.pk}/clima/'

    def pedir(self, url=None, respuesta=None, efecto=None):
        """GET al endpoint con Open-Meteo simulado. Devuelve (respuesta HTTP, mock de ``_abrir``)."""
        with mock.patch.object(
            clima, '_abrir', side_effect=efecto, return_value=None if efecto else (respuesta or cuerpo(RESPUESTA))
        ) as abrir:
            return self.client.get(url or self.url), abrir

    def test_la_ruta_es_la_esperada(self):
        from django.urls import reverse

        self.assertEqual(reverse('predio_clima_api', args=[7]), '/api/predios/7/clima/')

    def test_sin_sesion_responde_401_json_y_no_consulta_al_proveedor(self):
        self.client.logout()
        respuesta, abrir = self.pedir()
        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(respuesta.json()['error']['codigo'], 'no_autenticado')
        self.assertFalse(respuesta.json()['ok'])
        abrir.assert_not_called()

    def test_un_predio_ajeno_inexistente_o_archivado_responde_404_sin_consultar(self):
        from django.utils import timezone

        from core.models import Predio

        archivado = Predio.objects.create(
            propietario=self.ana, nombre='Viejo', ubicacion='X', superficie_ha=1, geojson=CUADRADO,
            archivado_en=timezone.now(),
        )
        for pk in (self.ajeno.pk, 999999, archivado.pk):
            with self.subTest(pk=pk):
                respuesta, abrir = self.pedir(f'/api/predios/{pk}/clima/')
                self.assertEqual(respuesta.status_code, 404)
                self.assertEqual(respuesta.json()['error']['codigo'], 'predio_no_encontrado')
                abrir.assert_not_called()

    def test_el_otro_usuario_ve_solo_lo_suyo(self):
        self.client.force_login(self.beto)
        self.assertEqual(self.pedir()[0].status_code, 404)
        self.assertEqual(self.pedir(f'/api/predios/{self.ajeno.pk}/clima/')[0].status_code, 200)

    def test_solo_acepta_get(self):
        for metodo in ('post', 'put', 'patch', 'delete'):
            with self.subTest(metodo):
                self.assertEqual(getattr(self.client, metodo)(self.url).status_code, 405)

    def test_devuelve_el_pronostico_con_proveedor_unidades_y_hora(self):
        respuesta, abrir = self.pedir()
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertTrue(datos['ok'])
        self.assertEqual(datos['predio'], {'id': self.predio.pk, 'nombre': 'Campo Norte'})
        self.assertEqual(datos['proveedor']['nombre'], 'Open-Meteo')
        self.assertEqual(datos['proveedor']['licencia'], 'CC BY 4.0')
        self.assertEqual(datos['ubicacion']['origen'], 'poligono')
        self.assertAlmostEqual(datos['ubicacion']['latitud'], -33.705, places=4)
        self.assertEqual(datos['actual']['tipo'], 'estimado_por_modelo')
        self.assertEqual(datos['actual']['hora'], '2026-11-21T20:45')
        self.assertEqual(datos['actual']['temperatura'], 18.2)
        self.assertEqual(len(datos['pronostico']), 5)
        self.assertEqual(datos['pronostico'][0]['tipo'], 'pronostico')
        self.assertEqual(datos['unidades']['viento'], 'km/h')
        self.assertEqual(datos['zona_horaria'], 'America/Santiago')
        self.assertFalse(datos['en_cache'])
        self.assertIn('obtenido_en', datos)
        self.assertIn('no-store', respuesta['Cache-Control'])
        url_consultada = abrir.call_args.args[0]
        self.assertIn('api.open-meteo.com', url_consultada)
        # Se consulta la zona (2 decimales ≈ 1,1 km, CLIMA_PRECISION_COORDENADAS): predios cercanos comparten la consulta.
        self.assertIn(f'latitude={clima.redondear(-33.705, 2):.2f}', url_consultada)
        self.assertIn(f'longitude={clima.redondear(-71.205, 2):.2f}', url_consultada)

    def test_no_expone_datos_de_la_cuenta_ni_del_proveedor_que_no_hacen_falta(self):
        texto = json.dumps(self.pedir()[0].json())
        for prohibido in ('ana@example.com', 'propietario', 'password', 'generationtime_ms', 'elevation', 'utc_offset'):
            self.assertNotIn(prohibido, texto)

    def test_una_segunda_consulta_usa_la_cache_y_no_vuelve_a_llamar_al_proveedor(self):
        _, abrir = self.pedir()
        segunda, abrir_2 = self.pedir()
        self.assertEqual(abrir.call_count, 1)
        abrir_2.assert_not_called()
        self.assertTrue(segunda.json()['en_cache'])

    def test_predios_en_el_mismo_punto_comparten_la_cache_pero_cada_usuario_pasa_su_permiso(self):
        self.pedir()
        self.client.force_login(self.beto)
        respuesta, abrir = self.pedir(f'/api/predios/{self.ajeno.pk}/clima/')
        self.assertEqual(respuesta.status_code, 200)
        abrir.assert_not_called()
        self.assertEqual(respuesta.json()['predio']['nombre'], 'Campo de Beto')

    def test_predio_sin_ubicacion_responde_422_y_no_consulta(self):
        from core.models import Predio

        sin = Predio.objects.create(propietario=self.ana, nombre='Sin mapa', ubicacion='?', superficie_ha=1)
        respuesta, abrir = self.pedir(f'/api/predios/{sin.pk}/clima/')
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(respuesta.json()['error']['codigo'], 'sin_ubicacion')
        abrir.assert_not_called()

    def test_con_solo_coordenadas_guardadas_consulta_esa_ubicacion(self):
        from core.models import Predio

        punto = Predio.objects.create(
            propietario=self.ana, nombre='Punto', ubicacion='?', superficie_ha=1, latitud=-34.1, longitud=-70.9
        )
        respuesta, abrir = self.pedir(f'/api/predios/{punto.pk}/clima/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['ubicacion']['origen'], 'coordenadas_guardadas')
        self.assertIn('latitude=-34.10&', abrir.call_args.args[0])

    def test_si_open_meteo_falla_responde_error_json_sin_datos_inventados_y_no_cachea_el_fallo(self):
        casos = [
            (TimeoutError(), 504, 'tiempo_agotado'),
            (urllib.error.URLError('sin red'), 502, 'proveedor_no_disponible'),
            (urllib.error.HTTPError('u', 500, 'Error', {}, None), 502, 'proveedor_error'),
            (urllib.error.HTTPError('u', 502, 'Bad Gateway', {}, None), 502, 'proveedor_error'),
        ]
        for excepcion, estado, codigo in casos:
            with self.subTest(codigo):
                respuesta, _ = self.pedir(efecto=excepcion)
                self.assertEqual(respuesta.status_code, estado)
                datos = respuesta.json()
                self.assertFalse(datos['ok'])
                self.assertEqual(datos['error']['codigo'], codigo)
                for clave in ('actual', 'pronostico', 'unidades'):
                    self.assertNotIn(clave, datos)
        recuperada, abrir = self.pedir()  # el fallo anterior no quedó guardado: se vuelve a intentar
        self.assertEqual(recuperada.status_code, 200)
        self.assertEqual(abrir.call_count, 1)

    def test_un_429_responde_503_con_pausa_y_no_inventa_datos(self):
        # A diferencia de los demás fallos, un 429 SÍ se recuerda: la pausa se comparte (ver test_clima_compartido).
        respuesta, _ = self.pedir(efecto=urllib.error.HTTPError('u', 429, 'Many', {}, None))
        self.assertEqual(respuesta.status_code, 503)
        datos = respuesta.json()
        self.assertFalse(datos['ok'])
        self.assertEqual(datos['error']['codigo'], 'proveedor_limite')
        self.assertGreaterEqual(datos['error']['reintentar_en'], 5)
        self.assertEqual(respuesta['Retry-After'], str(datos['error']['reintentar_en']))
        for clave in ('actual', 'pronostico', 'unidades'):
            self.assertNotIn(clave, datos)

    def test_una_respuesta_invalida_del_proveedor_responde_502(self):
        for malo in ((200, b'<html>en mantencion</html>'), cuerpo({'error': True, 'reason': 'x'}), cuerpo({})):
            with self.subTest(malo=malo[1][:20]):
                respuesta, _ = self.pedir(respuesta=malo)
                self.assertEqual(respuesta.status_code, 502)
                self.assertFalse(respuesta.json()['ok'])

    def test_consultar_el_clima_no_modifica_el_predio(self):
        from core.models import Predio

        antes = Predio.todos.values().get(pk=self.predio.pk)
        self.pedir()
        self.assertEqual(Predio.todos.values().get(pk=self.predio.pk), antes)
        self.assertEqual(Predio.todos.count(), 2)

    def test_mis_predios_sigue_cargando_aunque_open_meteo_este_caido(self):
        with mock.patch.object(clima, '_abrir', side_effect=urllib.error.URLError('sin red')) as abrir:
            respuesta = self.client.get('/predios/')
        self.assertEqual(respuesta.status_code, 200)
        abrir.assert_not_called()  # la página no consulta al proveedor: lo hace el navegador por el endpoint

    def test_mis_predios_incluye_la_tarjeta_con_proveedor_y_atribucion(self):
        html = self.client.get('/predios/').content.decode()
        self.assertIn('id="weather-card"', html)
        self.assertIn('data-weather-url="/api/predios/0/clima/"', html)
        self.assertIn('https://open-meteo.com/', html)
        self.assertIn('CC BY 4.0', html)
        # El crédito es el rótulo «Open-Meteo» (con enlace y licencia); ya no hay línea de fuente ni «Ver detalle».
        self.assertRegex(html, r'<a class="weather-provider"\s+href="https://open-meteo\.com/"')
        self.assertIn('Licencia CC BY 4.0">Open-Meteo</a>', html)
        self.assertNotIn('weather-credit', html)
        self.assertRegex(html, r'css/clima\.css\?v=\d+')
        self.assertRegex(html, r'js/predio-clima\.js\?v=\d+')

    def test_sin_predios_no_aparece_la_tarjeta_del_clima(self):
        self.client.force_login(self.beto)
        from core.models import Predio

        Predio.todos.filter(propietario=self.beto).delete()
        self.assertNotIn('weather-card', self.client.get('/predios/').content.decode())


class ClimaArchivosEstaticosTests(SimpleTestCase):
    def test_la_hoja_de_estilos_respeta_la_identidad_visual(self):
        css = CSS.read_text(encoding='utf-8')
        self.assertIsNone(re.search(r'font-weight:\s*(\d+|bold|bolder)\b', css))  # solo var(--fw-*)
        for familia in re.findall(r'font-family\s*:\s*([^;}]+)', css):
            self.assertTrue(familia.strip().startswith(('var(--', 'inherit')), familia)
        self.assertIn('.weather-card', css)

    def test_la_tarjeta_no_tiene_el_detalle_plegable_ni_la_linea_de_fuente(self):
        js = JS.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        for retirado in ('Ver detalle', 'weather-more', 'renderDetails', 'Ubicación usada', 'weather-credit'):
            self.assertNotIn(retirado, js, retirado)
            self.assertNotIn(retirado, css, retirado)
        self.assertIn('item.title = dayDetail(day, units, today);', js)  # el detalle de cada día sigue en su casilla

    def test_el_script_no_inserta_html_ni_inventa_valores(self):
        js = JS.read_text(encoding='utf-8')
        self.assertNotRegex(js, r'innerHTML|insertAdjacentHTML|document\.write|eval\(')
        self.assertIn("'use strict'", js)
        self.assertIn('data-weather-url', js)
        self.assertIn('AbortController', js)  # tiempo de espera del navegador
        self.assertNotRegex(js, r'-?\d{2}\.\d{3,}')  # ninguna coordenada escrita en el código
        self.assertNotRegex(js, r'open-meteo\.com/v1')  # el navegador nunca llama al proveedor directamente

    def test_el_script_maneja_datos_antiguos_y_limite_de_consultas_sin_reintentos_agresivos(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('desactualizado', js)  # marca visible cuando el pronóstico viene de una consulta anterior
        self.assertIn('reintentar_en', js)  # respeta el tiempo de espera que indica el servidor
        self.assertIn('startCooldown', js)  # el botón «Reintentar» espera en lugar de insistir
        self.assertNotRegex(js, r'setInterval\([^)]*(fetch|load)')  # nunca consulta en bucle
        self.assertIn('if (!result.data.desactualizado)', js)  # un dato antiguo no se guarda como si fuera reciente
