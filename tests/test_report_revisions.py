import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from finance_agents.api.app import create_app
from finance_agents.core.settings import Settings
from finance_agents.contracts.common import AuthContext
from finance_agents.mcp_services.tax import TaxMCPService
from finance_agents.storage.policy_store import PolicyStore


@pytest.mark.parametrize("income_type", ["annual_comprehensive_income", "年度综合所得"])
def test_annual_income_alias_calculates_verified_tax(income_type):
    store = PolicyStore(create_engine("sqlite://"))
    store.seed_verified_rules()
    result = asyncio.run(
        TaxMCPService(store).call(
            "tax.calculate",
            {
                "jurisdiction": "CN",
                "tax_year": 2026,
                "currency": "CNY",
                "resident_status": "resident",
                "income_type": income_type,
                "annual_income": "120000",
                "annual_deductions": "30000",
            },
            AuthContext(user_id="u", session_id="s"),
        )
    )
    assert result.status == "success"
    assert result.data["annual_tax"] == "900.00"
    assert result.evidence


def test_bonus_scope_explained_before_requesting_annual_fields():
    from finance_agents.agents.tax_policy import TaxPolicyAgent
    from finance_agents.workflow.state import SessionState

    class NoTool:
        async def call(self, *args):
            pytest.fail("Unsupported bonus must not call annual calculation")

    result = asyncio.run(
        TaxPolicyAgent(NoTool()).run(
            SessionState(run_id="r", user_id="u", session_id="s", message="年终奖单独计税怎么算")
        )
    )
    assert result.status == "unavailable"
    assert "年终奖" in result.summary
    assert "新建" in result.next_action


def test_api_field_examples_and_recoverable_invalid_money(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, llm_enabled=False))) as client:
        session = client.post("/api/v1/sessions").json()
        headers = {"Authorization": "Bearer " + session["token"]}
        value = client.post(
            "/api/v1/queries",
            headers=headers,
            json={
                "session_id": session["session_id"],
                "request_id": "start",
                "message": "年度税额试算",
                "intent_hint": "policy_tax",
            },
        ).json()
        assert value["field_hints"]["annual_income"]["example"] == {
            "amount": "120000.00",
            "currency": "CNY",
        }
        response = client.post(
            f"/api/v1/runs/{value['run_id']}/resume",
            headers=headers,
            json={
                "request_id": "bad",
                "state_version": value["state_version"],
                "message": "确认",
                "confirmed_fields": {"annual_income": 120000},
            },
        )
        assert response.status_code == 422
        assert response.json()["field_hints"]["annual_income"]["type"] == "money"
        assert "120000" not in response.json()["detail"]
        unchanged = client.get(f"/api/v1/runs/{value['run_id']}", headers=headers).json()
        assert unchanged["state_version"] == value["state_version"]


@pytest.mark.parametrize(
    "fields,extra",
    [
        ({}, {"password": "synthetic-sensitive-marker"}),
        ({"synthetic-sensitive-marker": "value"}, {}),
    ],
)
def test_api_invalid_request_does_not_echo_unknown_input(tmp_path, fields, extra):
    with TestClient(create_app(Settings(data_dir=tmp_path, llm_enabled=False))) as client:
        identity = client.post("/api/v1/sessions").json()
        headers = {"Authorization": "Bearer " + identity["token"]}
        run = client.post(
            "/api/v1/queries",
            headers=headers,
            json={
                "session_id": identity["session_id"],
                "request_id": "start",
                "message": "年度税额试算",
                "intent_hint": "policy_tax",
            },
        ).json()
        response = client.post(
            f"/api/v1/runs/{run['run_id']}/resume",
            headers=headers,
            json={
                "request_id": "bad",
                "state_version": run["state_version"],
                "message": "确认",
                "confirmed_fields": fields,
                **extra,
            },
        )
        assert response.status_code == 422
        assert "synthetic-sensitive-marker" not in response.text
