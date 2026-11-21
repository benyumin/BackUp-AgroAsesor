"""Pruebas de la puerta de enlace /api/v1/ (Django ↔ Flask).

Las pruebas de integración levantan la API Flask real en un hilo, apuntando a una COPIA de la base de pruebas de
Django (nunca a la base oficial), y se omiten si Flask no está instalado en el entorno.
"""

import sqlite3
import sys
import tempfile
import threading
from io import StringIO
from pathlib import Path
from unittest import SkipTest, mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, override_settings

from core import api_gateway
from core.models import Agricultor, Cultivo, Predio, Potrero

SECRETO = "t" * 40
RAIZ_FLASK = Path(settings.BASE_DIR) / "backend_flask"


def _flask_o_saltar(cls):
    try:
        if str(RAIZ_FLASK) not in sys.path:
            sys.path.append(str(RAIZ_FLASK))
        from app import create_app
        from werkzeug.serving import make_server
    except Exception as e:  # Flask no instalado en este entorno
        raise SkipTest(f"Flask no disponible: {e}")
    cls._create_app = staticmethod(create_app)
    cls._make_server = staticmethod(make_server)


def _levantar_flask(test):
    """Copia la base de PRUEBAS (con lo creado hasta ahora) y levanta Flask sobre esa copia en un hilo."""
    test.carpeta = tempfile.TemporaryDirectory()
    copia = Path(test.carpeta.name) / "copia.sqlite3"
    destino = sqlite3.connect(copia)
    connection.ensure_connection()
    # iterdump evita backup(), que se bloquea con la base en memoria compartida de las pruebas mientras hay una
    # transacción abierta.
    destino.executescript("\n".join(connection.connection.iterdump()))
    destino.close()
    app = test._create_app({"TESTING": True, "AGROASESOR_DB_PATH": str(copia), "AGROASESOR_INTERNAL_SECRET": SECRETO})
    test.servidor = test._make_server("127.0.0.1", 0, app)
    test.hilo = threading.Thread(target=test.servidor.serve_forever, daemon=True)
    test.hilo.start()
    test.url = f"http://127.0.0.1:{test.servidor.server_port}"


def _detener_flask(test):
    test.servidor.shutdown()
    test.hilo.join(5)
    test.carpeta.cleanup()


class FirmaTests(TestCase):
    def test_vector_fijo_coincide_con_flask(self):
        # El mismo vector está en backend_flask/tests/test_seguridad.py: si cambia uno, deben cambiar ambos.
        self.assertEqual(
            api_gateway.firmar("k" * 32, 7, True, 1700000000, "POST", "/api/v1/calculos?a=1", b'{"x":1}'),
            "25fc674706efda37f322e158b72366bc89da033988c045eca837b9d52143febc",
        )

    def test_la_firma_depende_de_cada_dato(self):
        base = api_gateway.firmar(SECRETO, 1, False, 100, "GET", "/api/v1/predios", b"")
        for otra in (
            api_gateway.firmar(SECRETO, 2, False, 100, "GET", "/api/v1/predios", b""),
            api_gateway.firmar(SECRETO, 1, True, 100, "GET", "/api/v1/predios", b""),
            api_gateway.firmar(SECRETO, 1, False, 101, "GET", "/api/v1/predios", b""),
            api_gateway.firmar(SECRETO, 1, False, 100, "POST", "/api/v1/predios", b""),
            api_gateway.firmar(SECRETO, 1, False, 100, "GET", "/api/v1/zonas", b""),
            api_gateway.firmar(SECRETO, 1, False, 100, "GET", "/api/v1/predios", b"x"),
        ):
            self.assertNotEqual(base, otra)


class PuertaDeEnlaceSinFlaskTests(TestCase):
    """No necesitan Flask: la URL del servicio se simula."""

    def test_sin_configurar_responde_503_y_no_reenvia(self):
        with override_settings(AGROASESOR_API_URL="", AGROASESOR_INTERNAL_SECRET=""):
            with mock.patch("urllib.request.OpenerDirector.open") as abrir:
                r = Client().get("/api/v1/health")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["codigo"], "API_NO_CONFIGURADA")
        abrir.assert_not_called()

    def test_clave_corta_responde_503(self):
        with override_settings(AGROASESOR_API_URL="http://x", AGROASESOR_INTERNAL_SECRET="corta"):
            self.assertEqual(Client().get("/api/v1/health").status_code, 503)

    @override_settings(AGROASESOR_API_URL="http://127.0.0.1:9", AGROASESOR_INTERNAL_SECRET=SECRETO)
    def test_anonimo_no_puede_pedir_datos_privados(self):
        with mock.patch("urllib.request.OpenerDirector.open") as abrir:
            for ruta in ("predios", "usuarios/me", "calculos", "potreros/1"):
                r = Client().get(f"/api/v1/{ruta}")
                self.assertEqual(r.status_code, 401, ruta)
            abrir.assert_not_called()

    @override_settings(AGROASESOR_API_URL="http://127.0.0.1:9", AGROASESOR_INTERNAL_SECRET=SECRETO)
    def test_rutas_raras_se_rechazan(self):
        c = Client()
        for ruta in ("a/../b", "a//b", "a%00b", "a b", "-/..", "./x"):
            self.assertEqual(c.get(f"/api/v1/{ruta}").status_code, 404, ruta)

    @override_settings(AGROASESOR_API_URL="http://127.0.0.1:9", AGROASESOR_INTERNAL_SECRET=SECRETO)
    def test_servicio_caido_da_502_json(self):
        r = Client().get("/api/v1/health")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["error"]["codigo"], "API_NO_DISPONIBLE")

    @override_settings(AGROASESOR_API_URL="http://127.0.0.1:9", AGROASESOR_INTERNAL_SECRET=SECRETO)
    def test_escritura_exige_csrf_de_django(self):
        usuario = get_user_model().objects.create_user("u", "u@example.com", "Clave-segura-123")
        c = Client(enforce_csrf_checks=True)
        c.force_login(usuario)
        self.assertEqual(c.post("/api/v1/predios", "{}", content_type="application/json").status_code, 403)

    @override_settings(AGROASESOR_API_URL="http://127.0.0.1:9", AGROASESOR_INTERNAL_SECRET=SECRETO)
    def test_cabeceras_falsas_del_navegador_no_se_reenvian(self):
        usuario = get_user_model().objects.create_user("u2", "u2@example.com", "Clave-segura-123")
        c = Client()
        c.force_login(usuario)
        capturado = {}

        def falso(self_, peticion, timeout=None):
            capturado.update({k.lower(): v for k, v in peticion.header_items()})
            raise OSError("fin")

        with mock.patch("urllib.request.OpenerDirector.open", falso):
            c.get("/api/v1/predios", HTTP_X_AGROASESOR_USER="999", HTTP_X_AGROASESOR_STAFF="1")
        self.assertEqual(capturado["x-agroasesor-user"], str(usuario.pk))
        self.assertEqual(capturado["x-agroasesor-staff"], "0")


class PuertaDeEnlaceConFlaskTests(TestCase):
    """Integración real: Django firma, Flask verifica y lee una copia de la base de pruebas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _flask_o_saltar(cls)

    def setUp(self):
        User = get_user_model()
        self.ana = User.objects.create_user(
            "ana_api", "ana_api@example.com", "Clave-segura-123", first_name="Ana", last_name="Pérez"
        )
        self.luis = User.objects.create_user("luis_api", "luis_api@example.com", "Clave-segura-123")
        self.admin = User.objects.create_user("admin_api", "admin_api@example.com", "Clave-segura-123", is_staff=True)
        Agricultor.objects.create(user=self.ana, telefono="", comuna="Melipilla")
        p_ana = Predio.objects.create(
            propietario=self.ana, nombre="Predio Ana", ubicacion="Melipilla", superficie_ha="10.5"
        )
        p_luis = Predio.objects.create(
            propietario=self.luis, nombre="Predio Luis", ubicacion="Talagante", superficie_ha="3"
        )
        self.pot_ana = Potrero.objects.create(predio=p_ana, nombre="Norte", superficie_ha="1.5", perimetro=[])
        self.pot_luis = Potrero.objects.create(predio=p_luis, nombre="Sur", superficie_ha="2", perimetro=[])
        self.predio_luis = p_luis
        _levantar_flask(self)

    def tearDown(self):
        _detener_flask(self)

    def _cliente(self, usuario=None):
        c = Client()
        if usuario:
            c.force_login(usuario)
        return c

    def test_health_publico_con_base_solo_lectura(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            r = self._cliente().get("/api/v1/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["base_datos"], "conectada_solo_lectura")

    def test_usuario_ve_solo_sus_predios_y_potreros(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            c = self._cliente(self.ana)
            predios = c.get("/api/v1/predios").json()["predios"]
            self.assertEqual([p["nombre"] for p in predios], ["Predio Ana"])
            self.assertEqual(predios[0]["superficie"]["texto"], "10,5 ha (105.000 m²)")
            potreros = c.get(f"/api/v1/predios/{predios[0]['id']}/potreros").json()["potreros"]
            self.assertEqual(potreros[0]["superficie"]["metros_cuadrados"], 15000)
            self.assertEqual(c.get(f"/api/v1/potreros/{self.pot_luis.pk}").status_code, 404)
            self.assertEqual(c.get(f"/api/v1/predios/{self.predio_luis.pk}").status_code, 404)

    def test_perfil_viene_de_la_cuenta_de_django(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            perfil = self._cliente(self.ana).get("/api/v1/usuarios/me").json()
            self.assertEqual(perfil["usuario"], "ana_api")
            self.assertTrue(perfil["perfil_completo"])
            self.assertEqual(perfil["nombre"], "Ana Pérez")  # first_name + last_name de la cuenta de Django
            self.assertFalse(perfil["es_admin"])
            self.assertTrue(self._cliente(self.admin).get("/api/v1/usuarios/me").json()["es_admin"])
            self.assertNotIn("password", str(perfil))

    def test_sin_sesion_no_hay_datos(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            self.assertEqual(self._cliente().get("/api/v1/predios").status_code, 401)

    def test_clave_distinta_en_django_hace_que_flask_rechace(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET="z" * 40):
            r = self._cliente(self.ana).get("/api/v1/predios")
        self.assertEqual(r.status_code, 401)

    def test_consulta_con_filtros_firmada_correctamente(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            r = self._cliente(self.ana).get(f"/api/v1/calculos?potrero_id={self.pot_ana.pk}&limite=5")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["calculos"], [])

    def test_escritura_no_implementada_y_no_toca_la_base(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            r = self._cliente(self.ana).post("/api/v1/predios", "{}", content_type="application/json")
        self.assertEqual(r.status_code, 501)
        self.assertEqual(Predio.objects.filter(propietario=self.ana).count(), 1)

    def test_openapi_visible_sin_sesion(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            r = self._cliente().get("/api/v1/openapi.json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["openapi"], "3.0.3")

    # --- Catálogo de fertilizantes a través de Django (integración real Django → Flask) ---

    def test_fertilizantes_publicos_por_django_sin_sesion(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            r = self._cliente().get("/api/v1/catalogo/fertilizantes/superfosfato-simple")
        self.assertEqual(r.status_code, 200)
        comp = r.json()["composicion_referencial"]
        self.assertEqual((comp["P2O5"]["min_pct"], comp["P2O5"]["max_pct"]), (16, 20))
        self.assertEqual(comp["K2O"]["max_pct"], 0)
        self.assertEqual(comp["S"]["min_pct"], 12)
        self.assertEqual(r.json()["fuente"]["organismo"], "FAO / IFA")

    def test_calculo_de_fertilizante_exige_sesion_y_calcula_por_django(self):
        cuerpo = '{"fertilizante_id": "urea", "nutriente": "N", "requerimiento_kg_ha": 92, "superficie_ha": 2, "ley_declarada_pct": 46}'
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            anonimo = self._cliente().post("/api/v1/catalogo/calcular-fertilizante", cuerpo, content_type="application/json")
            r = self._cliente(self.ana).post("/api/v1/catalogo/calcular-fertilizante", cuerpo, content_type="application/json")
        self.assertEqual(anonimo.status_code, 401)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["producto_total_kg"], {"min": 400.0, "max": 400.0})
        self.assertEqual(r.json()["ley"]["tipo"], "declarada_en_etiqueta")

    def test_revision_de_etiqueta_no_afirma_cumplimiento_integral(self):
        cuerpo = '{"categoria": "inorganico", "nutrientes": {"N": 46}}'
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            r = self._cliente(self.ana).post("/api/v1/catalogo/revisar-etiqueta", cuerpo, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["cumple_contenidos_minimos"])
        self.assertIn("NO acredita", r.json()["aviso"])

    def test_ruta_pendiente_responde_501_por_django_solo_con_sesion(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            self.assertEqual(self._cliente().get("/api/v1/actividades").status_code, 401)
            r = self._cliente(self.ana).get("/api/v1/actividades")
        self.assertEqual(r.status_code, 501)
        self.assertEqual(r.json()["error"]["codigo"], "NO_IMPLEMENTADO")

    def test_luis_no_ve_datos_de_ana_por_django(self):
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            c = self._cliente(self.luis)
            self.assertEqual(c.get(f"/api/v1/potreros/{self.pot_ana.pk}").status_code, 404)
            self.assertEqual([p["nombre"] for p in c.get("/api/v1/predios").json()["predios"]], ["Predio Luis"])
            self.assertEqual(c.get(f"/api/v1/calculos?potrero_id={self.pot_ana.pk}").json()["calculos"], [])


class CatalogoInicialEnApiTests(TestCase):
    """Los insumos de data/insumos.csv, cargados con cargar_catalogo_inicial, llegan a /api/v1/insumos por Django."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _flask_o_saltar(cls)

    def setUp(self):
        call_command("cargar_catalogo_inicial", stdout=StringIO())
        self.usuario = get_user_model().objects.create_user("eva_api", "eva_api@example.com", "Clave-segura-123")
        _levantar_flask(self)

    def tearDown(self):
        _detener_flask(self)

    def test_insumos_del_catalogo_inicial_disponibles_en_la_api(self):
        c = Client()
        c.force_login(self.usuario)
        with override_settings(AGROASESOR_API_URL=self.url, AGROASESOR_INTERNAL_SECRET=SECRETO):
            r = c.get("/api/v1/insumos")
            self.assertEqual(r.status_code, 200)
            insumos = {i["nombre"]: i for i in r.json()["insumos"]}
            self.assertEqual(
                set(insumos),
                {
                    "Semilla de trigo", "Semilla de arroz", "Papa semilla",
                    "Urea (referencia de fertilizante)", "Mezcla fertilizante N-P-K (referencia)",
                },
            )
            trigo = insumos["Semilla de trigo"]
            self.assertEqual(trigo["tipo"], "Semilla")
            self.assertEqual(trigo["cultivo_id"], Cultivo.objects.get(nombre="Trigo").pk)
            self.assertIsNone(trigo["dosis_hectarea"])  # sin dosis inventadas
            self.assertEqual(trigo["verificacion"]["estado"], "unverified")
            self.assertIsNone(insumos["Urea (referencia de fertilizante)"]["cultivo_id"])
            # Filtros del catálogo: por tipo y por cultivo.
            semillas = c.get("/api/v1/insumos?tipo=Semilla").json()["insumos"]
            self.assertEqual(len(semillas), 3)
            papa = c.get(f"/api/v1/insumos?cultivo_id={Cultivo.objects.get(nombre='Papa').pk}").json()["insumos"]
            self.assertEqual([i["nombre"] for i in papa], ["Papa semilla"])
            detalle = c.get(f"/api/v1/insumos/{trigo['id']}")
            self.assertEqual(detalle.status_code, 200)
            self.assertEqual(detalle.json()["nombre"], "Semilla de trigo")
            # Sin sesión no se listan.
            self.assertEqual(Client().get("/api/v1/insumos").status_code, 401)
