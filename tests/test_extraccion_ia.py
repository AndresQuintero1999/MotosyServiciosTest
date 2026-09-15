"""El extractor de reglas es el camino que corre en CI/pruebas sin llave de
API; se prueba contra texto tomado de conversaciones reales del dataset."""
from src.extraccion_ia import _extraer_reglas


def test_detecta_forma_pago_credito_y_monto_en_palos():
    texto = (
        "[10:37] cliente: Qué más pues, estoy averiguando por la Suzuki GN 125\n"
        "[11:24] cliente: Financiada. Tengo como 2 palos, ¿alcanza para la inicial?\n"
    )
    r = _extraer_reglas(texto)
    assert r["forma_pago"] == "credito"
    assert r["presupuesto_cop"] == 2_000_000


def test_detecta_objecion_credito_reportado():
    texto = "[11:39] cliente: Tengo un reporte viejo en centrales, ¿eso afecta?"
    r = _extraer_reglas(texto)
    assert r["objecion_principal"] == "credito_reportado"


def test_detecta_pidio_cotizacion_cuando_cliente_confirma_oferta_del_asesor():
    # El cliente no repite la palabra "cotizacion": confirma la oferta del
    # asesor. La heuristica debe leer el orden cronologico, no solo buscar
    # la palabra clave en cualquier parte del texto.
    texto = (
        "[12:07] asesor: ¿Le mando la cotización formal al WhatsApp?\n"
        "[12:25] cliente: Listo, envíemela\n"
    )
    r = _extraer_reglas(texto)
    assert r["pidio_cotizacion"] is True


def test_no_atribuye_al_cliente_precio_citado_por_el_asesor():
    # El asesor cita el precio de lista y pregunta forma de pago en su
    # primer mensaje; el cliente todavia no respondio nada. No debe
    # inventarse un presupuesto ni una forma de pago a partir de la
    # pregunta/oferta del asesor.
    texto = (
        "[04:27] asesor: La Bajaj Dominar 400 está en $24.900.000 más "
        "matrícula y SOAT. ¿La está buscando de contado o financiada?\n"
        "[04:31] cliente: Estoy es comparando por ahora\n"
    )
    r = _extraer_reglas(texto)
    assert r["presupuesto_cop"] is None
    assert r["forma_pago"] == "no_informa"


def test_detecta_intencion_comparando():
    texto = "[04:31] cliente: Estoy es comparando por ahora\n[09:24] cliente: Me están ofreciendo otra por menos plata"
    r = _extraer_reglas(texto)
    assert r["intencion"] == "comparando"
    assert r["objecion_principal"] == "comparando"


def test_monto_con_separador_de_miles():
    texto = "cliente: Tengo 1.500.000 de inicial de contado"
    r = _extraer_reglas(texto)
    assert r["presupuesto_cop"] == 1_500_000
    assert r["forma_pago"] == "contado"


def test_sin_senales_devuelve_valores_neutrales():
    texto = "cliente: Hola\nasesor: Hola, ¿en qué le ayudo?"
    r = _extraer_reglas(texto)
    assert r["forma_pago"] == "no_informa"
    assert r["objecion_principal"] == "ninguna"
    assert r["intencion"] == "curioseando"
    assert r["pidio_cita"] is False
