"""Reject meaningless identifiers through contracts and the authenticated API."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from finance_agents.api.app import create_app
from finance_agents.contracts.profiles import ClaimForm, CreditForm
from finance_agents.core.settings import Settings


CASES = [
    (CreditForm, "loan", "subject_ref", 128),
    (CreditForm, "loan", "employment_status", 128),
    (ClaimForm, "claim", "policy_id", 128),
    (ClaimForm, "claim", "policy_version", 128),
]


@pytest.mark.parametrize("model,intent,field,limit", CASES)
@pytest.mark.parametrize("kind", ["blank", "too_long"])
def test_identifier_contract_rejects_invalid_text(model, intent, field, limit, kind):
    value = " \t\n " if kind == "blank" else "字" * (limit + 1)
    with pytest.raises(ValidationError):
        model(**{field: value})


@pytest.mark.parametrize("model,intent,field,limit", CASES)
def test_identifier_contract_accepts_bounded_text_and_unknown_employment(model, intent, field, limit):
    assert getattr(model(**{field: "字" * limit}), field) == "字" * limit
    assert CreditForm(employment_status="自由职业 / 灵活就业").employment_status == "自由职业 / 灵活就业"


@pytest.fixture
def isolated_client(tmp_path):
    # Do not inherit DATABASE_URL or model credentials from a developer's environment.
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'negative.db').as_posix()}",
        llm_enabled=False,
        mcp_transport="inprocess",
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize("model,intent,field,limit", CASES)
@pytest.mark.parametrize("kind", ["blank", "too_long"])
def test_direct_api_rejects_invalid_confirmed_text(isolated_client, model, intent, field, limit, kind):
    client = isolated_client
    identity = client.post("/api/v1/sessions").json()
    headers = {"Authorization": "Bearer " + identity["token"]}
    run = client.post(
        "/api/v1/queries", headers=headers,
        json={"session_id": identity["session_id"], "request_id": "start", "message": "申请",
              "intent_hint": intent},
    ).json()
    response = client.post(
        f"/api/v1/runs/{run['run_id']}/resume", headers=headers,
        json={"request_id": "invalid", "state_version": run["state_version"], "message": "确认字段",
              "confirmed_fields": {field: " \t\n " if kind == "blank" else "字" * (limit + 1)}},
    )
    assert response.status_code == 422, response.text
    latest = client.get(f"/api/v1/runs/{run['run_id']}", headers=headers).json()
    assert latest["form"] == run["form"]
