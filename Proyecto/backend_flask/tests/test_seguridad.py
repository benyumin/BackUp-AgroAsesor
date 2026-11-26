import time

from tests.helpers import cabeceras, pedir


def test_sin_cabeceras_no_autenticado(client_bd):
    r = client_bd.get("/api/v1/predios")
    assert r.status_code == 401
    assert r.get_json()["error"]["codigo"] == "NO_AUTENTICADO"


def test_sin_clave_interna_falla_cerrada(client):
    r = client.get("/api/v1/predios", headers=cabeceras(1, destino="/api/v1/predios"))
    assert r.status_code == 503
    assert r.get_json()["error"]["codigo"] == "AUTENTICACION_NO_CONFIGURADA"


def test_clave_corta_no_se_acepta(bd_path):
    from app import create_app

    app = create_app({"TESTING": True, "AGROASESOR_DB_PATH": bd_path, "AGROASESOR_INTERNAL_SECRET": "corta"})
    r = app.test_client().get("/api/v1/predios", headers=cabeceras(1, destino="/api/v1/predios", secreto="corta"))
    assert r.status_code == 503


def test_firma_con_otra_clave_se_rechaza(client_bd):
    assert pedir(client_bd, "/api/v1/predios", secreto="x" * 40).status_code == 401


def test_no_se_puede_cambiar_el_usuario(client_bd):
    h = cabeceras(1, destino="/api/v1/predios")
    h["X-AgroAsesor-User"] = "2"
    assert client_bd.get("/api/v1/predios", headers=h).status_code == 401


def test_no_se_puede_subir_a_admin(client_bd):
    h = cabeceras(1, destino="/api/v1/predios")
    h["X-AgroAsesor-Staff"] = "1"
    assert client_bd.get("/api/v1/predios", headers=h).status_code == 401


def test_firma_no_sirve_en_otra_ruta(client_bd):
    h = cabeceras(1, destino="/api/v1/predios")
    assert client_bd.get("/api/v1/zonas", headers=h).status_code == 401


def test_firma_no_sirve_con_otra_consulta(client_bd):
    h = cabeceras(1, destino="/api/v1/calculos")
    assert client_bd.get("/api/v1/calculos?potrero_id=1", headers=h).status_code == 401


def test_firma_vencida(client_bd):
    r = pedir(client_bd, "/api/v1/predios", marca=int(time.time()) - 3600)
    assert r.status_code == 401


def test_marca_futura_lejana(client_bd):
    assert pedir(client_bd, "/api/v1/predios", marca=int(time.time()) + 3600).status_code == 401


def test_cabeceras_basura(client_bd):
    h = {"X-AgroAsesor-User": "abc", "X-AgroAsesor-Timestamp": "x", "X-AgroAsesor-Signature": "z"}
    assert client_bd.get("/api/v1/predios", headers=h).status_code == 401


def test_usuario_inactivo_no_tiene_perfil(client_bd):
    assert pedir(client_bd, "/api/v1/usuarios/me", usuario=4).status_code == 404


def test_vector_fijo_coincide_con_django(client=None):
    # Mismo vector que core/tests/test_api.py (FirmaTests): ambas definiciones de la firma deben coincidir.
    from app.security.gateway import firmar

    assert (
        firmar("k" * 32, 7, True, 1700000000, "POST", "/api/v1/calculos?a=1", b'{"x":1}')
        == "25fc674706efda37f322e158b72366bc89da033988c045eca837b9d52143febc"
    )
