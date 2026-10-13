"""Deterministic demonstration rules, never an actual lender underwriting model."""

from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from finance_agents.contracts.common import AuthContext, DataMode, ResultStatus
from finance_agents.contracts.profiles import CreditModelInput
from finance_agents.tools.form_filler import CreditFormFiller
from finance_agents.contracts.results import ToolError, ToolResult
from finance_agents.mcp_services.base import MCPServiceSkeleton


class CreditMCPService(MCPServiceSkeleton):
    name = "credit"
    tools = ("credit.query_mock_report", "credit.evaluate_mock")
    rule_version = "demo-v1"

    def _reject(self, message, code="INVALID_INPUT"):
        return ToolResult(
            status=ResultStatus.FAILED,
            data_mode=DataMode.MOCK,
            error=ToolError(code=code, message=message),
        )

    async def call(self, tool_name: str, arguments: dict[str, Any], auth: AuthContext):
        if tool_name not in self.tools:
            return self._reject("Unknown credit tool", "UNKNOWN_TOOL")
        if (
            not auth.consent_ref
            or arguments.get("consent_ref", auth.consent_ref) != auth.consent_ref
        ):
            return self._reject("Valid matching consent_ref is required", "CONSENT_REQUIRED")
        if "user_id" in arguments and arguments["user_id"] != auth.user_id:
            return self._reject("Cross-user query denied", "FORBIDDEN")
        if tool_name == "credit.query_mock_report":
            return ToolResult(
                status=ResultStatus.SUCCESS,
                data_mode=DataMode.MOCK,
                data={
                    "subject_ref": arguments.get("subject_ref"),
                    "report": "synthetic demonstration; no real bureau query",
                    "consent_ref": auth.consent_ref,
                },
                warnings=["No real credit bureau connection or real credit history."],
            )
        confirmed = arguments.get("confirmed_fields", [])
        if not isinstance(confirmed, list) or not all(
            name in confirmed for name in CreditFormFiller.required_fields
        ):
            return self._reject("Every required field must be confirmed", "UNCONFIRMED_FIELDS")
        try:
            row = arguments.get("model_input", arguments)
            return await self.evaluate_form(CreditModelInput.model_validate(row), auth)
        except (ValidationError, ValueError) as exc:
            return self._reject(str(exc))

    async def evaluate_form(self, model_input: CreditModelInput, auth: AuthContext):
        if not auth.consent_ref or auth.consent_ref != model_input.consent_ref:
            return self._reject("Valid matching consent_ref is required", "CONSENT_REQUIRED")
        if model_input.rule_version != self.rule_version:
            return self._reject("Unsupported demonstration rule version")
        income = model_input.monthly_income
        installment = model_input.requested_amount / Decimal(model_input.loan_term_months)
        ratio = (model_input.monthly_debt_payment + installment) / income if income > 0 else None
        category = "manual_review" if ratio is None or ratio > Decimal("0.5") else "demo_pass"
        return ToolResult(
            status=ResultStatus.SUCCESS,
            data_mode=DataMode.MOCK,
            data={
                "rule_version": self.rule_version,
                "result": category,
                "table_row": model_input.model_dump(mode="json"),
                "principal_only_monthly_payment": str(installment.quantize(Decimal("0.01"))),
                "debt_service_ratio": str(ratio.quantize(Decimal("0.0001")))
                if ratio is not None
                else None,
                "formula": "(monthly_debt_payment + requested_amount / loan_term_months) / monthly_income",
            },
            warnings=[
                "Synthetic demonstration rule with threshold 0.5; principal only, no interest/fees. This is not a real bank model or lending decision."
            ],
        )
