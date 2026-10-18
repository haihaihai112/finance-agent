"""Resident mainland annual comprehensive income tax; Decimal calculation, verified law."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from sqlalchemy.exc import SQLAlchemyError
from finance_agents.contracts.common import DataMode, ResultStatus
from finance_agents.contracts.results import ToolResult, ToolError
from finance_agents.storage.policy_store import BRACKETS
from finance_agents.tools.retrieval import EvidenceRetriever


def annual_income_type(value):
    aliases = {
        "annual_comprehensive_income",
        "年度综合所得",
        "居民年度综合所得",
        "居民综合所得（年度）",
    }
    return "resident_comprehensive_annual" if value in aliases else value


def calculate_annual_tax(annual_income, annual_deductions, brackets=None, basic_deduction="60000"):
    income, deductions = Decimal(str(annual_income)), Decimal(str(annual_deductions))
    if any(not x.is_finite() or x < 0 for x in (income, deductions)):
        raise ValueError("Income and annual deductions must be finite and nonnegative")
    taxable = max(Decimal("0"), income - Decimal(basic_deduction) - deductions)
    for upper, rate, quick in brackets or BRACKETS:
        if upper is None or taxable <= Decimal(upper):
            break
    tax = (taxable * Decimal(rate) - Decimal(quick)).quantize(
        Decimal(".01"), rounding=ROUND_HALF_UP
    )
    return {
        "annual_income": str(income),
        "annual_deductions": str(deductions),
        "basic_annual_deduction": str(basic_deduction),
        "taxable_income": str(taxable),
        "rate": rate,
        "quick_deduction": quick,
        "annual_tax": str(tax),
        "currency": "CNY",
        "calculation_type": "annual_liability_before_prepaid_tax",
        "formula": "max(0, annual_income - 60000 - annual_deductions) * rate - quick_deduction",
    }


class TaxMCPService:
    name = "tax"
    tools = ("tax.search_rules", "tax.calculate")

    def __init__(self, store):
        self.store = store

    async def call(self, tool_name, arguments, auth):
        def failure(status, code, message):
            return ToolResult(
                status=status, data_mode=DataMode.REAL, error=ToolError(code=code, message=message)
            )

        if not auth.user_id:
            return failure(ResultStatus.FAILED, "AUTH_REQUIRED", "User identity required")
        if tool_name not in self.tools:
            return failure(ResultStatus.FAILED, "UNKNOWN_TOOL", "Unknown tax tool")
        try:
            query = arguments.get("query", "综合所得")
            if any(
                term in query
                for term in [
                    "增值税",
                    "企业所得税",
                    "经营所得",
                    "非居民",
                    "财产转让",
                    "股息",
                    "印花税",
                    "契税",
                    "年终奖",
                    "全年一次性奖金",
                ]
            ):
                return failure(
                    ResultStatus.UNAVAILABLE,
                    "POLICY_SCOPE_UNSUPPORTED",
                    "首版仅支持中国大陆居民年度综合所得；该问题超出已核验政策范围，请新建受支持的年度综合所得请求。",
                )
            jurisdiction = arguments["jurisdiction"]
            year = int(arguments["tax_year"])
            on_date = date(year, 12, 31)
            if on_date > date.today():
                on_date = date(year, 1, 1)
            if year > date.today().year:
                return failure(
                    ResultStatus.UNAVAILABLE, "FUTURE_RULES", "Future tax law not verified"
                )
            rows = self.store.search(arguments.get("query", "综合所得"), jurisdiction, on_date)
            if not rows:
                return failure(
                    ResultStatus.UNAVAILABLE, "NO_EFFECTIVE_POLICY", "No verified effective policy"
                )
            refs = await EvidenceRetriever(self.store, jurisdiction, on_date).search(
                arguments.get("query", "综合所得"), auth, int(arguments.get("token_budget", 6000))
            )
            if tool_name == "tax.search_rules":
                return ToolResult(
                    status=ResultStatus.SUCCESS,
                    data_mode=DataMode.REAL,
                    data={
                        "jurisdiction": jurisdiction,
                        "tax_year": year,
                        "versions": [r["version"] for r in rows],
                        "token_budget_method": "conservative UTF-8 byte bound; not exact model tokens",
                    },
                    evidence=refs,
                )
            if (
                arguments.get("currency") != "CNY"
                or arguments.get("resident_status") != "resident"
                or annual_income_type(arguments.get("income_type"))
                != "resident_comprehensive_annual"
            ):
                return failure(
                    ResultStatus.UNAVAILABLE,
                    "UNSUPPORTED_TAX_PROFILE",
                    "仅支持人民币、居民个人和年度综合所得（resident_comprehensive_annual）；请复制为新试算，修正并确认字段。",
                )
            rule_map = {
                r["source_id"]: json.loads(r["rules_json"]) for r in rows if r["rules_json"]
            }
            if not {"cn_iit_law", "cn_iit_rates"} <= rule_map.keys():
                return failure(
                    ResultStatus.UNAVAILABLE,
                    "MISSING_STRUCTURED_RULE",
                    "No complete verified calculation rules",
                )
            result = calculate_annual_tax(
                arguments["annual_income"],
                arguments["annual_deductions"],
                rule_map["cn_iit_rates"]["brackets"],
                rule_map["cn_iit_law"]["basic_annual_deduction"],
            )
            return ToolResult(
                status=ResultStatus.SUCCESS,
                data_mode=DataMode.REAL,
                data=result,
                evidence=refs,
                as_of=datetime.combine(on_date, datetime.min.time(), tzinfo=timezone.utc),
                warnings=[
                    "仅试算年度税额；未减已预缴税额，不等于补税额或退税额。",
                    "收入须为年度综合所得收入额，非未经换算的劳务等原始流水；扣除为60000之外的年度合计。",
                ],
            )
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            return failure(ResultStatus.FAILED, "INVALID_ARGUMENT", str(exc))
        except SQLAlchemyError:
            return ToolResult(
                status=ResultStatus.UNAVAILABLE,
                data_mode=DataMode.REAL,
                error=ToolError(
                    code="STORAGE_UNAVAILABLE",
                    message="Policy database unavailable",
                    retryable=True,
                ),
            )
