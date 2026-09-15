from src.dedup import LeadParaDedup, agrupar_duplicados


def _lead(lead_id, empresa_id, tel, nombre, fecha_registro, fecha_contacto=None):
    return LeadParaDedup(
        lead_id=lead_id,
        empresa_id=empresa_id,
        telefono_e164=tel,
        nombre=nombre,
        fecha_registro=fecha_registro,
        fecha_primer_contacto=fecha_contacto,
    )


def test_mismo_telefono_misma_empresa_se_consolida():
    leads = [
        _lead("LD-1", "EMP-01", "+573001111111", "Paula Castaño", "2026-08-22T21:55:00"),
        _lead("LD-2", "EMP-01", "+573001111111", "Paula Castaño Perez", "2026-09-05T20:16:00"),
    ]
    grupos = agrupar_duplicados(leads)
    assert grupos["LD-1"].lead_maestro_id == "LD-1"  # es el mas antiguo
    assert grupos["LD-2"].lead_maestro_id == "LD-1"
    assert set(grupos["LD-1"].miembros) == {"LD-1", "LD-2"}


def test_mismo_telefono_distinta_empresa_NO_se_consolida():
    """Caso critico: el mismo telefono en dos empresas del grupo NO debe
    fusionarse, porque violaria el aislamiento por comercializadora."""
    leads = [
        _lead("LD-1", "EMP-01", "+573001111111", "Julian Perez", "2026-08-22T08:52:00"),
        _lead("LD-2", "EMP-02", "+573001111111", "J. Perez Arias", "2026-08-30T02:11:00"),
    ]
    grupos = agrupar_duplicados(leads)
    assert grupos["LD-1"].lead_maestro_id == "LD-1"
    assert grupos["LD-2"].lead_maestro_id == "LD-2"
    assert grupos["LD-1"].miembros == ["LD-1"]
    assert grupos["LD-2"].miembros == ["LD-2"]


def test_maestro_es_el_mas_antiguo():
    leads = [
        _lead("LD-2", "EMP-01", "+573002222222", "Ana", "2026-09-01T00:00:00"),
        _lead("LD-1", "EMP-01", "+573002222222", "Ana", "2026-08-01T00:00:00"),
    ]
    grupos = agrupar_duplicados(leads)
    assert grupos["LD-1"].lead_maestro_id == "LD-1"
    assert grupos["LD-2"].lead_maestro_id == "LD-1"


def test_primer_contacto_del_grupo_es_el_mas_temprano():
    leads = [
        _lead("LD-1", "EMP-01", "+573003333333", "Ana", "2026-08-01T00:00:00", None),
        _lead("LD-2", "EMP-01", "+573003333333", "Ana", "2026-08-05T00:00:00", "2026-08-06T10:00:00"),
    ]
    grupos = agrupar_duplicados(leads)
    assert grupos["LD-1"].fecha_primer_contacto_maestro == "2026-08-06T10:00:00"


def test_sin_telefono_no_se_fusiona_con_nadie():
    leads = [
        _lead("LD-1", "EMP-01", None, "Carlos Zapata", "2026-08-01T00:00:00"),
        _lead("LD-2", "EMP-01", None, "Carlos Zapata", "2026-08-02T00:00:00"),
    ]
    grupos = agrupar_duplicados(leads)
    assert grupos["LD-1"].lead_maestro_id == "LD-1"
    assert grupos["LD-2"].lead_maestro_id == "LD-2"


def test_lead_sin_duplicados_es_su_propio_maestro():
    leads = [_lead("LD-1", "EMP-01", "+573004444444", "Ana", "2026-08-01T00:00:00")]
    grupos = agrupar_duplicados(leads)
    assert grupos["LD-1"].lead_maestro_id == "LD-1"
    assert grupos["LD-1"].miembros == ["LD-1"]
