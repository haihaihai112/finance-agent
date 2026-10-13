"""Read-only report projection: never serialize workflow state or submitted forms."""

import json
import re

from finance_agents.workflow.state import SessionState


FACT_SECTIONS = {
    "market",
    "as_of",
    "strategy_version",
    "strategy_weights",
    "assessment",
    "mock_credit_report",
    "policy_history",
    "clause_check",
    "policy",
}
PRIVATE_KEYS = {
    "form",
    "profile",
    "credit_form",
    "claim_form",
    "tax_profile",
    "investment_profile",
    "consent_ref",
    "token",
    "token_hash",
    "api_key",
    "authorization",
    "password",
    "secret",
    "credentials",
    "access_token",
    "refresh_token",
    "subject_ref",
    "user_id",
    "session_id",
}


def public_value(value):
    """Defend approved result sections against nested credential/form fields."""
    if isinstance(value, dict):
        return {
            key: public_value(item)
            for key, item in value.items()
            if str(key).lower() not in PRIVATE_KEYS
            and not any(
                part in str(key).lower()
                for part in ("token", "secret", "password", "consent", "credential")
            )
        }
    if isinstance(value, list):
        return [public_value(item) for item in value]
    return value


def build_report(state: SessionState):
    if state.final_response is None:
        raise ValueError("尚无结果，不能导出报告")
    final = state.final_response.model_dump(mode="json")
    facts = final["facts"]
    result = {
        key: final[key]
        for key in ("conclusion", "citations", "limitations", "next_action", "data_mode")
    }
    result["facts"] = {
        "structured_facts": public_value(
            {
                key: value
                for key, value in facts.get("structured_facts", {}).items()
                if key in FACT_SECTIONS
            }
        ),
        "calculations": public_value(facts.get("calculations", {})),
    }
    return {
        "run_id": state.run_id,
        "parent_run_id": state.parent_run_id,
        "intent": state.intent.value if state.intent else None,
        "status": state.status.value,
        "state_version": state.state_version,
        "result": result,
        "data_scope": {
            "jurisdiction": "中国大陆",
            "currency": "CNY",
            "market_frequency": "daily_published_nav",
            "tax_scope": "resident_comprehensive_annual",
            "data_mode": final["data_mode"],
            "credit_mode": "mock",
            "external_services_verified": False,
        },
    }


def render_report(report, format):
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if format == "json":
        return serialized, "application/json", "json"
    # A dynamic fence prevents report text from prematurely closing the code block.
    fence = "`" * max(
        3, max((len(m.group()) + 1 for m in re.finditer(r"`+", serialized)), default=3)
    )
    return (
        "# 金融工作流结果报告\n\n金额按原始字符串保留；事实、来源、限制与数据范围如下。\n\n"
        + fence
        + "json\n"
        + serialized
        + "\n"
        + fence
        + "\n",
        "text/markdown",
        "md",
    )
