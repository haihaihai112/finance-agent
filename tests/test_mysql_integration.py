"""Dedicated MySQL acceptance: real API/stdio/CAS and transactional rollback.

Opt-in MYSQL_TEST_URL only; synthetic rows retained, no deletion or DSN fallback.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import os
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import make_url

from finance_agents.api.app import create_app
from finance_agents.contracts.common import AuthContext
from finance_agents.core.settings import Settings
from finance_agents.data_jobs.models import MarketRecord
from finance_agents.data_jobs.pipeline import MarketIngestionPipeline
from finance_agents.mcp_services.transport import ScopedMCPClient
from finance_agents.workflow.service import WorkflowService
from finance_agents.workflow.state import SessionState


pytestmark = pytest.mark.skipif(
    not os.getenv("MYSQL_TEST_URL"), reason="未配置真实MySQL专用测试库；未验证"
)


@pytest.fixture
def mysql_settings(tmp_path):
    dsn = os.environ["MYSQL_TEST_URL"]
    try:
        url = make_url(dsn)
    except Exception:
        pytest.fail("MYSQL_TEST_URL invalid; URL omitted", pytrace=False)
    if url.get_backend_name() != "mysql" or not url.database:
        pytest.fail("MYSQL_TEST_URL must name a dedicated MySQL database", pytrace=False)
    return Settings(data_dir=tmp_path, database_url=dsn, llm_enabled=False, mcp_transport="stdio")


@pytest.fixture
def mysql_service(mysql_settings):
    try:
        service = WorkflowService(mysql_settings)
    except Exception as error:
        pytest.fail(
            f"MySQL initialization failed ({type(error).__name__}); details omitted", pytrace=False
        )
    try:
        assert service.engine.dialect.name == "mysql"
        yield service
    finally:
        service.engine.dispose()


def test_mysql_real_api_loan_confirm_authorize_stdio_and_isolation(mysql_settings):
    # Real HTTP/DB/MCP subprocess, while financial assessment remains mock.
    with TestClient(create_app(mysql_settings)) as client:
        session = client.post("/api/v1/sessions").json()
        headers = {"Authorization": "Bearer " + session["token"]}
        response = client.post(
            "/api/v1/queries",
            headers=headers,
            json={
                "session_id": session["session_id"],
                "request_id": uuid4().hex,
                "message": "申请贷款",
                "intent_hint": "loan",
            },
        )
        assert response.status_code == 200
        run = response.json()
        assert run["wait_reason"] == "upload_documents"
        demo_response = client.post("/api/v1/demo", headers=headers)
        assert demo_response.status_code == 200
        demo = demo_response.json()

        def resume(**extra):
            nonlocal run
            payload = {
                "request_id": uuid4().hex,
                "state_version": run["state_version"],
                "message": "继续",
                **extra,
            }
            response = client.post(
                f"/api/v1/runs/{run['run_id']}/resume", headers=headers, json=payload
            )
            assert response.status_code == 200
            run = response.json()

        resume(attachment_ids=[demo["documents"][1]["document_id"]])
        assert run["status"] == "waiting_user" and run["pending_fields"]
        extracted = client.app.state.service.documents.get_extraction(
            session["user_id"], demo["documents"][1]["document_id"]
        )
        assert extracted is not None and extracted.fields
        resume(
            confirmed_fields={
                "subject_ref": "synthetic-applicant",
                "requested_amount": {"amount": "12000", "currency": "CNY"},
                "loan_term_months": 12,
                "monthly_income": {"amount": "8000", "currency": "CNY"},
                "monthly_debt_payment": {"amount": "1000", "currency": "CNY"},
                "employment_status": "employed",
            }
        )
        assert run["wait_reason"] == "consent"
        consent = client.post(
            f"/api/v1/runs/{run['run_id']}/consent", headers=headers, json={"granted": True}
        )
        assert consent.status_code == 200
        resume(consent_ref=consent.json()["consent_ref"])
        assert run["status"] == "completed" and run["result"]["data_mode"] == "mock"
        assert (
            run["result"]["facts"]["structured_facts"]["assessment"]["debt_service_ratio"]
            == "0.2500"
        )
        other = client.post("/api/v1/sessions").json()
        other_headers = {"Authorization": "Bearer " + other["token"]}
        assert client.get(f"/api/v1/runs/{run['run_id']}", headers=other_headers).status_code == 403
        assert (
            client.get(
                f"/api/v1/documents/{demo['documents'][0]['document_id']}", headers=other_headers
            ).status_code
            == 403
        )


def test_mysql_real_stdio_reads_shared_db_and_client_preflight_rejects_forgery(mysql_service):
    service = mysql_service
    session = service.store.create_session()
    state = SessionState(
        run_id=uuid4().hex, user_id=session["user_id"], session_id=session["session_id"]
    )
    service.store.create_run(state)
    service.claims.seed_demo(state.user_id)
    client = ScopedMCPClient(
        service.settings, service.store, session["token"], state, service.services
    )
    auth = AuthContext(user_id=state.user_id, session_id=state.session_id)
    result = asyncio.run(client.call("claims.lookup_policies", {}, auth))
    assert result.status == "success" and result.data_mode == "mock"
    assert result.data["policies"][0]["policy_id"] == "DEMO-POLICY-001"
    forged = auth.model_copy(update={"user_id": uuid4().hex})
    # ScopedMCPClient rejects this before launching stdio: client preflight proof.
    rejected = asyncio.run(client.call("claims.lookup_policies", {}, forged))
    assert rejected.status == "failed" and rejected.error.code == "FORBIDDEN"


def test_mysql_competing_cas_has_exactly_one_committed_writer(mysql_service):
    service = mysql_service
    session = service.store.create_session()
    state = SessionState(
        run_id=uuid4().hex, user_id=session["user_id"], session_id=session["session_id"]
    )
    service.store.create_run(state)
    barrier = Barrier(2)

    def compete(message):
        candidate = service.store.get_run(state.run_id, state.user_id)
        expected = candidate.state_version
        candidate.message = message
        barrier.wait(timeout=10)
        try:
            service.store.save_run(candidate, expected)
            return "committed"
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(compete, "writer-a")
        second = pool.submit(compete, "writer-b")
        outcomes = [first.result(timeout=20), second.result(timeout=20)]
    assert sorted(outcomes) == ["committed", "conflict"]
    stored = service.store.get_run(state.run_id, state.user_id)
    assert stored.state_version == state.state_version + 1
    assert stored.message in {"writer-a", "writer-b"}


def test_mysql_ingestion_fault_rolls_back_all_transactional_writes(mysql_service):
    service = mysql_service
    source_id = "acceptance-" + uuid4().hex
    today = date(2026, 10, 13)

    def record(instrument, value, digest):
        return MarketRecord(
            source_id=source_id,
            instrument_id=instrument,
            trade_date=today,
            record_type="fund_nav",
            value=value,
            currency="CNY",
            source_uri="synthetic://mysql-acceptance",
            fetched_at=datetime.now(timezone.utc),
            content_hash=digest,
        )

    asyncio.run(service.market.upsert_batch([record("one", "1.0", "original")], uuid4().hex))
    revision_count = len(service.market.revisions())

    class Source:
        async def fetch(self, *args):
            return [record("one", "2.0", "changed"), record("two", "3.0", "new")]

    source = Source()
    source.source_id = source_id
    writes = 0

    def fail_second_watermark(conn, cursor, statement, parameters, context, executemany):
        nonlocal writes
        if "INSERT INTO MARKET_WATERMARKS" in statement.upper():
            writes += 1
            if writes == 2:
                raise RuntimeError("synthetic transactional fault")

    event.listen(service.engine, "before_cursor_execute", fail_second_watermark)
    try:
        with pytest.raises(RuntimeError, match="synthetic transactional fault"):
            asyncio.run(
                MarketIngestionPipeline(source, service.market).run(["one", "two"], today, today)
            )
    finally:
        event.remove(service.engine, "before_cursor_execute", fail_second_watermark)
    rows = service.market.query(["one", "two"], source_id=source_id)
    assert len(rows) == 1 and rows[0].content_hash == "original"
    assert str(rows[0].value) == "1.0000000000"
    assert len(service.market.revisions()) == revision_count
    assert service.market.get_watermark(source_id, "one") == today
    assert service.market.get_watermark(source_id, "two") is None
    logs = [r for r in service.market.ingestion_logs() if r["source_id"] == source_id]
    assert sorted(r["status"] for r in logs) == ["failed", "success"]
