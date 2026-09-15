"""Pruebas de integración contra la BD ya poblada por `python -m src.pipeline`.

Se corren después del pipeline (ver README / CI), no generan datos por sí
solas: si `data/warehouse/leads.db` no existe, se saltan.
"""
import pytest
from fastapi.testclient import TestClient

from api.main import app
from src.config import CFG

pytestmark = pytest.mark.skipif(not CFG.db_path.exists(), reason="Corra el pipeline primero")

cliente = TestClient(app)
TOKEN_EMP_01 = "demo-token-emp-01"
TOKEN_EMP_02 = "demo-token-emp-02"
TOKEN_ADMIN = "demo-admin-token"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_sin_token_devuelve_401():
    r = cliente.get("/leads/hoy")
    assert r.status_code == 401


def test_token_invalido_devuelve_401():
    r = cliente.get("/leads/hoy", headers=_auth("token-que-no-existe"))
    assert r.status_code == 401


def test_con_token_valido_devuelve_leads():
    r = cliente.get("/leads/hoy", headers=_auth(TOKEN_EMP_01))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] > 0
    assert all(l["lead_id"] for l in data["leads"])


def test_aislamiento_entre_empresas_es_estricto():
    """El hallazgo central del proyecto: ninguna empresa puede ver leads de
    otra, ni siquiera coincidencias de telefono cross-empresa."""
    r1 = cliente.get("/leads/hoy", headers=_auth(TOKEN_EMP_01)).json()
    r2 = cliente.get("/leads/hoy", headers=_auth(TOKEN_EMP_02)).json()
    ids1 = {l["lead_id"] for l in r1["leads"]}
    ids2 = {l["lead_id"] for l in r2["leads"]}
    assert ids1.isdisjoint(ids2)
    assert len(ids1) > 0 and len(ids2) > 0


def test_no_se_puede_pedir_otra_empresa_por_query_param():
    """empresa_id nunca debe leerse de la query string, solo del token."""
    r = cliente.get("/leads/hoy?empresa_id=EMP-02", headers=_auth(TOKEN_EMP_01))
    assert r.status_code == 200
    for lead in r.json()["leads"]:
        assert lead["empresa_id"] == "EMP-01"


def test_resumen_devuelve_conteos_coherentes():
    r = cliente.get("/leads/hoy/resumen", headers=_auth(TOKEN_EMP_01))
    assert r.status_code == 200
    data = r.json()
    assert data["total_leads_hoy"] == sum(data["por_temperatura"].values())


def test_admin_sin_token_devuelve_401():
    assert cliente.get("/admin/corridas").status_code == 401


def test_admin_con_token_devuelve_corridas():
    r = cliente.get("/admin/corridas", headers=_auth(TOKEN_ADMIN))
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_token_de_empresa_no_sirve_para_admin():
    r = cliente.get("/admin/corridas", headers=_auth(TOKEN_EMP_01))
    assert r.status_code == 401


def test_salud_responde():
    r = cliente.get("/salud")
    assert r.status_code == 200
    assert r.json()["db_existe"] is True
