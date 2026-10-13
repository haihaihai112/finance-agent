"""Merge evidence without silently choosing conflicting financial values."""

from collections import defaultdict
from decimal import Decimal

from finance_agents.contracts.documents import ExtractedField
from finance_agents.contracts.profiles import CreditForm, CreditModelInput


def canonical_value(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict) and "amount" in value and "currency" in value:
        return (Decimal(str(value["amount"])), str(value["currency"]).upper())
    return str(value)


class CreditFormFiller:
    required_fields = (
        "subject_ref",
        "requested_amount",
        "loan_term_months",
        "monthly_income",
        "monthly_debt_payment",
        "employment_status",
    )

    def __init__(self):
        self.conflicts: dict[str, list[dict]] = {}

    async def prefill(
        self,
        fields: list[ExtractedField],
        user_message: str,
        current_form: CreditForm | None = None,
    ) -> CreditForm:
        from finance_agents.tools.ocr import parse_text_fields

        form = (current_form or CreditForm()).model_copy(deep=True)
        self.conflicts = {}
        grouped = defaultdict(list)
        for field in fields + parse_text_fields(user_message, None, source="user_message"):
            if field.name in CreditForm.model_fields and field.name not in {
                "confirmed_fields",
                "consent_ref",
                "form_version",
                "document_ids",
            }:
                grouped[field.name].append(field)
        data = form.model_dump(mode="json")
        for name, values in grouped.items():
            unique = {canonical_value(v.value) for v in values}
            if name in form.confirmed_fields:
                fresh = [
                    v
                    for v in values
                    if v.source == "user_message"
                    or (v.document_id and v.document_id not in form.document_ids)
                ]
                if not fresh:
                    continue
                if all(canonical_value(v.value) == canonical_value(data[name]) for v in fresh):
                    continue
                self.conflicts[name] = [
                    {
                        "value": data[name],
                        "source": "confirmed_form",
                        "confirmed": True,
                        "document_ids": form.document_ids,
                    },
                    *[v.model_dump(mode="json") for v in values],
                ]
                data[name] = None
                data["confirmed_fields"] = [n for n in data["confirmed_fields"] if n != name]
                continue
            if len(unique) > 1:
                self.conflicts[name] = [v.model_dump(mode="json") for v in values]
                data[name] = None
                data["confirmed_fields"] = [n for n in data["confirmed_fields"] if n != name]
            else:
                value = values[0].value
                if data.get(name) is not None and canonical_value(data[name]) != canonical_value(
                    value
                ):
                    self.conflicts[name] = [
                        {"value": data[name], "source": "current_form"},
                        *[v.model_dump(mode="json") for v in values],
                    ]
                    data[name] = None
                    data["confirmed_fields"] = [n for n in data["confirmed_fields"] if n != name]
                else:
                    data[name] = value
        data["document_ids"] = list(
            dict.fromkeys(form.document_ids + [f.document_id for f in fields if f.document_id])
        )
        return CreditForm.model_validate(data)

    def to_model_input(self, form: CreditForm, rule_version: str) -> CreditModelInput:
        missing = [n for n in self.required_fields if getattr(form, n) is None]
        if missing:
            raise ValueError("missing fields: " + ", ".join(missing))
        unconfirmed = [n for n in self.required_fields if n not in form.confirmed_fields]
        unconfirmed += [
            n
            for n in ("employment_months", "assets_total", "liabilities_total")
            if getattr(form, n) is not None and n not in form.confirmed_fields
        ]
        if unconfirmed:
            raise ValueError("unconfirmed fields: " + ", ".join(unconfirmed))
        if not form.consent_ref:
            raise ValueError("consent_ref is required")
        monies = [form.requested_amount, form.monthly_income, form.monthly_debt_payment]
        monies += [v for v in (form.assets_total, form.liabilities_total) if v is not None]
        currencies = {v.currency.upper() for v in monies}
        if len(currencies) != 1:
            raise ValueError("currency mismatch; confirmed conversion is required")
        if currencies != {"CNY"}:
            raise ValueError(
                "Only CNY currency is supported for this mainland demonstration; no currency conversion is performed"
            )
        return CreditModelInput(
            subject_ref=form.subject_ref,
            currency=currencies.pop(),
            requested_amount=form.requested_amount.amount,
            loan_term_months=form.loan_term_months,
            monthly_income=form.monthly_income.amount,
            monthly_debt_payment=form.monthly_debt_payment.amount,
            employment_status=form.employment_status,
            consent_ref=form.consent_ref,
            rule_version=rule_version,
        )
