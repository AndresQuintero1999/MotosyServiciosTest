"""Casos tomados de modelo_interes_texto real en leads.csv."""
from src.catalogo import construir_indice, resolver_modelo

CATALOGO = [
    {"sku": "SKU-010", "marca": "Bajaj", "linea": "Pulsar RS 200"},
    {"sku": "SKU-018", "marca": "AKT", "linea": "Dynamic R3 125"},
    {"sku": "SKU-016", "marca": "Suzuki", "linea": "Best 125"},
    {"sku": "SKU-005", "marca": "Honda", "linea": "Dio 110"},
    {"sku": "SKU-003", "marca": "Honda", "linea": "CB 190R"},
    {"sku": "SKU-001", "marca": "Honda", "linea": "CB 125F Twister"},
]
INDICE = construir_indice(CATALOGO)


def test_match_exacto():
    sku, score = resolver_modelo("Bajaj Pulsar RS 200", INDICE)
    assert sku == "SKU-010"
    assert score > 95


def test_match_con_siglas_con_puntos():
    sku, score = resolver_modelo("A.K.T Dynamic R3 125", INDICE)
    assert sku == "SKU-018"
    assert score > 80


def test_match_minusculas():
    sku, score = resolver_modelo("suzuki best 125", INDICE)
    assert sku == "SKU-016"


def test_match_sin_marca_explicita():
    sku, score = resolver_modelo("honda dio 110", INDICE)
    assert sku == "SKU-005"


def test_texto_vacio_no_hace_match():
    sku, score = resolver_modelo("", INDICE)
    assert sku is None
    assert score == 0.0


def test_texto_no_relacionado_no_hace_match_falso():
    sku, score = resolver_modelo("quiero informacion general", INDICE)
    assert sku is None
