"""专业表单契约：未确认的信息保留为空，绝不默填。"""

from datetime import date
from decimal import Decimal

from pydantic import Field

from finance_agents.contracts.common import Contract, Money


class InvestmentProfile(Contract):
    investable_amount: Money | None = None
    horizon_months: int | None = Field(default=None, ge=1)
    risk_tolerance: str | None = None
    liquidity_need: Money | None = None
    liabilities: Money | None = None
    existing_assets: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    instrument_ids: list[str] = Field(default_factory=list)
    confirmed_fields: list[str] = Field(default_factory=list)


class ClaimForm(Contract):
    policy_id: str | None = None
    insurer: str | None = None
    insurance_type: str | None = None
    policy_version: str | None = None
    premium: Money | None = None
    insured_amount: Money | None = None
    incident_type: str | None = None
    incident_date: date | None = None
    confirmed_fields: list[str] = Field(default_factory=list)


class CreditForm(Contract):
    """风控表格的一行。OCR 与提示词可预填，用户确认前不可提交风控。"""

    subject_ref: str | None = None
    requested_amount: Money | None = None
    loan_term_months: int | None = Field(default=None, ge=1)
    monthly_income: Money | None = None
    monthly_debt_payment: Money | None = None
    employment_status: str | None = None
    employment_months: int | None = Field(default=None, ge=0)
    assets_total: Money | None = None
    liabilities_total: Money | None = None
    document_ids: list[str] = Field(default_factory=list)
    confirmed_fields: list[str] = Field(default_factory=list)
    consent_ref: str | None = None
    form_version: int = Field(default=1, ge=1)


class CreditModelInput(Contract):
    """确认后的模型输入示意；最终字段须按实际风控模型契约修订。"""

    subject_ref: str
    currency: str = Field(min_length=3, max_length=3)
    requested_amount: Decimal = Field(gt=0, allow_inf_nan=False)
    loan_term_months: int = Field(ge=1)
    monthly_income: Decimal = Field(ge=0, allow_inf_nan=False)
    monthly_debt_payment: Decimal = Field(ge=0, allow_inf_nan=False)
    employment_status: str
    consent_ref: str
    rule_version: str


class TaxProfile(Contract):
    jurisdiction: str | None = None
    tax_year: int | None = Field(default=None, ge=1900, le=2200)
    income_type: str | None = None
    income: Money | None = None
    deductions: Money | None = None
    annual_income: Money | None = None
    annual_deductions: Money | None = None
    resident_status: str | None = None
    confirmed_fields: list[str] = Field(default_factory=list)
