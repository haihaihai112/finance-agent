"""Tax profile confirmation precedes policy lookup and deterministic calculation."""

from finance_agents.contracts.common import AuthContext, DataMode, ResultStatus
from finance_agents.contracts.profiles import TaxProfile
from finance_agents.contracts.results import AgentResult


class TaxPolicyAgent:
    def __init__(self, mcp):
        self.mcp = mcp

    async def run(self, state):
        profile = state.tax_profile or TaxProfile()
        state.tax_profile = profile
        business_message = "\n".join(
            dict.fromkeys(filter(None, [getattr(state, "initial_message", ""), state.message]))
        )
        if profile.income_type == "annual_bonus" or any(
            term in business_message for term in ["年终奖", "全年一次性奖金"]
        ):
            return AgentResult(
                agent_type="tax_policy",
                status=ResultStatus.UNAVAILABLE,
                data_mode=DataMode.REAL,
                summary="首版尚不支持年终奖单独计税，不能用年度综合所得公式代替。",
                next_action="如需居民年度综合所得试算，请新建请求并提交全年收入额；年终奖单独计税请使用税务机关提供的渠道核算。",
            )
        calculate = profile.annual_income is not None or any(
            word in business_message for word in ["计算", "试算", "多少", "税额"]
        )
        required = ["jurisdiction", "tax_year"]
        if calculate:
            required += ["income_type", "annual_income", "annual_deductions", "resident_status"]
        missing = [
            f"tax_profile.{key}"
            for key in required
            if getattr(profile, key, None) is None or key not in profile.confirmed_fields
        ]
        if missing:
            return AgentResult(
                agent_type="tax_policy",
                status=ResultStatus.NEEDS_INPUT,
                data_mode=DataMode.REAL,
                summary="请确认地域与税年；年度税额试算还需居民身份、年度综合所得收入额、人民币币种及60000元基本减除费用之外的年度扣除合计。",
                missing_fields=missing,
                next_action="请在表单确认地域、税年和居民年度综合所得；金额为全年人民币金额，扣除不含60000元基本减除费用，无其他扣除须填0。",
            )
        arguments = {
            "jurisdiction": profile.jurisdiction,
            "tax_year": profile.tax_year,
            "query": business_message or "综合所得",
        }
        if calculate:
            if profile.annual_income.currency != profile.annual_deductions.currency:
                return AgentResult(
                    agent_type="tax_policy",
                    status=ResultStatus.NEEDS_INPUT,
                    data_mode=DataMode.REAL,
                    summary="收入和扣除币种不一致，请统一确认人民币金额。",
                    missing_fields=["tax_profile.annual_income", "tax_profile.annual_deductions"],
                )
            arguments.update(
                annual_income=str(profile.annual_income.amount),
                annual_deductions=str(profile.annual_deductions.amount),
                currency=profile.annual_income.currency,
                resident_status=profile.resident_status,
                income_type=profile.income_type,
            )
        result = await self.mcp.call(
            "tax.calculate" if calculate else "tax.search_rules",
            arguments,
            AuthContext(user_id=state.user_id, session_id=state.session_id),
        )
        return AgentResult(
            agent_type="tax_policy",
            status=result.status,
            data_mode=result.data_mode,
            summary=(
                "已按官方居民综合所得年度七档税率试算。"
                if calculate
                else "已检索指定地域和税年生效的综合所得政策。"
            )
            if result.status == ResultStatus.SUCCESS
            else "无法取得符合条件的已核验税务结果。",
            structured_facts={"policy": result.data} if not calculate and result.data else {},
            calculations=result.data if calculate and result.data else {},
            evidence=result.evidence,
            warnings=result.warnings + ([result.error.message] if result.error else []),
            next_action=None
            if result.status == ResultStatus.SUCCESS
            else "请检查支持范围与字段格式；已结束的运行可复制为新试算，重新确认后提交。超出范围的问题请新建受支持请求。",
        )
