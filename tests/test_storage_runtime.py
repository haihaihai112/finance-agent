from datetime import timedelta

import pytest
from sqlalchemy import create_engine


def make_store():
    from finance_agents.storage.runtime_store import RuntimeStore

    return RuntimeStore(create_engine("sqlite+pysqlite:///:memory:"))


def test_tokens_are_server_generated_and_scope_isolated():
    store = make_store()
    alice = store.create_session()
    bob = store.create_session()
    assert alice["user_id"] != bob["user_id"]
    assert store.authenticate(alice["token"])["user_id"] == alice["user_id"]
    with pytest.raises(PermissionError):
        store.authenticate("made-up-token")


def test_run_cas_and_cross_user_access():
    from finance_agents.workflow.state import SessionState

    store = make_store()
    user = store.create_session()
    state = SessionState(run_id="r", user_id=user["user_id"], session_id=user["session_id"])
    store.create_run(state)
    state.message = "first"
    store.save_run(state, expected_version=1)
    assert store.get_run("r", user["user_id"]).state_version == 2
    with pytest.raises(ValueError, match="版本"):
        store.save_run(state, expected_version=1)
    with pytest.raises(PermissionError):
        store.get_run("r", "someone-else")


def test_consent_is_bound_to_user_run_and_revocation():
    from finance_agents.workflow.state import SessionState

    store = make_store()
    user = store.create_session()
    store.create_run(
        SessionState(run_id="r", user_id=user["user_id"], session_id=user["session_id"])
    )
    consent = store.grant_consent(user["user_id"], "r")
    assert store.check_consent(consent, user["user_id"], "r")
    assert not store.check_consent(consent, "someone-else", "r")
    assert not store.check_consent(consent, user["user_id"], "different")
    store.revoke_consent(consent, user["user_id"])
    assert not store.check_consent(consent, user["user_id"], "r")
    expired = store.grant_consent(user["user_id"], "r", lifetime=timedelta(seconds=-1))
    assert not store.check_consent(expired, user["user_id"], "r")


def test_idempotency_conflicts_on_different_payload():
    store = make_store()
    assert store.begin_request("u", "k", "payload") is None
    store.finish_request("u", "k", {"run_id": "r"})
    assert store.begin_request("u", "k", "payload") == {"run_id": "r"}
    with pytest.raises(ValueError, match="请求"):
        store.begin_request("u", "k", "different-payload")
