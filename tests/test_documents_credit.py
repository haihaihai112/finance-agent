import asyncio
from decimal import Decimal

import pytest

from finance_agents.contracts.common import AuthContext, Money
from finance_agents.contracts.documents import ExtractedField
from finance_agents.contracts.profiles import CreditForm
from finance_agents.tools.form_filler import CreditFormFiller


def complete_form():
    return CreditForm(
        subject_ref="applicant",
        requested_amount=Money(amount="10000", currency="CNY"),
        loan_term_months=12,
        monthly_income=Money(amount="8000", currency="CNY"),
        monthly_debt_payment=Money(amount="1000", currency="CNY"),
        employment_status="employed",
        consent_ref="consent-1",
    )


def test_unconfirmed_financial_numbers_cannot_enter_model():
    with pytest.raises(ValueError, match="confirmed"):
        CreditFormFiller().to_model_input(complete_form(), "demo-v1")


def test_multidocument_conflict_is_preserved_and_needs_resolution():
    fields = [
        ExtractedField(
            name="monthly_income",
            value={"amount": "8000", "currency": "CNY"},
            document_id=doc,
            quote="月收入",
            confidence=0.9,
            source="ocr",
        )
        for doc in ["doc-1", "doc-2"]
    ]
    fields[1].value = {"amount": "12000", "currency": "CNY"}
    filler = CreditFormFiller()
    form = asyncio.run(filler.prefill(fields, ""))
    assert form.monthly_income is None
    assert len(filler.conflicts["monthly_income"]) == 2


def test_confirmed_model_input_requires_same_currency_and_consent():
    filler = CreditFormFiller()
    form = complete_form()
    form.confirmed_fields = list(filler.required_fields)
    result = filler.to_model_input(form, "demo-v1")
    assert result.monthly_income == Decimal("8000")
    form.monthly_debt_payment.currency = "USD"
    with pytest.raises(ValueError, match="currency"):
        filler.to_model_input(form, "demo-v1")
    form.monthly_debt_payment.currency = "CNY"
    form.consent_ref = None
    with pytest.raises(ValueError, match="consent"):
        filler.to_model_input(form, "demo-v1")


def test_credit_service_checks_authorization_and_marks_mock():
    from finance_agents.mcp_services.credit import CreditMCPService

    filler = CreditFormFiller()
    form = complete_form()
    form.confirmed_fields = list(filler.required_fields)
    row = filler.to_model_input(form, "demo-v1")
    service = CreditMCPService()
    rejected = asyncio.run(service.evaluate_form(row, AuthContext(user_id="u", session_id="s")))
    assert rejected.status == "failed"
    accepted = asyncio.run(
        service.evaluate_form(
            row, AuthContext(user_id="u", session_id="s", consent_ref="consent-1")
        )
    )
    assert accepted.status == "success" and accepted.data_mode == "mock"
    assert accepted.data["table_row"]["monthly_income"] == "8000"


def test_parser_preserves_decimal_units_evidence_and_confirmation():
    from finance_agents.tools.ocr import parse_text_fields

    fields = parse_text_fields(
        "月收入：1.25万元\n月还款：2,000元\n贷款期限：12个月", "doc", page_number=2
    )
    by_name = {f.name: f for f in fields}
    assert by_name["monthly_income"].value == {"amount": "12500.00", "currency": "CNY"}
    assert by_name["monthly_debt_payment"].value["amount"] == "2000"
    assert by_name["loan_term_months"].value == 12
    assert all(f.document_id == "doc" and f.page_number == 2 and not f.confirmed for f in fields)


def test_document_store_validates_content_and_isolates_users(tmp_path):
    import fitz
    from sqlalchemy import create_engine
    from finance_agents.storage.document_store import DocumentStore

    pdf = fitz.open()
    pdf.new_page().insert_text((50, 50), "monthly_income: 8000 CNY")
    content = pdf.tobytes()
    store = DocumentStore(create_engine("sqlite://"), tmp_path)
    info = store.save("user-a", "../income.pdf", content, "application/pdf")
    assert store.read("user-a", info["document_id"]) == content
    with pytest.raises(PermissionError):
        store.read("user-b", info["document_id"])
    with pytest.raises(ValueError):
        store.save("user-a", "fake.pdf", b"fake", "application/pdf")
    with pytest.raises(ValueError):
        store.save("user-a", "a.exe", content, "application/octet-stream")


def test_text_pdf_uses_parser_and_service_cache(tmp_path):
    import fitz
    from finance_agents.storage.database import create_database_engine
    from finance_agents.storage.document_store import DocumentStore
    from finance_agents.tools.document_service import DocumentService
    from finance_agents.tools.ocr import DocumentOCR

    pdf = fitz.open()
    pdf.new_page().insert_text((50, 50), "monthly_income: 8000 CNY")
    store = DocumentStore(create_database_engine("sqlite:///:memory:"), tmp_path)
    info = store.save("u", "income.pdf", pdf.tobytes(), "application/pdf")
    service = DocumentService(store, DocumentOCR())
    first = asyncio.run(service.extract("u", info["document_id"]))
    second = asyncio.run(service.extract("u", info["document_id"]))
    assert first == second
    assert first.fields[0].source == "text_parser"
    assert first.fields[0].bounding_box is not None
    with pytest.raises(PermissionError):
        asyncio.run(service.extract("someone-else", info["document_id"]))


def test_claims_history_and_terms_are_scoped_and_demo_labeled():
    from sqlalchemy import create_engine
    from finance_agents.storage.claims_store import ClaimsStore
    from finance_agents.mcp_services.claims import ClaimsMCPService

    store = ClaimsStore(create_engine("sqlite://"))
    store.seed_demo("owner")
    service = ClaimsMCPService(store)
    other = asyncio.run(
        service.call("claims.lookup_policies", {}, AuthContext(user_id="other", session_id="s"))
    )
    assert other.status == "unavailable"
    owner = asyncio.run(
        service.call("claims.lookup_policies", {}, AuthContext(user_id="owner", session_id="s"))
    )
    assert owner.data_mode == "mock"
    policy = owner.data["policies"][0]
    result = asyncio.run(
        service.call(
            "claims.check_conditions",
            {
                "policy_id": policy["policy_id"],
                "policy_version": policy["policy_version"],
                "incident_type": "hospitalization",
                "incident_date": "2026-10-10",
            },
            AuthContext(user_id="owner", session_id="s"),
        )
    )
    assert result.status == "success"
    assert result.data["preliminary_result"] == "potentially_covered"
    assert "payout" not in result.data


def test_missing_real_policy_clauses_are_unavailable():
    from sqlalchemy import create_engine
    from finance_agents.storage.claims_store import ClaimsStore
    from finance_agents.mcp_services.claims import ClaimsMCPService

    store = ClaimsStore(create_engine("sqlite://"))
    store.put_policy(
        "u",
        {
            "policy_id": "real-id",
            "policy_version": "v1",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
            "data_mode": "real",
            "source_uri": "https://example.org/terms",
        },
    )
    result = asyncio.run(
        ClaimsMCPService(store).call(
            "claims.match_clauses",
            {"policy_id": "real-id", "policy_version": "v1", "incident_type": "hospitalization"},
            AuthContext(user_id="u", session_id="s"),
        )
    )
    assert result.status == "unavailable"


def test_agents_wait_for_upload_confirmation_and_consent_without_calling_tools():
    from finance_agents.agents.claim_intake import ClaimIntakeAgent
    from finance_agents.agents.credit_assessment import CreditAssessmentAgent
    from finance_agents.workflow.state import SessionState

    class NoCalls:
        async def call(self, *args):
            pytest.fail("Tool must not run before confirmation and consent")

    state = SessionState(run_id="r", user_id="u", session_id="s")
    assert asyncio.run(ClaimIntakeAgent(NoCalls()).run(state)).missing_fields == ["document_refs"]
    assert asyncio.run(CreditAssessmentAgent(NoCalls()).run(state)).missing_fields == [
        "document_refs"
    ]
    state.document_refs = ["d"]
    state.extracted_fields = [
        ExtractedField(
            name="monthly_income",
            value={"amount": "8000", "currency": "CNY"},
            document_id="d",
            source="ocr",
            confidence=0.8,
        )
    ]
    state.credit_form = complete_form()
    result = asyncio.run(CreditAssessmentAgent(NoCalls()).run(state))
    assert "confirmed_fields" in result.missing_fields
    state.credit_form.confirmed_fields = list(CreditFormFiller.required_fields)
    state.consent_ref = None
    result = asyncio.run(CreditAssessmentAgent(NoCalls()).run(state))
    assert result.missing_fields == ["consent_ref"]


def test_public_credit_tool_rejects_unconfirmed_rows():
    from finance_agents.mcp_services.credit import CreditMCPService

    form = complete_form()
    form.confirmed_fields = list(CreditFormFiller.required_fields)
    row = CreditFormFiller().to_model_input(form, "demo-v1")
    auth = AuthContext(user_id="u", session_id="s", consent_ref="consent-1")
    service = CreditMCPService()
    result = asyncio.run(
        service.call("credit.evaluate_mock", {"model_input": row.model_dump(mode="json")}, auth)
    )
    assert result.status == "failed" and result.error.code == "UNCONFIRMED_FIELDS"


def test_numeric_equivalence_does_not_create_false_conflict():
    fields = [
        ExtractedField(
            name="monthly_income",
            value={"amount": value, "currency": "CNY"},
            document_id=doc,
            source="text_parser",
            confidence=1,
        )
        for value, doc in [("8000.00", "a"), ("8000", "b")]
    ]
    filler = CreditFormFiller()
    form = asyncio.run(filler.prefill(fields, ""))
    assert form.monthly_income.amount == Decimal("8000") and not filler.conflicts


def test_unknown_currency_is_not_submitted_to_model():
    form = complete_form()
    form.confirmed_fields = list(CreditFormFiller.required_fields)
    for name in ("requested_amount", "monthly_income", "monthly_debt_payment"):
        getattr(form, name).currency = "UNK"
    with pytest.raises(ValueError, match="currency"):
        CreditFormFiller().to_model_input(form, "demo-v1")


def test_foreign_currency_credit_requires_explicit_supported_currency():
    form = complete_form()
    form.confirmed_fields = list(CreditFormFiller.required_fields)
    for name in ("requested_amount", "monthly_income", "monthly_debt_payment"):
        getattr(form, name).currency = "USD"
    with pytest.raises(ValueError, match="CNY"):
        CreditFormFiller().to_model_input(form, "demo-v1")


def render_ocr_fixture(lines, width=1300, height=250, font_size=56):
    """Rasterize bundled CJK font; no operating-system font files are needed."""
    import fitz

    with fitz.open() as pdf:
        page = pdf.new_page(width=width, height=height)
        for x, y, text in lines:
            page.insert_text((x, y), text, fontname="china-s", fontsize=font_size)
        return page.get_pixmap(alpha=False).tobytes("png")


def test_image_and_scanned_pdf_run_real_onnx_ocr(tmp_path):
    import fitz
    from finance_agents.tools.ocr import DocumentOCR

    image_bytes = render_ocr_fixture([(35, 130, "月收入：8000元")])
    ocr = DocumentOCR()
    result = asyncio.run(ocr.extract("image", image_bytes, "image/png"))
    incomes = [f for f in result.fields if f.name == "monthly_income"]
    assert incomes and Decimal(incomes[0].value["amount"]) == Decimal("8000")
    assert incomes[0].source == "ocr" and incomes[0].bounding_box and 0 < incomes[0].confidence <= 1
    pdf = fitz.open()
    page = pdf.new_page(width=650, height=125)
    page.insert_image(page.rect, stream=image_bytes)
    scan = asyncio.run(ocr.extract("scan", pdf.tobytes(), "application/pdf"))
    assert any(
        f.name == "monthly_income" and f.page_number == 1 and f.source == "ocr" for f in scan.fields
    )


def test_changing_confirmed_income_removes_old_confirmation():
    form = complete_form()
    form.confirmed_fields = list(CreditFormFiller.required_fields)
    filler = CreditFormFiller()
    changed = asyncio.run(filler.prefill([], "月收入：9000元", form))
    assert "monthly_income" not in changed.confirmed_fields
    assert "monthly_income" in filler.conflicts


def test_unit_without_currency_and_foreign_currency_unit_are_explicit():
    from finance_agents.tools.ocr import parse_text_fields

    unclear = parse_text_fields("月收入：1.2万", "doc")
    assert unclear[0].value == {"amount": "12000.0", "currency": "UNK"}
    dollar = parse_text_fields("月收入：1.2万美元", "doc")
    assert dollar[0].value == {"amount": "12000.0", "currency": "USD"}


def test_fullwidth_ocr_digits_are_normalized_without_rounding():
    from finance_agents.tools.ocr import parse_text_fields

    fields = parse_text_fields("月还款：１０００．５０元", "doc", source="ocr")
    assert fields and fields[0].value == {"amount": "1000.50", "currency": "CNY"}
    assert fields[0].quote == "月还款：１０００．５０元"


def test_complete_credit_agent_uses_confirmed_rows_and_mock_service():
    from finance_agents.agents.credit_assessment import CreditAssessmentAgent
    from finance_agents.mcp_services.credit import CreditMCPService
    from finance_agents.workflow.state import SessionState

    form = complete_form()
    form.confirmed_fields = list(CreditFormFiller.required_fields)
    state = SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        credit_form=form,
        consent_ref="consent-1",
        document_refs=["d"],
        extracted_fields=[
            ExtractedField(
                name="monthly_income",
                value={"amount": "8000", "currency": "CNY"},
                document_id="d",
                source="ocr",
                confidence=0.9,
            )
        ],
    )
    result = asyncio.run(CreditAssessmentAgent(CreditMCPService()).run(state))
    assert result.status == "success" and result.data_mode == "mock"
    assert result.structured_facts["assessment"]["result"] == "demo_pass"


def test_claim_agent_checks_exact_policy_version_after_confirmation():
    from sqlalchemy import create_engine
    from finance_agents.agents.claim_intake import ClaimIntakeAgent
    from finance_agents.contracts.profiles import ClaimForm
    from finance_agents.mcp_services.claims import ClaimsMCPService
    from finance_agents.storage.claims_store import ClaimsStore
    from finance_agents.workflow.state import SessionState

    store = ClaimsStore(create_engine("sqlite://"))
    store.seed_demo("u")
    form = ClaimForm(
        policy_id="DEMO-POLICY-001",
        policy_version="demo-v1",
        incident_type="hospitalization",
        incident_date="2026-10-10",
    )
    state = SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        claim_form=form,
        document_refs=["d"],
        extracted_fields=[
            ExtractedField(
                name="policy_id",
                value="DEMO-POLICY-001",
                document_id="d",
                source="text_parser",
                confidence=1,
            )
        ],
    )
    agent = ClaimIntakeAgent(ClaimsMCPService(store))
    assert asyncio.run(agent.run(state)).missing_fields == ["confirmed_fields"]
    state.claim_form.confirmed_fields = list(agent.required_fields)
    result = asyncio.run(agent.run(state))
    assert result.status == "success" and result.data_mode == "mock"
    assert result.structured_facts["clause_check"]["policy_version"] == "demo-v1"


def test_ocr_table_columns_are_joined_by_row():
    from finance_agents.tools.ocr import DocumentOCR

    image_bytes = render_ocr_fixture(
        [
            (35, 110, "月收入"),
            (750, 110, "8000元"),
            (35, 230, "月还款"),
            (750, 230, "1000元"),
        ],
        height=320,
        font_size=50,
    )
    result = asyncio.run(DocumentOCR().extract("table", image_bytes, "image/png"))
    by_name = {field.name: field for field in result.fields}
    assert Decimal(by_name["monthly_income"].value["amount"]) == Decimal("8000")
    assert Decimal(by_name["monthly_debt_payment"].value["amount"]) == Decimal("1000")


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("covered", "潜在保障"),
        ("outside", "不在保障期"),
        ("excluded", "条款排除"),
        ("unmatched", "需人工复核"),
    ],
)
def test_claim_summary_explains_actual_screening_outcome(scenario, expected):
    from sqlalchemy import create_engine
    from finance_agents.agents.claim_intake import ClaimIntakeAgent
    from finance_agents.contracts.profiles import ClaimForm
    from finance_agents.mcp_services.claims import ClaimsMCPService
    from finance_agents.storage.claims_store import ClaimsStore
    from finance_agents.workflow.state import SessionState

    store = ClaimsStore(create_engine("sqlite://"))
    policy = store.seed_demo("u")[0]
    if scenario == "excluded":
        policy["clauses"][0]["excluded"] = True
        store.put_policy("u", policy)
    form = ClaimForm(
        policy_id=policy["policy_id"],
        policy_version=policy["policy_version"],
        incident_type="unmatched_incident" if scenario == "unmatched" else "hospitalization",
        incident_date="2031-01-01" if scenario == "outside" else "2026-10-10",
        confirmed_fields=list(ClaimIntakeAgent.required_fields),
    )
    state = SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        claim_form=form,
        document_refs=["d"],
        extracted_fields=[
            ExtractedField(
                name="policy_id",
                value=policy["policy_id"],
                document_id="d",
                source="text_parser",
                confidence=1,
            )
        ],
    )
    result = asyncio.run(ClaimIntakeAgent(ClaimsMCPService(store)).run(state))
    assert expected in result.summary and "模拟" in result.summary
    assert "已批准" not in result.summary and "已赔付" not in result.summary
    assert result.next_action and "保险机构" in result.next_action


@pytest.mark.parametrize("income,expected", [("8000", "演示规则通过"), ("1000", "需人工复核")])
def test_credit_summary_reports_demo_result_and_principal_estimate(income, expected):
    from finance_agents.agents.credit_assessment import CreditAssessmentAgent
    from finance_agents.mcp_services.credit import CreditMCPService
    from finance_agents.workflow.state import SessionState

    form = complete_form()
    form.monthly_income.amount = Decimal(income)
    form.confirmed_fields = list(CreditFormFiller.required_fields)
    state = SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        credit_form=form,
        consent_ref="consent-1",
        document_refs=["d"],
        extracted_fields=[
            ExtractedField(
                name="monthly_income",
                value={"amount": income, "currency": "CNY"},
                document_id="d",
                source="text_parser",
                confidence=1,
            )
        ],
    )
    result = asyncio.run(CreditAssessmentAgent(CreditMCPService()).run(state))
    assert expected in result.summary
    assert income in result.summary and "1000" in result.summary and "833.33" in result.summary
    assert (
        "本金" in result.summary and "利息" in result.summary and "不代表银行审批" in result.summary
    )
    assert result.next_action and "金额" in result.next_action and "期限" in result.next_action
    assert "重新评估" in result.next_action


@pytest.mark.parametrize("kind,field_name", [("credit", "monthly_income"), ("claim", "premium")])
def test_new_document_reopens_confirmed_value_but_old_correction_remains_valid(
    tmp_path, kind, field_name
):
    import fitz
    from finance_agents.storage.database import create_database_engine
    from finance_agents.agents.claim_intake import ClaimIntakeAgent
    from finance_agents.agents.credit_assessment import CreditAssessmentAgent
    from finance_agents.contracts.profiles import ClaimForm
    from finance_agents.storage.document_store import DocumentStore
    from finance_agents.tools.document_service import DocumentService
    from finance_agents.workflow.state import SessionState

    store = DocumentStore(create_database_engine("sqlite:///:memory:"), tmp_path)

    def upload(amount):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((30, 50), f"{field_name}: {amount} CNY")
            return store.save("u", f"{amount}.pdf", pdf.tobytes(), "application/pdf")["document_id"]

    old, new = upload("8000"), upload("11000")
    service = DocumentService(store)
    old_fields = asyncio.run(service.extract("u", old)).fields
    if kind == "credit":
        form = complete_form()
        form.monthly_income.amount = Decimal("9000")
        form.confirmed_fields = list(CreditFormFiller.required_fields)
        form.document_ids = [old]
        profile = {"credit_form": form}
    else:
        form = ClaimForm(
            policy_id="DEMO-POLICY-001",
            policy_version="demo-v1",
            incident_type="hospitalization",
            incident_date="2026-10-10",
            premium=Money(amount="9000", currency="CNY"),
            confirmed_fields=[*ClaimIntakeAgent.required_fields, "premium"],
        )
        profile = {"claim_form": form}
    state = SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        document_refs=[old],
        extracted_fields=old_fields,
        **profile,
    )

    class UnavailableTools:
        calls = 0

        async def call(self, *args):
            from finance_agents.contracts.results import ToolResult

            self.calls += 1
            return ToolResult(status="unavailable", data_mode="real")

    tools = UnavailableTools()
    agent = (
        CreditAssessmentAgent(tools, service)
        if kind == "credit"
        else ClaimIntakeAgent(tools, service)
    )
    # The user deliberately corrected the OLD 8000 evidence to 9000.
    unchanged = asyncio.run(agent.run(state))
    assert not unchanged.structured_facts.get("conflicts")
    assert getattr(
        getattr(state, f"{kind if kind == 'claim' else 'credit'}_form"), field_name
    ).amount == Decimal("9000")
    assert (
        field_name
        in getattr(state, f"{kind if kind == 'claim' else 'credit'}_form").confirmed_fields
    )
    state.document_refs.append(new)
    state.message = "请继续"
    state.consent_ref = "consent-1"
    prior_calls = tools.calls
    reopened = asyncio.run(agent.run(state))
    assert reopened.status == "needs_input" and reopened.missing_fields == ["confirmed_fields"]
    updated_form = getattr(state, f"{kind if kind == 'claim' else 'credit'}_form")
    assert field_name not in updated_form.confirmed_fields
    assert tools.calls == prior_calls
    conflicts = reopened.structured_facts["conflicts"][field_name]
    assert any(c.get("document_id") == new for c in conflicts)
    assert any(
        c.get("value") == {"amount": "9000", "currency": "CNY"} and c.get("confirmed")
        for c in conflicts
    )
    # A second explicit correction resolves the now-reviewed evidence; the same
    # uploaded documents must not endlessly invalidate subsequent confirmation.
    setattr(updated_form, field_name, Money(amount="9500", currency="CNY"))
    updated_form.confirmed_fields.append(field_name)
    state.message = "已确认修正值"
    resolved = asyncio.run(agent.run(state))
    assert not resolved.structured_facts.get("conflicts")
    assert field_name in getattr(state, f"{kind}_form").confirmed_fields
    assert getattr(getattr(state, f"{kind}_form"), field_name).amount == Decimal("9500")


@pytest.mark.parametrize("token", ["1,23", "1.2.3"])
def test_malformed_numeric_token_is_not_partially_extracted(token):
    from finance_agents.tools.ocr import parse_text_fields

    assert not parse_text_fields(f"月收入：{token}元", "doc")
    valid = parse_text_fields("月收入：1,234.56元\n月还款：1.25万元", "doc")
    assert [f.value["amount"] for f in valid] == ["1234.56", "12500.00"]
