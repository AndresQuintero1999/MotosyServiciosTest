"""Casos tomados directamente del perfilado de leads.csv, no inventados."""
from src.normalizacion import (
    normalizar_canal,
    normalizar_ciudad,
    normalizar_estado_gestion,
    normalizar_fecha,
    normalizar_nombre,
    normalizar_telefono,
)


class TestTelefono:
    def test_formatos_validos_normalizan_igual(self):
        crudos = [
            "310 482 4081",
            " 3055411722 ",
            "+57 350 2258611",
            "3002859667",
            "573156610968",
            "322-624-9127",
            "(322) 624-9985",
            "+57 318 5408260",
        ]
        for c in crudos:
            e164, valido = normalizar_telefono(c)
            assert valido, f"{c!r} deberia ser valido"
            assert e164.startswith("+57")
            assert len(e164) == 13

    def test_mismo_numero_distintos_formatos_da_mismo_e164(self):
        a, _ = normalizar_telefono("3002859667")
        b, _ = normalizar_telefono("573002859667")
        c, _ = normalizar_telefono("+57 300 2859667")
        assert a == b == c == "+573002859667"

    def test_basura_es_invalida(self):
        e164, valido = normalizar_telefono("300123")
        assert not valido
        assert e164 is None

    def test_vacio_es_invalido(self):
        assert normalizar_telefono("") == (None, False)
        assert normalizar_telefono(None) == (None, False)


class TestFecha:
    def test_iso_con_t(self):
        r = normalizar_fecha("2026-08-15T11:08:00")
        assert r.iso == "2026-08-15T11:08:00"
        assert not r.ambigua

    def test_iso_con_espacio(self):
        r = normalizar_fecha("2026-08-26 20:28:00")
        assert r.iso == "2026-08-26T20:28:00"
        assert not r.ambigua

    def test_guion_es_dia_mes_anio_no_ambiguo(self):
        # segundo componente = 8 (mes), cae en rango observado -> confirmado
        r = normalizar_fecha("25-08-2026")
        assert r.iso == "2026-08-25T00:00:00"
        assert not r.ambigua

    def test_slash_primer_componente_mayor_a_12_fuerza_dd_mm(self):
        r = normalizar_fecha("22/08/2026 21:55")
        assert r.iso == "2026-08-22T21:55:00"
        assert not r.ambigua

    def test_slash_segundo_componente_mayor_a_12_fuerza_mm_dd(self):
        # 08/30/2026 -> el "30" no puede ser mes: es MM/DD -> 30 de agosto
        r = normalizar_fecha("08/30/2026 02:11")
        assert r.iso == "2026-08-30T02:11:00"
        assert not r.ambigua

    def test_slash_ambiguo_ambos_componentes_meses_validos(self):
        # 08/09 podria ser 8-sep o 9-ago: ambas caen en el rango observado.
        r = normalizar_fecha("08/09/2026 10:00")
        assert r.ambigua
        assert r.iso is not None  # se asume DD/MM (convencion CO) pero se marca

    def test_vacio(self):
        r = normalizar_fecha("")
        assert r.iso is None
        assert not r.ambigua


class TestCiudad:
    def test_variantes_bogota_convergen(self):
        variantes = ["Bogotá D.C.", "Bogotá", "Bogota", "BOGOTA", "bogotá", "Bogota DC"]
        assert {normalizar_ciudad(v) for v in variantes} == {"Bogotá"}

    def test_abreviaturas(self):
        assert normalizar_ciudad("B/quilla") == "Barranquilla"
        assert normalizar_ciudad("Sta Marta") == "Santa Marta"
        assert normalizar_ciudad("Rio Negro") == "Rionegro"

    def test_vacio_es_none(self):
        assert normalizar_ciudad("") is None
        assert normalizar_ciudad(None) is None


class TestCanal:
    def test_variantes_convergen(self):
        assert normalizar_canal("WHATSAPP") == "WhatsApp"
        assert normalizar_canal("whatsapp") == "WhatsApp"
        assert normalizar_canal("WhatsApp") == "WhatsApp"
        assert normalizar_canal("META ADS") == "Meta Ads"
        assert normalizar_canal("meta ads") == "Meta Ads"
        assert normalizar_canal("FORMULARIO WEB") == "Formulario Web"
        assert normalizar_canal("formulario web") == "Formulario Web"


class TestEstadoGestion:
    def test_variantes_convergen(self):
        assert normalizar_estado_gestion("sin gestion") == "Sin gestión"
        assert normalizar_estado_gestion("SIN GESTION") == "Sin gestión"
        assert normalizar_estado_gestion("contactado") == "Contactado"
        assert normalizar_estado_gestion("no contesta") == "No contesta"


class TestNombre:
    def test_colapsa_espacios(self):
        assert normalizar_nombre("  Wilmar Castaño Rodríguez  ") == "Wilmar Castaño Rodríguez"
