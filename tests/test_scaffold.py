"""启动、契约和架构边界，替代先前501骨架验收。"""

import pytest
from fastapi.testclient import TestClient
from finance_agents.api.app import create_app
from finance_agents.core.settings import Settings


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, llm_enabled=False))) as client:
        yield client


def test_health_reports_external_validation_limits(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["stage"] == "personal_project"
    assert response.json()["external_services_verified"] is False


def test_query_requires_server_session(client):
    response = client.post(
        "/api/v1/queries",
        json={"session_id": "untrusted", "request_id": "q", "message": "我有十万元，怎么投资？"},
    )
    assert response.status_code == 401
    assert "conclusion" not in response.json()


@pytest.mark.parametrize("message", ["", "   "])
def test_empty_input_rejected(client, message):
    session = client.post("/api/v1/sessions").json()
    response = client.post(
        "/api/v1/queries",
        headers={"Authorization": "Bearer " + session["token"]},
        json={"session_id": session["session_id"], "request_id": "q", "message": message},
    )
    assert response.status_code == 422


def test_client_cannot_assign_trusted_identity(client):
    session = client.post("/api/v1/sessions").json()
    response = client.post(
        "/api/v1/queries",
        headers={"Authorization": "Bearer " + session["token"]},
        json={
            "session_id": session["session_id"],
            "request_id": "q",
            "message": "保单",
            "user_id": "someone-else",
        },
    )
    assert response.status_code == 422


def test_resume_missing_run_is_not_completion(client):
    session = client.post("/api/v1/sessions").json()
    response = client.post(
        "/api/v1/runs/missing/resume",
        headers={"Authorization": "Bearer " + session["token"]},
        json={"request_id": "r", "state_version": 1, "message": "继续"},
    )
    assert response.status_code == 404


def test_workbench_and_openapi_available(client):
    assert client.get("/").status_code == 200
    assert client.get("/web/app.js").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/queries" in paths
    assert "/api/v1/runs/{run_id}/resume" in paths


def test_architecture_preserves_five_intents_six_agents_four_mcp(client):
    architecture = client.get("/api/v1/architecture").json()
    assert set(architecture["intents"]) == {"investment", "claim", "loan", "policy_tax", "other"}
    assert len(architecture["agents"]) == 6
    assert len(architecture["mcp_services"]) == 4
    assert "confirm_credit_form" in architecture["branches"]["loan"]
