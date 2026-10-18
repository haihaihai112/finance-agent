import pytest
from fastapi.testclient import TestClient

from finance_agents.api.app import create_app
from finance_agents.contracts.common import DataMode
from finance_agents.contracts.documents import ExtractedField
from finance_agents.contracts.profiles import CreditForm
from finance_agents.contracts.results import FinalResponse, Intent
from finance_agents.core.settings import Settings
from finance_agents.workflow.state import RunStatus, SessionState


@pytest.fixture
def loan(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, llm_enabled=False))) as client:
        session = client.post("/api/v1/sessions").json()
        headers = {"Authorization": "Bearer " + session["token"]}
        service = client.app.state.service
        import fitz

        with fitz.open() as pdf:
            pdf.new_page().insert_text((50, 60), "Monthly income CNY 8000")
            content = pdf.tobytes()
        doc = service.documents.save(session["user_id"], "income.pdf", content, "application/pdf")[
            "document_id"
        ]
        old = SessionState(
            run_id="old",
            user_id=session["user_id"],
            session_id=session["session_id"],
            intent=Intent.LOAN,
            status=RunStatus.COMPLETED,
            document_refs=[doc],
            extracted_fields=[
                ExtractedField(
                    name="monthly_income",
                    value={"amount": "8000", "currency": "CNY"},
                    document_id=doc,
                    confidence=1,
                    source="text_parser",
                    confirmed=True,
                )
            ],
            credit_form=CreditForm(
                subject_ref="synthetic",
                requested_amount={"amount": "12000", "currency": "CNY"},
                loan_term_months=12,
                monthly_income={"amount": "8000", "currency": "CNY"},
                monthly_debt_payment={"amount": "1000", "currency": "CNY"},
                employment_status="employed",
                confirmed_fields=[
                    "subject_ref",
                    "requested_amount",
                    "loan_term_months",
                    "monthly_income",
                    "monthly_debt_payment",
                    "employment_status",
                ],
                document_ids=[doc],
            ),
            final_response=FinalResponse(conclusion="模拟评估", data_mode=DataMode.MOCK),
        )
        service.store.create_run(old)
        reference = service.store.grant_consent(old.user_id, old.run_id)
        old.consent_ref = old.credit_form.consent_ref = reference
        service.store.save_run(old, 1)
        yield client, headers, old, reference


def test_copy_preserves_values_evidence_and_parent_but_clears_every_confirmation(loan, monkeypatch):
    client, headers, old, reference = loan
    service = client.app.state.service

    async def forbidden(*args):
        pytest.fail("copy must not execute external tools")

    monkeypatch.setattr(service, "execute", forbidden)
    response = client.post("/api/v1/runs/old/copy", headers=headers, json={"request_id": "copy"})
    assert response.status_code == 200, response.text
    result = response.json()
    copied = service.store.get_run(result["run_id"], old.user_id)
    assert result["parent_run_id"] == copied.parent_run_id == "old"
    assert result["document_ids"] == copied.document_refs == old.document_refs
    assert copied.credit_form.monthly_income == old.credit_form.monthly_income
    assert copied.credit_form.confirmed_fields == []
    assert copied.consent_ref is None and copied.credit_form.consent_ref is None
    assert not any(field.confirmed for field in copied.extracted_fields)
    assert copied.extracted_fields[0].quote == old.extracted_fields[0].quote
    assert result["wait_reason"] == "confirm_form"
    assert copied.final_response is None and copied.branch_result is None
    assert service.store.get_run("old", old.user_id) == old
    assert service.store.check_consent(reference, old.user_id, "old")
    assert not service.store.check_consent(reference, old.user_id, copied.run_id)
    assert (
        client.post("/api/v1/runs/old/copy", headers=headers, json={"request_id": "copy"}).json()
        == result
    )
    assert len(service.store.list_runs(old.user_id)) == 2


@pytest.mark.parametrize(
    "status,intent",
    [
        (RunStatus.WAITING_USER, Intent.LOAN),
        (RunStatus.RUNNING, Intent.LOAN),
        (RunStatus.COMPLETED, Intent.OTHER),
    ],
)
def test_only_ended_financial_business_can_be_copied(loan, status, intent):
    client, headers, old, _ = loan
    service = client.app.state.service
    old.status, old.intent = status, intent
    service.store.save_run(old, old.state_version)
    assert (
        client.post(
            "/api/v1/runs/old/copy", headers=headers, json={"request_id": "copy"}
        ).status_code
        == 409
    )
    assert len(service.store.list_runs(old.user_id)) == 1


@pytest.mark.parametrize("intent", [Intent.INVESTMENT, Intent.CLAIM, Intent.POLICY_TAX])
def test_copy_other_business_preserves_parent_and_requires_reconfirmation(
    loan, intent, monkeypatch
):
    client, headers, old, _ = loan
    service = client.app.state.service
    from finance_agents.workflow.service import FORMS

    old.intent = intent
    attr, model = FORMS[intent]
    setattr(old, attr, model(jurisdiction="CN") if intent != Intent.CLAIM else model(policy_id="P"))
    getattr(old, attr).confirmed_fields = [
        "policy_id" if intent == Intent.CLAIM else "jurisdiction"
    ]
    service.store.save_run(old, old.state_version)

    async def forbidden(*args):
        pytest.fail("Copy cannot execute external tools")

    monkeypatch.setattr(service, "execute", forbidden)
    response = client.post("/api/v1/runs/old/copy", headers=headers, json={"request_id": "copy"})
    assert response.status_code == 200, response.text
    copied = response.json()
    assert copied["intent"] == intent
    assert copied["form"]["confirmed_fields"] == []
    assert copied["status"] == "waiting_user"
    assert copied["result"] is None
    assert copied["parent_run_id"] == old.run_id
    assert service.store.get_run("old", old.user_id) == old


@pytest.mark.parametrize("transport", ["inprocess", "stdio"])
def test_copy_requires_new_confirmation_and_new_consent_before_reassessment(loan, transport):
    client, headers, old, reference = loan
    service = client.app.state.service
    service.settings.mcp_transport = transport
    copied = client.post(
        "/api/v1/runs/old/copy", headers=headers, json={"request_id": "copy"}
    ).json()
    fields = old.credit_form.model_dump(
        mode="json", exclude={"consent_ref", "confirmed_fields", "document_ids", "form_version"}
    )
    fields = {key: value for key, value in fields.items() if value is not None}
    endpoint = "/api/v1/runs/" + copied["run_id"]
    response = client.post(
        endpoint + "/resume",
        headers=headers,
        json={
            "request_id": "reconfirm",
            "state_version": copied["state_version"],
            "message": "确认复制后的全部字段",
            "confirmed_fields": fields,
            "consent_ref": reference,
        },
    )
    assert response.status_code == 200
    waiting = response.json()
    assert waiting["wait_reason"] == "consent"
    assert waiting["result"] is None
    authorization = client.post(
        endpoint + "/consent", headers=headers, json={"granted": True}
    ).json()["consent_ref"]
    result = client.post(
        endpoint + "/resume",
        headers=headers,
        json={
            "request_id": "reevaluate",
            "state_version": waiting["state_version"],
            "message": "授权新申请模拟评估",
            "consent_ref": authorization,
        },
    )
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["result"]["data_mode"] == "mock"
    assert service.store.get_run("old", old.user_id) == old


def test_copy_request_and_waiting_run_commit_in_one_transaction(loan):
    from sqlalchemy import event

    client, headers, old, _ = loan
    store = client.app.state.service.store

    def fail(connection, cursor, statement, parameters, context, many):
        if (
            "runtime_requests" in statement
            and statement.startswith(("INSERT", "UPDATE"))
            and context.compiled_parameters[0].get("result") is not None
        ):
            raise RuntimeError("injected copy failure")

    event.listen(store.engine, "before_cursor_execute", fail)
    with pytest.raises(RuntimeError, match="injected"):
        client.post("/api/v1/runs/old/copy", headers=headers, json={"request_id": "copy"})
    event.remove(store.engine, "before_cursor_execute", fail)
    assert len(store.list_runs(old.user_id)) == 1
    with pytest.raises(LookupError):
        store.get_request(old.user_id, "copy")
