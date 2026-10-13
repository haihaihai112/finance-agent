import pytest
from fastapi.testclient import TestClient

from finance_agents.api.app import create_app
from finance_agents.core.settings import Settings


@pytest.fixture
def client(tmp_path, request):
    transport = getattr(request, "param", "inprocess")
    with TestClient(
        create_app(Settings(data_dir=tmp_path, llm_enabled=False, mcp_transport=transport))
    ) as client:
        yield client


def session(client):
    response = client.post("/api/v1/sessions")
    assert response.status_code == 200
    value = response.json()
    return value["session_id"], {"Authorization": "Bearer " + value["token"]}


def query(client, sid, headers, intent="loan", request_id="q1"):
    return client.post(
        "/api/v1/queries",
        headers=headers,
        json={
            "session_id": sid,
            "request_id": request_id,
            "message": "申请贷款" if intent == "loan" else "理赔申请",
            "intent_hint": intent,
        },
    )


def test_auth_and_idempotent_start(client):
    sid, headers = session(client)
    assert query(client, sid, {}).status_code == 401
    first = query(client, sid, headers)
    assert first.status_code == 200
    assert first.json()["wait_reason"] == "upload_documents"
    assert query(client, sid, headers).json() == first.json()
    changed = client.post(
        "/api/v1/queries",
        headers=headers,
        json={"session_id": sid, "request_id": "q1", "message": "不同内容", "intent_hint": "other"},
    )
    assert changed.status_code == 409


def test_request_id_matches_database_limit(client):
    sid, headers = session(client)
    response = client.post(
        "/api/v1/queries",
        headers=headers,
        json={
            "session_id": sid,
            "request_id": "x" * 129,
            "message": "申请贷款",
            "intent_hint": "loan",
        },
    )
    assert response.status_code == 422


def test_run_and_document_isolation(client):
    sid, headers = session(client)
    run = query(client, sid, headers).json()
    _, other = session(client)
    assert client.get("/api/v1/runs/" + run["run_id"], headers=other).status_code == 403
    demo = client.post("/api/v1/demo", headers=headers).json()
    document = demo["documents"][0]["document_id"]
    assert client.get("/api/v1/documents/" + document, headers=other).status_code == 403
    assert (
        client.get("/api/v1/documents/" + document + "/download", headers=headers).status_code
        == 200
    )


@pytest.mark.parametrize("client", ["inprocess", "stdio"], indirect=True)
def test_credit_requires_confirmation_and_current_consent(client):
    sid, headers = session(client)
    run = query(client, sid, headers).json()
    demo = client.post("/api/v1/demo", headers=headers).json()

    def resume(message, fields=None, docs=None, consent=None):
        nonlocal run
        response = client.post(
            "/api/v1/runs/" + run["run_id"] + "/resume",
            headers=headers,
            json={
                "request_id": "r" + str(run["state_version"]),
                "state_version": run["state_version"],
                "message": message,
                "confirmed_fields": fields or {},
                "attachment_ids": docs or [],
                "consent_ref": consent,
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()

    resume("已上传", docs=[demo["documents"][1]["document_id"]])
    assert run["status"] == "waiting_user"
    fields = {
        "subject_ref": "demo-applicant",
        "requested_amount": {"amount": "12000", "currency": "CNY"},
        "loan_term_months": 12,
        "monthly_income": {"amount": "8000", "currency": "CNY"},
        "monthly_debt_payment": {"amount": "1000", "currency": "CNY"},
        "employment_status": "employed",
    }
    resume("确认表单", fields)
    assert run["wait_reason"] == "consent"
    consent = client.post(
        "/api/v1/runs/" + run["run_id"] + "/consent", headers=headers, json={"granted": True}
    ).json()["consent_ref"]
    client.post(
        "/api/v1/runs/" + run["run_id"] + "/consent", headers=headers, json={"granted": False}
    )
    resume("继续", consent=consent)
    assert run["wait_reason"] == "consent"
    consent = client.post(
        "/api/v1/runs/" + run["run_id"] + "/consent", headers=headers, json={"granted": True}
    ).json()["consent_ref"]
    resume("继续", consent=consent)
    assert run["status"] == "completed", run
    assert run["result"]["data_mode"] == "mock"
    assert (
        run["result"]["facts"]["structured_facts"]["assessment"]["debt_service_ratio"] == "0.2500"
    )
    client.post(
        "/api/v1/runs/" + run["run_id"] + "/consent", headers=headers, json={"granted": False}
    )
    assert (
        client.get("/api/v1/runs/" + run["run_id"], headers=headers).json()["form"]["consent_ref"]
        is None
    )


def test_versions_unknown_fields_and_cancel(client):
    sid, headers = session(client)
    run = query(client, sid, headers).json()
    endpoint = "/api/v1/runs/" + run["run_id"]
    bad = {
        "request_id": "bad",
        "state_version": run["state_version"],
        "message": "确认",
        "confirmed_fields": {"consent_ref": "forged"},
    }
    assert client.post(endpoint + "/resume", headers=headers, json=bad).status_code == 422
    assert client.post(endpoint + "/cancel", headers=headers).json()["status"] == "cancelled"
    bad["confirmed_fields"] = {}
    bad["request_id"] = "late"
    assert client.post(endpoint + "/resume", headers=headers, json=bad).status_code == 409


def test_stale_version_does_not_consume_request_id(client):
    sid, headers = session(client)
    run = query(client, sid, headers).json()
    endpoint = "/api/v1/runs/" + run["run_id"] + "/resume"
    body = {"request_id": "retry", "state_version": 1, "message": "补答"}
    assert client.post(endpoint, headers=headers, json=body).status_code == 409
    body["state_version"] = run["state_version"]
    assert client.post(endpoint, headers=headers, json=body).status_code == 200


def test_restart_restores_run_and_session(tmp_path):
    settings = Settings(data_dir=tmp_path, llm_enabled=False)
    with TestClient(create_app(settings)) as first:
        sid, headers = session(first)
        run = query(first, sid, headers).json()
    with TestClient(create_app(settings)) as second:
        restored = second.get("/api/v1/runs/" + run["run_id"], headers=headers)
        assert restored.status_code == 200
        assert restored.json() == run


@pytest.mark.parametrize(
    "message,expected",
    [
        ("请试算年度个税税额", "waiting_user"),
        ("查询企业增值税政策", "partial"),
    ],
)
def test_resume_preserves_original_tax_task(client, message, expected):
    sid, headers = session(client)
    run = client.post(
        "/api/v1/queries",
        headers=headers,
        json={
            "session_id": sid,
            "request_id": "tax",
            "message": message,
            "intent_hint": "policy_tax",
        },
    ).json()
    result = client.post(
        "/api/v1/runs/" + run["run_id"] + "/resume",
        headers=headers,
        json={
            "request_id": "tax-r",
            "state_version": run["state_version"],
            "message": "已确认",
            "confirmed_fields": {"jurisdiction": "中国大陆", "tax_year": 2026},
        },
    ).json()
    assert result["status"] == expected, result


def test_claim_end_to_end(client):
    sid, headers = session(client)
    demo = client.post("/api/v1/demo", headers=headers).json()
    run = client.post(
        "/api/v1/queries",
        headers=headers,
        json={
            "session_id": sid,
            "request_id": "claim",
            "message": "申请理赔",
            "intent_hint": "claim",
            "attachment_ids": [demo["documents"][0]["document_id"]],
        },
    ).json()
    assert run["wait_reason"] == "confirm_form"
    fields = {
        "policy_id": "DEMO-POLICY-001",
        "policy_version": "demo-v1",
        "incident_date": "2026-10-01",
        "incident_type": "hospitalization",
    }
    final = client.post(
        "/api/v1/runs/" + run["run_id"] + "/resume",
        headers=headers,
        json={
            "request_id": "claim-r",
            "state_version": run["state_version"],
            "message": "已确认",
            "confirmed_fields": fields,
        },
    ).json()
    assert final["status"] == "completed", final
    assert final["result"]["data_mode"] == "mock"
    assert (
        final["result"]["facts"]["structured_facts"]["clause_check"]["preliminary_result"]
        == "potentially_covered"
    )


def test_investment_and_tax_end_to_end(client):
    sid, headers = session(client)
    client.post("/api/v1/demo", headers=headers)
    for intent, fields in [
        (
            "investment",
            {
                "jurisdiction": "中国大陆",
                "investable_amount": {"amount": "100000", "currency": "CNY"},
                "horizon_months": 36,
                "risk_tolerance": "medium",
                "liquidity_need": {"amount": "10000", "currency": "CNY"},
                "liabilities": {"amount": "0", "currency": "CNY"},
                "existing_assets": [],
                "instrument_ids": ["510300"],
            },
        ),
        (
            "policy_tax",
            {
                "jurisdiction": "中国大陆",
                "tax_year": 2026,
                "income_type": "resident_comprehensive_annual",
                "annual_income": {"amount": "120000", "currency": "CNY"},
                "annual_deductions": {"amount": "0", "currency": "CNY"},
                "resident_status": "resident",
            },
        ),
    ]:
        run = client.post(
            "/api/v1/queries",
            headers=headers,
            json={
                "session_id": sid,
                "request_id": intent,
                "message": "投资咨询" if intent == "investment" else "计算税额",
                "intent_hint": intent,
            },
        ).json()
        result = client.post(
            "/api/v1/runs/" + run["run_id"] + "/resume",
            headers=headers,
            json={
                "request_id": intent + "-r",
                "state_version": run["state_version"],
                "message": "确认",
                "confirmed_fields": fields,
            },
        ).json()
        assert result["status"] == "completed", result
        assert result["result"]["data_mode"] == "real"
        calculations = result["result"]["facts"]["calculations"]
        if intent == "policy_tax":
            assert calculations["annual_tax"] == "3480.00"
        else:
            assert calculations["historical_metrics"]["510300"]["observations"] == 16


def test_other_and_ambiguous_intent(client):
    sid, headers = session(client)
    for index, message in enumerate(["写一首诗", "我想投资，也想贷款"]):
        result = client.post(
            "/api/v1/queries",
            headers=headers,
            json={"session_id": sid, "request_id": str(index), "message": message},
        ).json()
        assert result["status"] == ("completed" if index == 0 else "waiting_user")
        if index == 1:
            assert result["wait_reason"] == "clarify_intent"
