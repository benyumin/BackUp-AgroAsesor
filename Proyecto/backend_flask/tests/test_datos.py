import sqlite3

from tests.helpers import pedir


def test_perfil_propio(client_bd):
    cuerpo = pedir(client_bd, "/api/v1/usuarios/me", usuario=1).get_json()
    assert cuerpo["usuario"] == "ana" and cuerpo["perfil_completo"] is True and cuerpo["es_admin"] is False
    assert cuerpo["nombre"] == "Ana Pérez"  # sale de first_name + last_name de la cuenta, no de la ficha
    assert "password" not in cuerpo and "contraseña" not in str(cuerpo).lower()


def test_perfil_incompleto_se_informa(client_bd):
    cuerpo = pedir(client_bd, "/api/v1/usuarios/me", usuario=2).get_json()
    assert cuerpo["perfil_completo"] is False


def test_predios_solo_los_propios(client_bd):
    nombres = [p["nombre"] for p in pedir(client_bd, "/api/v1/predios", usuario=1).get_json()["predios"]]
    assert nombres == ["Predio Ana"]


def test_predio_ajeno_es_404(client_bd):
    r = pedir(client_bd, "/api/v1/predios/2", usuario=1)
    assert r.status_code == 404 and r.get_json()["error"]["codigo"] == "NO_ENCONTRADO"


def test_predio_inexistente_igual_que_ajeno(client_bd):
    assert (
        pedir(client_bd, "/api/v1/predios/999", usuario=1).get_json()
        == pedir(client_bd, "/api/v1/predios/2", usuario=1).get_json()
    )


def test_admin_no_ve_predios_ajenos_por_este_camino(client_bd):
    assert pedir(client_bd, "/api/v1/predios", usuario=3, admin=True).get_json()["predios"] == []


def test_potreros_con_ha_y_m2(client_bd):
    p = pedir(client_bd, "/api/v1/predios/1/potreros", usuario=1).get_json()["potreros"][0]
    assert p["nombre"] == "Norte" and p["cultivo"] == "Trigo"
    assert p["superficie"]["hectareas"] == 1.5 and p["superficie"]["metros_cuadrados"] == 15000
    assert p["superficie"]["texto"] == "1,5 ha (15.000 m²)"
    assert p["tiene_perimetro"] is True and len(p["perimetro"]) == 3


def test_potrero_sin_perimetro(client_bd):
    p = pedir(client_bd, "/api/v1/potreros/2", usuario=2).get_json()
    assert p["tiene_perimetro"] is False and p["perimetro"] == []


def test_potrero_ajeno_404(client_bd):
    assert pedir(client_bd, "/api/v1/potreros/2", usuario=1).status_code == 404
    assert pedir(client_bd, "/api/v1/predios/2/potreros", usuario=1).status_code == 404


def test_archivados_no_se_entregan(client_bd, bd_path):
    """Un potrero o predio archivado en la plataforma deja de aparecer en la API (sus datos siguen en la base)."""
    con = sqlite3.connect(bd_path)
    con.execute("UPDATE core_potrero SET archivado_en = '2026-11-15 10:00:00' WHERE id = 1")
    con.commit()
    assert pedir(client_bd, "/api/v1/potreros/1", usuario=1).status_code == 404
    assert all(p["id"] != 1 for p in pedir(client_bd, "/api/v1/predios/1/potreros", usuario=1).get_json()["potreros"])
    con.execute("UPDATE core_predio SET archivado_en = '2026-11-15 10:00:00' WHERE id = 1")
    con.commit()
    con.close()
    assert pedir(client_bd, "/api/v1/predios", usuario=1).get_json()["predios"] == []
    assert pedir(client_bd, "/api/v1/predios/1", usuario=1).status_code == 404


def test_cultivos_solo_activos_y_estado_de_verificacion(client_bd):
    cultivos = pedir(client_bd, "/api/v1/cultivos").get_json()["cultivos"]
    assert [c["nombre"] for c in cultivos] == ["Trigo"]
    assert cultivos[0]["densidad"]["estado"] == "verificado"
    assert cultivos[0]["rendimiento"]["estado"] == "sin_verificar"
    assert pedir(client_bd, "/api/v1/cultivos/2").status_code == 404


def test_zonas_solo_activas(client_bd):
    assert [z["nombre"] for z in pedir(client_bd, "/api/v1/zonas").get_json()["zonas"]] == ["Melipilla"]


def test_insumos_marcados_sin_verificar(client_bd):
    i = pedir(client_bd, "/api/v1/insumos").get_json()["insumos"][0]
    assert i["verificacion"]["estado"] == "sin_verificar" and i["tipo"] == "Semilla"
    assert pedir(client_bd, "/api/v1/insumos/99").status_code == 404


def test_historial_solo_del_usuario(client_bd):
    h = pedir(client_bd, "/api/v1/calculos", usuario=1).get_json()["calculos"]
    assert len(h) == 1 and h[0]["potrero"] == "Norte" and h[0]["entrada"] == {"ha": 1.5}


def test_historial_filtra_por_potrero_ajeno_vacio(client_bd):
    assert pedir(client_bd, "/api/v1/calculos?potrero_id=2", usuario=1).get_json()["calculos"] == []


def test_la_api_no_puede_escribir_en_la_base(bd_path):
    con = sqlite3.connect(f"file:{bd_path}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        con.execute("DELETE FROM core_predio")
        escribio = True
    except sqlite3.OperationalError:
        escribio = False
    assert escribio is False


def test_metodos_de_escritura_siguen_sin_implementar(client_bd):
    r = pedir(client_bd, "/api/v1/predios", usuario=1, metodo="POST", json_body={"nombre": "x"})
    assert r.status_code == 501
    # Y no se creó nada:
    assert len(pedir(client_bd, "/api/v1/predios", usuario=1).get_json()["predios"]) == 1


def test_base_inexistente_da_503(tmp_path):
    from app import create_app
    from tests.helpers import SECRETO

    app = create_app(
        {"TESTING": True, "AGROASESOR_DB_PATH": str(tmp_path / "no.sqlite3"), "AGROASESOR_INTERNAL_SECRET": SECRETO}
    )
    r = pedir(app.test_client(), "/api/v1/predios")
    assert r.status_code == 503 and r.get_json()["error"]["codigo"] == "BASE_NO_DISPONIBLE"
