"""Caché compartida del clima (``core.clima_compartido`` + ``core.cache_compartida``) y su endpoint.

Cubre lo que fallaba en Vercel: cada instancia tenía su propia memoria y Open-Meteo recibía una consulta por visita.
Ninguna prueba sale a internet (``core.clima._abrir`` se simula) ni escribe fuera de la base de pruebas de Django
(SQLite temporal, nunca Supabase). El reloj es simulado: no se espera nunca de verdad.
"""

import json
import urllib.error
from unittest import mock

from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase, override_settings

from core import cache_compartida, clima, clima_compartido
from core.models import CacheCompartida, Predio
from core.tests.test_clima import CUADRADO, RESPUESTA, cuerpo

INICIO = 1_800_000_000  # un instante cualquiera (segundos desde 1970)


class Reloj:
    def __init__(self, t=INICIO):
        self.t = t

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        self.t += segundos


def http(codigo, cabeceras=None, cuerpo_json=None):
    import io

    cuerpo_bytes = io.BytesIO(json.dumps(cuerpo_json).encode()) if cuerpo_json is not None else None
    return urllib.error.HTTPError('https://x', codigo, 'x', cabeceras or {}, cuerpo_bytes)


class BaseCompartida(TestCase):
    def setUp(self):
        cache.clear()
        clima_compartido.reiniciar()
        self.reloj = Reloj()
        for objetivo in (
            mock.patch.object(clima_compartido, '_ahora', self.reloj),
            mock.patch.object(clima_compartido, '_dormir', lambda segundos: None),
        ):
            objetivo.start()
            self.addCleanup(objetivo.stop)
        self.addCleanup(cache.clear)
        self.addCleanup(clima_compartido.reiniciar)

    def otra_instancia(self):
        """Simula que la petición llega a OTRA instancia: su memoria local está vacía; la base es la misma."""
        cache.clear()
        clima_compartido.reiniciar()

    def pedir(self, lat=-33.705, lon=-71.205, efecto=None, respuesta=None):
        """Llama al servicio con Open-Meteo simulado; devuelve (resultado, mock de la red)."""
        with mock.patch.object(
            clima, '_abrir', side_effect=efecto, return_value=None if efecto else (respuesta or cuerpo(RESPUESTA))
        ) as abrir:
            return clima_compartido.obtener_pronostico(lat, lon), abrir

    def pedir_con_error(self, efecto, lat=-33.705, lon=-71.205):
        with mock.patch.object(clima, '_abrir', side_effect=efecto) as abrir:
            with self.assertRaises(clima.ClimaError) as ctx:
                clima_compartido.obtener_pronostico(lat, lon)
        return ctx.exception, abrir


class CacheCompartidaTests(BaseCompartida):
    def test_la_primera_consulta_va_a_open_meteo_y_la_segunda_usa_la_memoria(self):
        primera, abrir = self.pedir()
        self.assertEqual((primera.en_cache, primera.desactualizado, primera.antiguedad), (False, False, 0))
        self.assertEqual(abrir.call_count, 1)
        segunda, abrir_2 = self.pedir()
        abrir_2.assert_not_called()
        self.assertTrue(segunda.en_cache)

    def test_otra_instancia_reutiliza_lo_que_guardo_la_primera_sin_consultar(self):
        self.pedir()
        self.reloj.avanzar(120)
        self.otra_instancia()
        resultado, abrir = self.pedir()
        abrir.assert_not_called()  # antes (caché local) cada instancia habría vuelto a consultar
        self.assertTrue(resultado.en_cache)
        self.assertFalse(resultado.desactualizado)
        self.assertEqual(resultado.antiguedad, 120)
        self.assertEqual(CacheCompartida.objects.filter(clave__startswith='clima:').count(), 1)

    def test_predios_con_coordenadas_aproximadas_iguales_comparten_la_entrada(self):
        self.pedir(-33.7012, -71.2008)
        _, cercano = self.pedir(-33.6992, -71.1994)  # a ~300 m: misma zona de 2 decimales (-33.70, -71.20)
        cercano.assert_not_called()
        _, lejano = self.pedir(-33.80, -71.30)
        self.assertEqual(lejano.call_count, 1)
        self.assertEqual(CacheCompartida.objects.filter(clave__startswith='clima:').count(), 2)

    @override_settings(CLIMA_PRECISION_COORDENADAS=3)
    def test_la_precision_es_configurable(self):
        self.pedir(-33.7012, -71.2008)
        _, otro = self.pedir(-33.6992, -71.1994)
        self.assertEqual(otro.call_count, 1)  # con 3 decimales ya son zonas distintas

    def test_la_entrada_vence_segun_el_tiempo_configurado(self):
        with override_settings(CLIMA_CACHE_SEGUNDOS=300):
            self.pedir()
            self.reloj.avanzar(299)
            _, antes = self.pedir()
            antes.assert_not_called()
            self.reloj.avanzar(2)
            resultado, despues = self.pedir()
        self.assertEqual(despues.call_count, 1)
        self.assertFalse(resultado.en_cache)

    def test_la_vigencia_tambien_vence_en_la_tabla_compartida(self):
        with override_settings(CLIMA_CACHE_SEGUNDOS=300):
            self.pedir()
            self.reloj.avanzar(301)
            self.otra_instancia()
            _, abrir = self.pedir()
        self.assertEqual(abrir.call_count, 1)

    def test_no_se_guarda_ubicacion_ni_datos_de_personas(self):
        self.pedir(-33.705, -71.205)
        filas = list(CacheCompartida.objects.all())
        self.assertTrue(filas)
        for fila in filas:
            texto = f'{fila.clave} {fila.carga}'
            for prohibido in ('-33.7', '-71.2', '33.70', '71.20', 'Campo', 'propietario', '@'):
                self.assertNotIn(prohibido, texto)
        self.assertRegex(filas[0].clave, r'^clima:[0-9a-f]{40}$')  # código opaco, no la coordenada

    def test_una_fila_alterada_o_creada_desde_fuera_se_ignora(self):
        self.pedir()
        fila = CacheCompartida.objects.get(clave__startswith='clima:')
        manipulada = json.loads(fila.carga)
        manipulada['actual']['temperatura'] = 45.0  # alguien con acceso a la tabla cambia el pronóstico
        CacheCompartida.objects.filter(pk=fila.pk).update(carga=json.dumps(manipulada))
        self.otra_instancia()
        with self.assertLogs('core.cache_compartida', 'WARNING'):
            resultado, abrir = self.pedir()
        self.assertEqual(abrir.call_count, 1)  # no se confía en ella: se consulta de nuevo
        self.assertEqual(resultado.datos['actual']['temperatura'], 18.2)

    def test_una_fila_con_fechas_cambiadas_no_valida(self):
        self.pedir()
        fila = CacheCompartida.objects.get(clave__startswith='clima:')
        CacheCompartida.objects.filter(pk=fila.pk).update(vence_en=fila.vence_en.replace(year=2099))
        self.otra_instancia()
        with self.assertLogs('core.cache_compartida', 'WARNING'):
            _, abrir = self.pedir()
        self.assertEqual(abrir.call_count, 1)

    def test_una_fila_sin_firma_creada_con_la_api_publica_no_sirve(self):
        clave = cache_compartida.clave_opaca('clima', '-33.70', '-71.20')
        ahora = self.reloj.t
        CacheCompartida.objects.create(
            clave=clave, carga=json.dumps(RESPUESTA), firma='0' * 64,
            obtenido_en=cache_compartida._fecha(ahora), vence_en=cache_compartida._fecha(ahora + 900),
        )
        with self.assertLogs('core.cache_compartida', 'WARNING'):
            _, abrir = self.pedir()
        self.assertEqual(abrir.call_count, 1)

    def test_las_entradas_viejas_se_borran_al_guardar(self):
        self.pedir(-33.60, -71.10)
        self.reloj.avanzar(7 * 3600)  # más que CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS
        self.pedir(-33.90, -71.40)
        claves = list(CacheCompartida.objects.values_list('clave', flat=True))
        self.assertEqual(len([c for c in claves if c.startswith('clima:')]), 1)

    @override_settings(CLIMA_CACHE_COMPARTIDA=False)
    def test_sin_tabla_compartida_se_usa_solo_la_memoria(self):
        self.pedir()
        self.assertEqual(CacheCompartida.objects.count(), 0)
        _, abrir = self.pedir()
        abrir.assert_not_called()

    def test_si_la_tabla_falla_el_clima_sigue_funcionando_con_la_memoria_local(self):
        with mock.patch.object(cache_compartida, 'leer', side_effect=OperationalError('no existe la tabla')), \
                mock.patch.object(cache_compartida, 'guardar', side_effect=OperationalError('no existe la tabla')), \
                mock.patch.object(cache_compartida, 'reclamar', side_effect=OperationalError('no existe la tabla')), \
                mock.patch.object(cache_compartida, 'purgar', side_effect=OperationalError('no existe la tabla')):
            with self.assertLogs('core.clima_compartido', 'WARNING') as registro:
                primera, _ = self.pedir()
            _, segunda = self.pedir()
        self.assertEqual(primera.datos['zona_horaria'], 'America/Santiago')
        segunda.assert_not_called()  # la memoria de la instancia sigue sirviendo
        self.assertTrue(any('0024' in linea for linea in registro.output))  # el log explica qué hacer
        self.assertNotIn('no existe la tabla', ' '.join(registro.output))  # y no filtra el detalle del error

    def test_la_clave_de_pago_cambia_el_dominio_y_nunca_va_al_log(self):
        with override_settings(CLIMA_API_KEY='clave-de-prueba-123'):
            _, abrir = self.pedir()
        url = abrir.call_args.args[0]
        self.assertTrue(url.startswith('https://customer-api.open-meteo.com/v1/forecast?'))
        self.assertIn('apikey=clave-de-prueba-123', url)
        self.otra_instancia()
        CacheCompartida.objects.all().delete()
        _, sin_clave = self.pedir()
        self.assertTrue(sin_clave.call_args.args[0].startswith('https://api.open-meteo.com/v1/forecast?'))
        self.assertNotIn('apikey', sin_clave.call_args.args[0])


class PausaPorLimiteTests(BaseCompartida):
    def test_un_429_pone_una_pausa_que_comparten_todas_las_instancias(self):
        error, abrir = self.pedir_con_error(http(429))
        self.assertEqual((error.codigo, error.estado), ('proveedor_limite', 503))
        self.assertEqual(abrir.call_count, 1)
        self.assertEqual(error.reintentar_en, 120)  # CLIMA_ESPERA_LIMITE_SEGUNDOS por defecto: Open-Meteo no dijo cuánto

        self.reloj.avanzar(30)
        for _ in range(3):  # ni esta instancia ni otra vuelven a insistir mientras dura la pausa
            self.otra_instancia()
            error_2, abrir_2 = self.pedir_con_error(http(200))
            abrir_2.assert_not_called()
            self.assertEqual(error_2.codigo, 'proveedor_limite')
            self.assertEqual(error_2.reintentar_en, 91)

    def test_al_terminar_la_pausa_se_vuelve_a_consultar(self):
        self.pedir_con_error(http(429))
        self.reloj.avanzar(121)
        resultado, abrir = self.pedir()
        self.assertEqual(abrir.call_count, 1)
        self.assertFalse(resultado.desactualizado)

    def test_retry_after_del_proveedor_manda_sobre_el_valor_por_defecto(self):
        error, _ = self.pedir_con_error(http(429, {'Retry-After': '90'}))
        self.assertEqual(error.reintentar_en, 90)

    def test_el_aviso_de_limite_de_open_meteo_define_la_pausa(self):
        casos = {
            'Minutely API request limit exceeded. Please try again in the next minute.': 60,
            'Hourly API request limit exceeded. Please try again in the next hour.': 600,
            'Daily API request limit exceeded. Please try again tomorrow.': 1800,
        }
        for aviso, esperado in casos.items():
            with self.subTest(aviso):
                CacheCompartida.objects.all().delete()
                self.otra_instancia()
                self.reloj.avanzar(3600)
                error, _ = self.pedir_con_error(http(429, cuerpo_json={'error': True, 'reason': aviso}))
                self.assertEqual(error.reintentar_en, esperado)

    @override_settings(CLIMA_ESPERA_LIMITE_MAX_SEGUNDOS=300)
    def test_la_pausa_tiene_un_tope_configurable(self):
        error, _ = self.pedir_con_error(http(429, {'Retry-After': '86400'}))
        self.assertEqual(error.reintentar_en, 300)

    def test_una_pausa_demasiado_corta_se_sube_al_minimo(self):
        error, _ = self.pedir_con_error(http(429, {'Retry-After': '1'}))
        self.assertEqual(error.reintentar_en, clima_compartido.PAUSA_MINIMA)

    def test_con_pronostico_anterior_un_429_devuelve_ese_pronostico_marcado(self):
        self.pedir()
        self.reloj.avanzar(1000)
        with mock.patch.object(clima, '_abrir', side_effect=http(429)) as abrir:
            resultado = clima_compartido.obtener_pronostico(-33.705, -71.205)
        self.assertEqual(abrir.call_count, 1)
        self.assertTrue(resultado.desactualizado)
        self.assertEqual(resultado.motivo, 'proveedor_limite')
        self.assertEqual(resultado.antiguedad, 1000)
        self.assertEqual(resultado.datos['zona_horaria'], 'America/Santiago')


class DatosAnterioresTests(BaseCompartida):
    def vencer(self):
        self.pedir()
        self.reloj.avanzar(1000)

    def test_si_el_proveedor_falla_se_muestra_el_ultimo_pronostico_valido_marcado(self):
        casos = [
            (TimeoutError(), 'tiempo_agotado'),
            (http(500), 'proveedor_error'),
            (http(502), 'proveedor_error'),
            (urllib.error.URLError('sin red'), 'proveedor_no_disponible'),
        ]
        for efecto, codigo in casos:
            with self.subTest(codigo):
                cache.clear()
                clima_compartido.reiniciar()
                CacheCompartida.objects.all().delete()
                self.reloj.avanzar(10_000)
                self.vencer()
                with mock.patch.object(clima, '_abrir', side_effect=efecto):
                    resultado = clima_compartido.obtener_pronostico(-33.705, -71.205)
                self.assertTrue(resultado.desactualizado)
                self.assertEqual(resultado.motivo, codigo)
                self.assertEqual(resultado.antiguedad, 1000)
                self.assertTrue(resultado.en_cache)

    def test_una_respuesta_invalida_tampoco_reemplaza_lo_anterior_ni_inventa_nada(self):
        self.vencer()
        with mock.patch.object(clima, '_abrir', return_value=(200, b'{"error": true}')):
            resultado = clima_compartido.obtener_pronostico(-33.705, -71.205)
        self.assertTrue(resultado.desactualizado)
        self.assertEqual(resultado.datos['actual']['temperatura'], 18.2)  # el dato anterior, intacto

    def test_sin_pronostico_anterior_un_fallo_es_un_error_y_no_se_inventan_datos(self):
        error, _ = self.pedir_con_error(http(500))
        self.assertEqual(error.codigo, 'proveedor_error')
        self.assertEqual(CacheCompartida.objects.filter(clave__startswith='clima:').count(), 0)

    @override_settings(CLIMA_DATOS_ANTIGUOS_MAX_SEGUNDOS=1800)
    def test_un_pronostico_demasiado_viejo_ya_no_se_ofrece(self):
        self.pedir()
        self.reloj.avanzar(1801)
        error, _ = self.pedir_con_error(http(500))
        self.assertEqual(error.codigo, 'proveedor_error')

    def test_cuando_el_proveedor_se_recupera_se_guarda_lo_nuevo(self):
        self.vencer()
        with mock.patch.object(clima, '_abrir', side_effect=http(500)):
            self.assertTrue(clima_compartido.obtener_pronostico(-33.705, -71.205).desactualizado)
        nueva = json.loads(json.dumps(RESPUESTA))
        nueva['current']['temperature_2m'] = 21.0
        resultado, _ = self.pedir(respuesta=cuerpo(nueva))
        self.assertFalse(resultado.desactualizado)
        self.assertEqual(resultado.datos['actual']['temperatura'], 21.0)
        self.assertEqual(resultado.antiguedad, 0)


class ConsultasSimultaneasTests(BaseCompartida):
    def marca(self):
        clave = cache_compartida.clave_opaca('clima', '-33.70', '-71.20')
        return clave.replace('clima:', 'clima-consulta:', 1)

    def test_si_otra_instancia_ya_consulta_se_espera_su_resultado_y_no_se_duplica_la_llamada(self):
        self.assertTrue(cache_compartida.reclamar(self.marca(), self.reloj.t, 9))  # «otra instancia» consultando
        guardados = []

        def la_otra_termina(segundos):
            if not guardados:  # mientras esta petición espera, la otra instancia deja su resultado
                otra = clima.normalizar(RESPUESTA)
                otra['obtenido_en'] = '2026-11-22T12:00:00+00:00'
                cache_compartida.guardar(
                    cache_compartida.clave_opaca('clima', '-33.70', '-71.20'), otra, self.reloj.t, self.reloj.t + 900
                )
                guardados.append(True)

        with mock.patch.object(clima_compartido, '_dormir', la_otra_termina):
            resultado, abrir = self.pedir()
        abrir.assert_not_called()
        self.assertTrue(resultado.en_cache)

    @override_settings(CLIMA_ESPERA_CONCURRENTE_SEGUNDOS=2)
    def test_la_espera_es_acotada_y_si_nadie_responde_se_consulta_una_vez(self):
        cache_compartida.reclamar(self.marca(), self.reloj.t, 9)
        reloj_real = iter(x * 1.0 for x in range(100))
        with mock.patch.object(clima_compartido, '_monotonico', side_effect=lambda: next(reloj_real)):
            resultado, abrir = self.pedir()
        self.assertEqual(abrir.call_count, 1)
        self.assertFalse(resultado.en_cache)

    def test_una_marca_vencida_de_una_instancia_caida_no_bloquea(self):
        cache_compartida.reclamar(self.marca(), self.reloj.t, 9)
        self.reloj.avanzar(10)
        resultado, abrir = self.pedir()
        self.assertEqual(abrir.call_count, 1)
        self.assertFalse(resultado.en_cache)

    def test_la_marca_se_libera_al_terminar_incluso_si_la_consulta_falla(self):
        self.pedir_con_error(http(500))
        self.assertEqual(CacheCompartida.objects.filter(clave__startswith='clima-consulta:').count(), 0)
        self.pedir()
        self.assertEqual(CacheCompartida.objects.filter(clave__startswith='clima-consulta:').count(), 0)

    def test_reclamar_es_atomico_solo_una_gana(self):
        self.assertTrue(cache_compartida.reclamar('clima-consulta:x', 1000, 9))
        self.assertFalse(cache_compartida.reclamar('clima-consulta:x', 1005, 9))
        self.assertTrue(cache_compartida.reclamar('clima-consulta:x', 1010, 9))  # la anterior ya venció


class EndpointCompartidoTests(BaseCompartida):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

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
        super().setUp()
        self.client.force_login(self.ana)

    def get(self, predio=None, efecto=None):
        predio = predio or self.predio
        with mock.patch.object(
            clima, '_abrir', side_effect=efecto, return_value=None if efecto else cuerpo(RESPUESTA)
        ) as abrir:
            return self.client.get(f'/api/predios/{predio.pk}/clima/'), abrir

    def test_respuesta_normal_no_esta_desactualizada(self):
        respuesta, _ = self.get()
        datos = respuesta.json()
        self.assertEqual(
            (datos['en_cache'], datos['desactualizado'], datos['antiguedad_segundos'], datos['aviso']),
            (False, False, 0, None),
        )

    def test_dos_usuarios_con_predios_en_el_mismo_punto_hacen_una_sola_consulta_pero_cada_uno_ve_solo_lo_suyo(self):
        self.get()
        self.otra_instancia()
        self.client.force_login(self.beto)
        respuesta, abrir = self.get(self.ajeno)
        abrir.assert_not_called()
        datos = respuesta.json()
        self.assertEqual(datos['predio']['nombre'], 'Campo de Beto')
        self.assertTrue(datos['en_cache'])
        self.assertNotIn('Campo Norte', json.dumps(datos))  # nada del otro predio ni de su dueña
        self.assertNotIn('ana@example.com', json.dumps(datos))
        ajena, _ = self.get(self.predio)  # y Beto no puede pedir el predio de Ana aunque la zona esté en caché
        self.assertEqual(ajena.status_code, 404)

    def test_sin_sesion_no_se_toca_ni_la_cache_ni_el_proveedor(self):
        self.client.logout()
        respuesta, abrir = self.get()
        self.assertEqual(respuesta.status_code, 401)
        abrir.assert_not_called()
        self.assertEqual(CacheCompartida.objects.count(), 0)

    def test_si_falla_el_proveedor_el_agricultor_ve_el_pronostico_anterior_con_aviso(self):
        self.get()
        self.reloj.avanzar(3 * 3600 + 5 * 60)
        respuesta, _ = self.get(efecto=http(500))
        datos = respuesta.json()
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(datos['ok'])
        self.assertTrue(datos['desactualizado'])
        self.assertTrue(datos['en_cache'])
        self.assertEqual(datos['antiguedad_segundos'], 3 * 3600 + 5 * 60)
        self.assertIn('3 h 5 min', datos['aviso'])
        self.assertIn('último pronóstico disponible', datos['aviso'])
        self.assertEqual(datos['actual']['temperatura'], 18.2)

    def test_el_aviso_menciona_el_limite_cuando_ese_es_el_motivo(self):
        self.get()
        self.reloj.avanzar(2000)
        datos = self.get(efecto=http(429))[0].json()
        self.assertTrue(datos['desactualizado'])
        self.assertIn('limitando las consultas', datos['aviso'])
        self.assertIn('33 min', datos['aviso'])

    def test_en_pausa_y_sin_datos_anteriores_responde_503_con_cuanto_esperar(self):
        respuesta, _ = self.get(efecto=http(429, {'Retry-After': '75'}))
        self.assertEqual(respuesta.status_code, 503)
        error = respuesta.json()['error']
        self.assertEqual((error['codigo'], error['reintentar_en']), ('proveedor_limite', 75))
        self.assertEqual(respuesta['Retry-After'], '75')
        otra, abrir = self.get(efecto=http(200))
        abrir.assert_not_called()  # la pausa se respeta también en la petición siguiente
        self.assertEqual(otra.status_code, 503)

    def test_el_endpoint_sigue_funcionando_aunque_falle_la_tabla_compartida(self):
        with mock.patch.object(cache_compartida, 'leer', side_effect=OperationalError('x')), \
                mock.patch.object(cache_compartida, 'guardar', side_effect=OperationalError('x')), \
                mock.patch.object(cache_compartida, 'reclamar', side_effect=OperationalError('x')), \
                mock.patch.object(cache_compartida, 'purgar', side_effect=OperationalError('x')):
            with self.assertLogs('core.clima_compartido', 'WARNING'):
                respuesta, _ = self.get()
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()['ok'])
