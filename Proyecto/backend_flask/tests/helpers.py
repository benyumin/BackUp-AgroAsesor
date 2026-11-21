"""Ayudas de prueba: base SQLite con el esquema de Django y cabeceras firmadas como las emite Django."""

import sqlite3
import time
from pathlib import Path

from app.security.gateway import firmar

SECRETO = "s" * 40
ESQUEMA = Path(__file__).with_name("schema_django.sql")


def crear_bd(ruta):
    """Crea una base de PRUEBA (jamás la real) con 2 agricultores, predios, potreros, cultivos e insumos."""
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    ahora = "2026-10-01 10:00:00"
    con.executemany(
        "INSERT INTO auth_user (id,password,is_superuser,username,last_name,email,is_staff,is_active,date_joined,first_name)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "x", 0, "ana", "Pérez", "ana@example.com", 0, 1, ahora, "Ana"),
            (2, "x", 0, "luis", "", "luis@example.com", 0, 1, ahora, ""),
            (3, "x", 1, "admin", "", "admin@example.com", 1, 1, ahora, ""),
            (4, "x", 0, "baja", "", "baja@example.com", 0, 0, ahora, ""),
        ],
    )
    con.execute(
        "INSERT INTO core_agricultor (telefono,comuna,user_id) VALUES ('','Melipilla',1)"
    )
    con.execute(
        "INSERT INTO core_zona (id,nombre,descripcion,activo) VALUES (1,'Melipilla','Zona central',1),(2,'Inactiva','',0)"
    )
    con.execute(
        "INSERT INTO core_cultivo (id,nombre,descripcion,rendimiento_ton_ha,densidad_siembra,unidad_siembra,activo,"
        "densidad_fuente,densidad_url,densidad_verificada,rendimiento_fuente,rendimiento_url,rendimiento_verificado)"
        " VALUES (1,'Trigo','',6.5,180,'kg/ha',1,'INIA','https://example.org/a','2026-10-01','',  '',NULL),"
        "(2,'Cultivo viejo','',NULL,NULL,'kg/ha',0,'','',NULL,'','',NULL)"
    )
    con.execute(
        "INSERT INTO core_predio (id,nombre,ubicacion,superficie_ha,creado,propietario_id,zona_id,geojson,latitud,longitud,"
        "actualizado,descripcion,observaciones) VALUES (1,'Predio Ana','Melipilla',10.5,?,1,1,NULL,-33.7,-71.2,?,'','')",
        (ahora, ahora),
    )
    con.execute(
        "INSERT INTO core_predio (id,nombre,ubicacion,superficie_ha,creado,propietario_id,zona_id,geojson,latitud,longitud,"
        "actualizado,descripcion,observaciones) VALUES (2,'Predio Luis','Talagante',3,?,2,1,NULL,NULL,NULL,?,'','')",
        (ahora, ahora),
    )
    con.executemany(
        "INSERT INTO core_potrero (id,nombre,variedad,fecha_siembra,fecha_cosecha,superficie_ha,perimetro,latitud,longitud,"
        "creado,actualizado,cultivo_id,predio_id,geojson) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
        [
            (
                1,
                "Norte",
                "Invierno",
                "2026-05-10",
                None,
                1.5,
                "[[-33.7,-71.2],[-33.7,-71.1],[-33.6,-71.1]]",
                -33.7,
                -71.2,
                ahora,
                ahora,
                1,
                1,
            ),
            (2, "Sur", "", None, None, 2, "[]", None, None, ahora, ahora, None, 2),
        ],
    )
    con.execute("INSERT INTO core_tipoinsumo (id,nombre) VALUES (1,'Semilla')")
    con.execute(
        "INSERT INTO core_insumo (id,nombre,descripcion,dosis_hectarea,unidad_dosis,vigente,cultivo_id,tipo_id,zona_id,"
        "advertencia_polinizadores,composicion_nutricional,concentracion,cultivos_autorizados,dosis_fuente,dosis_verificada,"
        "estado_verificacion,etiqueta_url,fabricante,formulacion,fuente_nombre,fuente_url,ingrediente_activo,"
        "numero_autorizacion_sag,objetivos_control,precauciones_ambientales,presentacion_unidad,restricciones,"
        "tiempo_reingreso,tipo_semilla,variedad_semilla,unidad_dosis_maxima,modo_aplicacion,periodo_carencia,hoja_seguridad_url)"
        " VALUES (1,'Semilla trigo',  '',180,'kg/ha',1,1,1,NULL,'','','','[]','',0,'sin_verificar','','','','','','','','[]','','','','','','','','','','')"
    )
    con.executemany(
        "INSERT INTO core_calculoagricola (operacion,entrada,resultado,creado,potrero_id,usuario_id) VALUES (?,?,?,?,?,?)",
        [
            ("semillas", '{"ha":1.5}', '{"kg":270}', "2026-10-02 09:00:00", 1, 1),
            ("semillas", '{"ha":2}', '{"kg":360}', "2026-10-03 09:00:00", 2, 2),
        ],
    )
    con.commit()
    con.close()
    return str(ruta)


def cabeceras(
    usuario_id, es_admin=False, metodo="GET", destino="/api/v1/health", cuerpo=b"", secreto=SECRETO, marca=None
):
    marca = int(time.time()) if marca is None else marca
    return {
        "X-AgroAsesor-User": str(usuario_id),
        "X-AgroAsesor-Staff": "1" if es_admin else "0",
        "X-AgroAsesor-Timestamp": str(marca),
        "X-AgroAsesor-Signature": firmar(secreto, usuario_id, es_admin, marca, metodo, destino, cuerpo),
    }


def pedir(client, destino, usuario=1, admin=False, metodo="GET", json_body=None, **kw):
    import json as _j

    cuerpo = _j.dumps(json_body).encode() if json_body is not None else b""
    h = cabeceras(usuario, admin, metodo, destino, cuerpo, **kw)
    if json_body is not None:
        h["Content-Type"] = "application/json"
    return client.open(destino, method=metodo, data=cuerpo or None, headers=h)
