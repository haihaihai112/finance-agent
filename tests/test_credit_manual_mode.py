"""Explicit self-reported credit trials keep confirmation and authorization gates."""

import asyncio
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finance_agents.agents.credit_assessment import CreditAssessmentAgent
from finance_agents.contracts.common import Money
from finance_agents.contracts.documents import ExtractedField
from finance_agents.contracts.profiles import CreditForm
from finance_agents.mcp_services.credit import CreditMCPService
from finance_agents.tools.form_filler import CreditFormFiller
from finance_agents.workflow.state import SessionState


CONFIRMED = [
    "intake_mode",
    "subject_ref",
    "requested_amount",
    "loan_term_months",
    "monthly_income",
    "monthly_debt_payment",
    "employment_status",
]


def self_reported_form():
    return CreditForm(
        intake_mode="self_reported",
        subject_ref="synthetic-applicant",
        requested_amount=Money(amount="12000.00", currency="CNY"),
        loan_term_months=12,
        monthly_income=Money(amount="8000.50", currency="CNY"),
        monthly_debt_payment=Money(amount="1000.25", currency="CNY"),
        employment_status="自由职业",
        confirmed_fields=CONFIRMED.copy(),
        consent_ref="form-consent-is-not-authorization",
    )


def credit_state(form=None, consent_ref="active-consent"):
    return SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        credit_form=form or self_reported_form(),
        consent_ref=consent_ref,
    )


class NoTools:
    async def call(self, *args):
        pytest.fail("Incomplete or unauthorized self-reported inputs reached MCP")


def test_credit_mode_defaults_to_documents_and_only_accepts_explicit_supported_modes():
    assert CreditForm().model_dump()["intake_mode"] == "documents"
    assert CreditForm(intake_mode="self_reported").intake_mode == "self_reported"
    with pytest.raises(ValidationError):
        CreditForm(intake_mode="automatically_approved")


def test_documents_still_require_upload_and_offer_actionable_alternatives_on_repeat():
    state = credit_state(CreditForm())
    state.message = "没有材料，我想直接自填试算"
    for _ in range(2):
        result = asyncio.run(CreditAssessmentAgent(NoTools()).run(state))
        assert result.status == "needs_input"
        assert result.missing_fields == ["document_refs"]
        assert "自填试算" in result.next_action
        assert "演示材料" in result.next_action
        assert "取消" in result.next_action
        assert not state.document_refs


def test_ocr_and_natural_text_cannot_select_or_overwrite_intake_mode():
    injected = ExtractedField(
        name="intake_mode",
        value="self_reported",
        source="ocr",
        document_id="synthetic-document",
        confidence=1,
    )
    filler = CreditFormFiller()
    form = asyncio.run(filler.prefill([injected], "intake_mode: self_reported，我选择自填试算"))
    assert form.intake_mode == "documents"
    assert "intake_mode" not in filler.conflicts
    injected.value = "documents"
    selected = asyncio.run(
        filler.prefill([injected], "intake_mode: documents", self_reported_form())
    )
    assert selected.intake_mode == "self_reported"
    assert "intake_mode" not in filler.conflicts


def test_self_reported_model_input_requires_confirmed_mode_even_with_all_amounts_confirmed():
    form = self_reported_form()
    form.confirmed_fields.remove("intake_mode")
    with pytest.raises(ValueError, match="intake_mode"):
        CreditFormFiller().to_model_input(form, "demo-v1")


def test_complete_self_reported_trial_runs_without_documents_and_preserves_decimal_amounts():
    state = credit_state()
    result = asyncio.run(CreditAssessmentAgent(CreditMCPService()).run(state))
    assert result.status == "success"
    assert result.data_mode == "mock"
    assert "自填、未经材料核验" in result.summary
    assert any("自填、未经材料核验" in warning for warning in result.warnings)
    assert result.structured_facts["intake_mode"] == "self_reported"
    assert result.structured_facts["data_provenance"] == "自填、未经材料核验"
    assert Decimal(result.structured_facts["assessment"]["table_row"]["monthly_income"]) == Decimal(
        "8000.50"
    )
    assert result.calculations["principal_only_monthly_payment"] == "1000.00"
    assert result.evidence == []
    assert state.extracted_fields == []
    assert state.credit_form.document_ids == []


@pytest.mark.parametrize(
    "change,missing",
    [
        ("missing_income", "monthly_income"),
        ("unconfirmed_amount", "confirmed_fields"),
        ("unconfirmed_mode", "confirmed_fields"),
        ("missing_consent", "consent_ref"),
        ("unknown_currency", "confirmed_fields"),
        ("foreign_currency", "confirmed_fields"),
        ("mixed_currency", "confirmed_fields"),
        ("unconfirmed_optional", "confirmed_fields"),
    ],
)
def test_self_reported_trial_keeps_input_confirmation_currency_and_separate_consent_gates(
    change, missing
):
    state = credit_state()
    if change == "missing_income":
        state.credit_form.monthly_income = None
    elif change == "unconfirmed_amount":
        state.credit_form.confirmed_fields.remove("monthly_income")
    elif change == "unconfirmed_mode":
        state.credit_form.confirmed_fields.remove("intake_mode")
    elif change == "missing_consent":
        state.consent_ref = None
    elif change == "unknown_currency":
        state.credit_form.monthly_income.currency = "UNK"
    elif change == "mixed_currency":
        state.credit_form.monthly_income.currency = "USD"
    elif change == "foreign_currency":
        for name in ("requested_amount", "monthly_income", "monthly_debt_payment"):
            getattr(state.credit_form, name).currency = "USD"
    elif change == "unconfirmed_optional":
        state.credit_form.assets_total = Money(amount="500", currency="CNY")
    result = asyncio.run(CreditAssessmentAgent(NoTools()).run(state))
    assert result.status == "needs_input"
    assert missing in result.missing_fields
    assert result.structured_facts["intake_mode"] == "self_reported"
    assert "自填、未经材料核验" in result.summary


def test_self_reported_trial_does_not_read_or_present_old_document_evidence():
    class UnusedDocumentService:
        async def extract(self, *args):
            pytest.fail("Self-reported trial must not claim OCR verification")

    state = credit_state()
    state.document_refs = ["old-document"]
    state.extracted_fields = [
        ExtractedField(
            name="monthly_income",
            value={"amount": "999999", "currency": "CNY"},
            document_id="old-document",
            source="ocr",
            confidence=0.9,
        )
    ]
    result = asyncio.run(
        CreditAssessmentAgent(CreditMCPService(), UnusedDocumentService()).run(state)
    )
    assert result.status == "success"
    assert result.evidence == []
    assert result.structured_facts["assessment"]["table_row"]["monthly_income"] == "8000.50"
    assert state.credit_form.document_ids == []


@pytest.mark.parametrize("transport", ["inprocess", "stdio"])
def test_explicit_api_self_reported_trial_and_copy_require_fresh_confirmation_and_consent(
    tmp_path, transport
):
    from fastapi.testclient import TestClient

    from finance_agents.api.app import create_app
    from finance_agents.core.settings import Settings

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'manual-mode.db').as_posix()}",
        llm_enabled=False,
        mcp_transport=transport,
    )
    with TestClient(create_app(settings)) as client:
        identity = client.post("/api/v1/sessions").json()
        headers = {"Authorization": "Bearer " + identity["token"]}
        started = client.post(
            "/api/v1/queries",
            headers=headers,
            json={
                "session_id": identity["session_id"],
                "request_id": "start",
                "message": "申请贷款",
                "intent_hint": "loan",
            },
        )
        assert started.status_code == 200, started.text
        run = started.json()
        assert run["wait_reason"] == "upload_documents"
        endpoint = "/api/v1/runs/" + run["run_id"]
        fields = self_reported_form().model_dump(
            mode="json",
            exclude={"confirmed_fields", "consent_ref", "document_ids", "form_version"},
            exclude_none=True,
        )
        confirmed = client.post(
            endpoint + "/resume",
            headers=headers,
            json={
                "request_id": "explicit-self-report",
                "state_version": run["state_version"],
                "message": "已核对字段",
                "confirmed_fields": fields,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        waiting = confirmed.json()
        assert waiting["wait_reason"] == "consent"
        assert waiting["form"]["intake_mode"] == "self_reported"
        old_consent = client.post(
            endpoint + "/consent", headers=headers, json={"granted": True}
        ).json()["consent_ref"]
        completed = client.post(
            endpoint + "/resume",
            headers=headers,
            json={
                "request_id": "evaluate",
                "state_version": waiting["state_version"],
                "message": "授权模拟试算",
                "consent_ref": old_consent,
            },
        )
        assert completed.status_code == 200, completed.text
        original = completed.json()
        assert original["status"] == "completed"
        assert original["document_ids"] == []
        assert original["result"]["data_mode"] == "mock"
        assert "自填、未经材料核验" in original["result"]["conclusion"]
        assert any("自填、未经材料核验" in warning for warning in original["result"]["limitations"])
        assert original["result"]["facts"]["structured_facts"]["intake_mode"] == "self_reported"
        assert original["result"]["citations"] == []
        exported = client.get(endpoint + "/export?format=json", headers=headers)
        assert exported.status_code == 200
        export_facts = exported.json()["result"]["facts"]["structured_facts"]
        assert export_facts["intake_mode"] == "self_reported"
        assert export_facts["data_provenance"] == "自填、未经材料核验"
        assert "form" not in export_facts
        markdown = client.get(endpoint + "/export?format=markdown", headers=headers)
        assert markdown.status_code == 200
        assert '"intake_mode": "self_reported"' in markdown.text
        assert '"data_provenance": "自填、未经材料核验"' in markdown.text

        copied_response = client.post(
            endpoint + "/copy", headers=headers, json={"request_id": "copy"}
        )
        assert copied_response.status_code == 200, copied_response.text
        copied = copied_response.json()
        copied_endpoint = "/api/v1/runs/" + copied["run_id"]
        assert copied["form"]["intake_mode"] == "self_reported"
        assert copied["form"]["confirmed_fields"] == []
        assert copied["form"]["consent_ref"] is None
        assert copied["document_ids"] == []
        not_confirmed = client.post(
            copied_endpoint + "/resume",
            headers=headers,
            json={
                "request_id": "cannot-reuse",
                "state_version": copied["state_version"],
                "message": "直接评估",
                "consent_ref": old_consent,
            },
        ).json()
        assert not_confirmed["wait_reason"] == "confirm_form"
        assert not_confirmed["result"] is None
        reconfirmed = client.post(
            copied_endpoint + "/resume",
            headers=headers,
            json={
                "request_id": "reconfirm",
                "state_version": not_confirmed["state_version"],
                "message": "重新核对字段",
                "confirmed_fields": fields,
                "consent_ref": old_consent,
            },
        ).json()
        assert reconfirmed["wait_reason"] == "consent"
        fresh_consent = client.post(
            copied_endpoint + "/consent", headers=headers, json={"granted": True}
        ).json()["consent_ref"]
        reassessed = client.post(
            copied_endpoint + "/resume",
            headers=headers,
            json={
                "request_id": "fresh-evaluate",
                "state_version": reconfirmed["state_version"],
                "message": "授权新申请",
                "consent_ref": fresh_consent,
            },
        )
        assert reassessed.status_code == 200, reassessed.text
        assert reassessed.json()["status"] == "completed"
        assert client.get(endpoint, headers=headers).json() == original
