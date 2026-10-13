from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text, update

from finance_agents.api.app import create_app
from finance_agents.core.settings import Settings
from finance_agents.storage.runtime_store import RuntimeStore, requests, runs, utcnow
from finance_agents.workflow.state import RunStatus, SessionState


@pytest.fixture
def store(tmp_path):
    return RuntimeStore(create_engine("sqlite:///" + str(tmp_path / "runtime.db")))


def state():
    return SessionState(run_id="run", user_id="user", session_id="session")


def test_registration_and_creation_rollback_together(store):
    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO runtime_runs"):
            raise RuntimeError("injected run write failure")

    event.listen(store.engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="injected"):
        store.begin_run_request(state(), "request", "payload")
    event.remove(store.engine, "before_cursor_execute", fail)
    with store.engine.connect() as connection:
        assert connection.execute(select(requests)).first() is None
        assert connection.execute(select(runs)).first() is None
    assert store.begin_run_request(state(), "request", "payload") is None


def test_final_response_failure_rolls_back_run_version(store):
    value = state()
    store.begin_run_request(value, "request", "payload")
    value.status = RunStatus.COMPLETED

    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE runtime_requests"):
            raise RuntimeError("injected response write failure")

    event.listen(store.engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="injected"):
        store.complete_run_request(value, "request", 1, {"run_id": "run"})
    event.remove(store.engine, "before_cursor_execute", fail)
    assert store.get_run("run", "user").state_version == 1
    assert store.get_run("run", "user").status == RunStatus.RECEIVED
    assert store.get_request("user", "request")["status"] == "processing"
    assert value.state_version == 1


def test_interruption_is_stale_only_and_rejects_late_result(store):
    value = state()
    store.begin_run_request(value, "request", "payload")
    reference = store.grant_consent("user", "run")
    with pytest.raises(ValueError, match="5"):
        store.interrupt_request("user", "request")
    with store.engine.begin() as connection:
        connection.execute(update(requests).values(updated_at=utcnow() - timedelta(minutes=6)))
    result = store.interrupt_request("user", "request")
    assert result["status"] == "interrupted"
    assert store.get_run("run", "user").status == RunStatus.CANCELLED
    assert not store.check_consent(reference, "user", "run")
    value.status = RunStatus.COMPLETED
    with pytest.raises(ValueError):
        store.complete_run_request(value, "request", 1, {"run_id": "run"})
    assert store.get_run("run", "user").status == RunStatus.CANCELLED


def test_existing_request_table_migrates_without_guessing_association(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "old.db"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE runtime_requests (user_id VARCHAR(64) NOT NULL, request_id VARCHAR(128) NOT NULL, payload_hash VARCHAR(64) NOT NULL, result JSON, UNIQUE(user_id, request_id))"
            )
        )
        connection.execute(text("INSERT INTO runtime_requests VALUES ('u','legacy','hash',NULL)"))
        connection.execute(
            text("INSERT INTO runtime_requests VALUES ('u','done','hash','{\"run_id\":\"old\"}')")
        )
        connection.execute(
            text("INSERT INTO runtime_requests VALUES ('u','jsonnull','hash','null')")
        )
        # Whitespace is valid JSON: its deserialized value is still None.
        connection.execute(
            text("INSERT INTO runtime_requests VALUES ('u','paddednull','hash',' null ')")
        )
    migrated = RuntimeStore(engine)
    row = migrated.get_request("u", "legacy")
    assert row["status"] == "interrupted"
    assert row["run_id"] is None
    assert row["created_at"] and row["updated_at"]
    assert RuntimeStore(engine).get_request("u", "legacy") == row
    assert migrated.get_request("u", "done")["status"] == "completed"
    assert migrated.get_request("u", "done")["run_id"] is None
    assert migrated.get_request("u", "jsonnull")["status"] == "interrupted"
    assert migrated.get_request("u", "paddednull")["status"] == "interrupted"
    with engine.connect() as connection:
        original_results = connection.execute(
            text("SELECT request_id, result FROM runtime_requests ORDER BY request_id")
        ).all()
    repeated = RuntimeStore(engine)
    assert repeated.get_request("u", "paddednull")["status"] == "interrupted"
    assert repeated.get_request("u", "done")["status"] == "completed"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT request_id, result FROM runtime_requests ORDER BY request_id")
            ).all()
            == original_results
        )


def test_resume_checkpoint_failure_does_not_consume_request(store):
    value = state()
    store.create_run(value)

    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE runtime_runs"):
            raise RuntimeError("injected checkpoint write failure")

    event.listen(store.engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="injected"):
        store.begin_run_request(value, "resume", "payload", expected_version=1)
    event.remove(store.engine, "before_cursor_execute", fail)
    with pytest.raises(LookupError):
        store.get_request("user", "resume")
    assert store.get_run("run", "user").state_version == 1


def test_interrupted_request_cannot_be_replayed(store):
    value = state()
    store.begin_run_request(value, "request", "payload")
    with store.engine.begin() as connection:
        connection.execute(update(requests).values(updated_at=utcnow() - timedelta(minutes=6)))
    store.interrupt_request("user", "request")
    with pytest.raises(ValueError, match="中断"):
        store.begin_run_request(value, "request", "payload")
    with pytest.raises(ValueError, match="关闭"):
        store.finish_request("user", "request", {"run_id": "run"})


def test_request_api_is_user_scoped(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, llm_enabled=False))) as client:
        alice = client.post("/api/v1/sessions").json()
        bob = client.post("/api/v1/sessions").json()
        headers = {"Authorization": "Bearer " + alice["token"]}
        run = client.post(
            "/api/v1/queries",
            headers=headers,
            json={
                "session_id": alice["session_id"],
                "request_id": "q",
                "message": "贷款申请",
                "intent_hint": "loan",
            },
        ).json()
        row = client.get("/api/v1/requests/q", headers=headers)
        assert row.status_code == 200
        assert row.json()["run_id"] == run["run_id"]
        assert row.json()["status"] == "completed"
        assert "payload_hash" not in row.json()
        assert (
            client.get(
                "/api/v1/requests/q", headers={"Authorization": "Bearer " + bob["token"]}
            ).status_code
            == 404
        )
        assert client.post("/api/v1/requests/q/interrupt", headers=headers).status_code == 409


@pytest.mark.parametrize("status", [RunStatus.CANCELLED, RunStatus.FAILED])
def test_terminal_run_cannot_invoke_late_mcp_tools(store, status):
    from finance_agents.contracts.common import AuthContext
    from finance_agents.mcp_services.transport import ScopedDispatcher

    user = store.create_session()
    value = SessionState(
        run_id="terminal", user_id=user["user_id"], session_id=user["session_id"], status=status
    )
    store.create_run(value)
    denied = ScopedDispatcher(store, {}).validate(
        "tax.search_rules",
        {},
        user["token"],
        value.run_id,
        AuthContext(user_id=user["user_id"], session_id=user["session_id"]),
    )
    assert denied is not None
    assert denied.error.code == "RUN_CLOSED"
