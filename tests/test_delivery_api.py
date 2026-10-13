import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, text, update

from finance_agents.api.app import create_app
from finance_agents.core.settings import Settings
from finance_agents.contracts.results import FinalResponse
from finance_agents.storage.runtime_store import RuntimeStore, events, runs
from finance_agents.workflow.state import SessionState


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, llm_enabled=False))) as client:
        yield client


def identity(client):
    session = client.post("/api/v1/sessions").json()
    return session, {"Authorization": "Bearer " + session["token"]}


def seed(client, session, run_id, result=False):
    state = SessionState(
        run_id=run_id,
        user_id=session["user_id"],
        session_id=session["session_id"],
        intent="loan",
        status="partial",
        consent_ref="private-consent",
    )
    if result:
        state.final_response = FinalResponse(
            conclusion="模拟评估",
            data_mode="mock",
            limitations=["模拟规则，不代表真实授信"],
            facts={
                "structured_facts": {
                    "form": {"consent_ref": "private-consent"},
                    "assessment": {"debt_service_ratio": "0.2500", "token": "private-token"},
                },
                "calculations": {"amount": "9007199254740993.01"},
            },
            citations=[
                {
                    "source_id": "demo",
                    "source_title": "模拟来源",
                    "retrieved_at": datetime(2026, 10, 13),
                    "quote": "规则说明",
                }
            ],
        )
    client.app.state.service.store.create_run(state)
    return state


def test_latest_first_paging_is_stable_and_owned(client):
    session, headers = identity(client)
    for run_id in ["z-first", "a-second", "m-third"]:
        seed(client, session, run_id)
    other, _ = identity(client)
    seed(client, other, "other")
    first = client.get("/api/v1/runs?limit=2", headers=headers).json()
    assert [r["run_id"] for r in first] == ["m-third", "a-second"]
    assert [
        r["run_id"] for r in client.get("/api/v1/runs?limit=2&offset=2", headers=headers).json()
    ] == ["z-first"]
    assert client.get("/api/v1/runs?offset=99", headers=headers).json() == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=no", "offset=1.5"])
def test_invalid_pagination(client, query):
    _, headers = identity(client)
    assert client.get("/api/v1/runs?" + query, headers=headers).status_code == 422


@pytest.mark.parametrize("format", ["json", "markdown"])
def test_export_is_owned_whitelisted_exact_and_read_only(client, monkeypatch, format):
    session, headers = identity(client)
    state = seed(client, session, "report", True)

    def forbidden(*args, **kwargs):
        raise AssertionError("read route invoked workflow")

    monkeypatch.setattr(client.app.state.service, "start", forbidden)
    monkeypatch.setattr(client.app.state.service, "resume", forbidden)
    before = client.app.state.service.store.get_run(state.run_id, session["user_id"]).model_dump()
    endpoint = "/api/v1/runs/report/export?format=" + format
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 200
    assert "attachment;" in response.headers["content-disposition"]
    for value in ["9007199254740993.01", "0.2500", "模拟来源", "模拟规则", "mock"]:
        assert value in response.text
    for value in ["private-consent", "private-token", "consent_ref", '"form"', session["token"]]:
        assert value not in response.text
    if format == "json":
        assert set(response.json()) == {
            "run_id",
            "parent_run_id",
            "intent",
            "status",
            "state_version",
            "result",
            "data_scope",
        }
    assert (
        client.app.state.service.store.get_run(state.run_id, session["user_id"]).model_dump()
        == before
    )
    assert client.get(endpoint).status_code == 401
    _, other = identity(client)
    assert client.get(endpoint, headers=other).status_code == 403


def test_export_missing_result_and_invalid_format(client):
    session, headers = identity(client)
    seed(client, session, "empty")
    assert client.get("/api/v1/runs/empty/export", headers=headers).status_code == 409
    assert client.get("/api/v1/runs/empty/export?format=html", headers=headers).status_code == 422
    assert client.get("/api/v1/runs/unknown/export", headers=headers).status_code == 404


def test_legacy_creation_order_migration_is_idempotent(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    state = SessionState(run_id="legacy", user_id="u", session_id="s").model_dump(mode="json")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE runtime_runs (run_id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL, version INTEGER NOT NULL, state JSON NOT NULL)"
            )
        )
        conn.execute(
            text("INSERT INTO runtime_runs VALUES (:id, :u, 1, :state)"),
            {"id": "legacy", "u": "u", "state": json.dumps(state)},
        )
        conn.execute(
            text("INSERT INTO runtime_runs VALUES (:id, :u, 1, :state)"),
            {"id": "unknown", "u": "u", "state": json.dumps({**state, "run_id": "unknown"})},
        )
    events.create(engine)
    with engine.begin() as conn:
        for index, date in enumerate([datetime(2025, 1, 2), datetime(2025, 1, 1)]):
            conn.execute(
                insert(events).values(
                    event_id=str(index),
                    run_id="legacy",
                    event_type="created",
                    created_at=date,
                    payload={},
                )
            )
    store = RuntimeStore(engine)
    store.create_run(SessionState(run_id="new", user_id="u", session_id="s"))
    RuntimeStore(engine)
    assert [r["run_id"] for r in store.list_runs("u")] == ["new", "legacy", "unknown"]
    with engine.connect() as conn:
        assert conn.execute(
            select(runs.c.created_at).where(runs.c.run_id == "legacy")
        ).scalar_one() == datetime(2025, 1, 1)
        assert conn.execute(
            select(runs.c.created_at).where(runs.c.run_id == "unknown")
        ).scalar_one() == datetime(1970, 1, 1)


def test_registration_copy_save_and_equal_time_order(client):
    session, headers = identity(client)
    store = client.app.state.service.store
    first = seed(client, session, "first")
    second = SessionState(
        run_id="second",
        parent_run_id="first",
        user_id=session["user_id"],
        session_id=session["session_id"],
        intent="loan",
    )
    store.begin_run_request(second, "copy-request", "{}")
    assert [r["run_id"] for r in client.get("/api/v1/runs", headers=headers).json()] == [
        "second",
        "first",
    ]
    store.save_run(first, first.state_version)
    assert [r["run_id"] for r in store.list_runs(session["user_id"])] == ["second", "first"]
    with store.engine.begin() as conn:
        conn.execute(update(runs).values(created_at=datetime(2026, 1, 1)))
    assert [r["run_id"] for r in store.list_runs(session["user_id"])] == ["second", "first"]
