"""用于结构审阅的流程声明；此文件不是已经执行的 LangGraph。"""

from finance_agents.contracts.results import Intent

AGENTS = (
    "intent_router",
    "asset_allocation",
    "claim_intake",
    "credit_assessment",
    "tax_policy",
    "output_optimizer",
)

MCP_SERVICES = ("market", "claims", "credit", "tax")

BRANCH_STEPS = {
    Intent.INVESTMENT: (
        "check_investment_profile",
        "ask_missing_information",
        "market_mcp",
        "risk_return_calculation",
        "output_optimizer",
    ),
    Intent.CLAIM: (
        "request_claim_documents",
        "ocr_extract",
        "extract_claim_fields",
        "confirm_claim_fields",
        "claims_mcp",
        "lookup_historical_policies_mysql",
        "match_policy_clauses",
        "output_optimizer",
    ),
    Intent.LOAN: (
        "request_credit_documents",
        "ocr_extract",
        "prefill_credit_form",
        "ask_missing_or_conflicting_fields",
        "confirm_credit_form",
        "check_consent",
        "credit_mcp",
        "output_optimizer",
    ),
    Intent.POLICY_TAX: (
        "check_jurisdiction_and_tax_year",
        "tax_mcp",
        "deterministic_calculation",
        "output_optimizer",
    ),
    Intent.OTHER: ("out_of_scope_notice", "display"),
}
