"""Transparent allocation templates; no performance guarantee or invented instrument identity."""

from decimal import Decimal, ROUND_HALF_UP
from finance_agents.contracts.common import AuthContext, DataMode, ResultStatus
from finance_agents.contracts.results import AgentResult
from finance_agents.contracts.profiles import InvestmentProfile

TEMPLATES = {
    "low": ("0.70", "0.25", "0.05"),
    "medium": ("0.30", "0.40", "0.30"),
    "high": ("0.10", "0.30", "0.60"),
}
RISK_ALIASES = {
    "保守": "low",
    "低": "low",
    "低风险": "low",
    "稳健": "medium",
    "中": "medium",
    "中风险": "medium",
    "积极": "high",
    "高": "high",
    "高风险": "high",
}


def allocate_amount(amount, risk, liquidity_need, horizon_months):
    amount = Decimal(str(amount))
    reserve = Decimal(str(liquidity_need))
    risk = RISK_ALIASES.get(risk, risk)
    if (
        risk not in TEMPLATES
        or amount <= 0
        or reserve < 0
        or reserve > amount
        or any(x != x.quantize(Decimal(".01")) for x in [amount, reserve])
    ):
        raise ValueError("Invalid allocation profile")
    weights = [Decimal(x) for x in TEMPLATES[risk]]
    if horizon_months < 12:
        weights[0] += weights[2]
        weights[2] = Decimal(0)
    cash = max((amount * weights[0]).quantize(Decimal(".01"), rounding=ROUND_HALF_UP), reserve)
    remaining = amount - cash
    non_cash = weights[1] + weights[2]
    bond = (
        (remaining * weights[1] / non_cash).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if non_cash
        else remaining
    )
    amounts = [cash, bond, amount - cash - bond]
    final_weights = [(v / amount).quantize(Decimal(".00000001")) for v in amounts[:2]]
    final_weights.append(1 - sum(final_weights))
    return [
        dict(category=category, amount=str(value), weight=str(weight), currency="CNY")
        for category, value, weight in zip(
            ["现金储备", "债券基金类别", "宽基指数基金类别"], amounts, final_weights
        )
    ]


class AssetAllocationAgent:
    def __init__(self, mcp):
        self.mcp = mcp

    async def run(self, state):
        profile = state.investment_profile or InvestmentProfile()
        state.investment_profile = profile
        required = [
            "jurisdiction",
            "investable_amount",
            "horizon_months",
            "risk_tolerance",
            "liquidity_need",
            "liabilities",
            "existing_assets",
            "instrument_ids",
        ]
        missing = [
            f"investment_profile.{key}"
            for key in required
            if (
                getattr(profile, key, None) is None
                or (key == "instrument_ids" and not getattr(profile, key, []))
                or key not in profile.confirmed_fields
            )
        ]
        if missing:
            return AgentResult(
                agent_type="asset_allocation",
                status=ResultStatus.NEEDS_INPUT,
                data_mode=DataMode.REAL,
                summary="请补充并确认地域、人民币可投资金额、期限、风险程度、流动资金需求、负债、现有资产及需要查询的六位基金代码。",
                missing_fields=missing,
                next_action="补充并确认投资画像；无负债或资产时请明确填0或空列表。",
            )
        if profile.jurisdiction not in {"中国大陆", "CN", "CN-mainland", "中国"} or any(
            x.currency != "CNY"
            for x in [profile.investable_amount, profile.liquidity_need, profile.liabilities]
        ):
            return AgentResult(
                agent_type="asset_allocation",
                status=ResultStatus.UNAVAILABLE,
                data_mode=DataMode.REAL,
                summary="首版支持中国大陆人民币基金类别配置。",
                warnings=["地域或币种不受支持。"],
            )
        try:
            allocation = allocate_amount(
                profile.investable_amount.amount,
                profile.risk_tolerance,
                profile.liquidity_need.amount,
                profile.horizon_months,
            )
        except ValueError:
            return AgentResult(
                agent_type="asset_allocation",
                status=ResultStatus.NEEDS_INPUT,
                data_mode=DataMode.REAL,
                summary="请确认有效风险程度及不超过可投资金额的流动资金需求。",
                missing_fields=[
                    "investment_profile.risk_tolerance",
                    "investment_profile.liquidity_need",
                ],
            )
        tool = await self.mcp.call(
            "market.query_prices",
            {"instrument_ids": profile.instrument_ids},
            AuthContext(user_id=state.user_id, session_id=state.session_id),
        )
        if tool.status != ResultStatus.SUCCESS:
            return AgentResult(
                agent_type="asset_allocation",
                status=tool.status,
                data_mode=tool.data_mode,
                summary="公开基金净值暂不可用，无法给出有行情依据的投资结论。",
                warnings=tool.warnings + ([tool.error.message] if tool.error else []),
            )
        metrics = await self.mcp.call(
            "market.compute_metrics",
            {"instrument_ids": profile.instrument_ids},
            AuthContext(user_id=state.user_id, session_id=state.session_id),
        )
        if metrics.status != ResultStatus.SUCCESS:
            return AgentResult(
                agent_type="asset_allocation",
                status=metrics.status,
                data_mode=metrics.data_mode,
                summary="已取得净值，但历史数据不足以形成可核验收益风险指标。",
                evidence=tool.evidence,
                warnings=metrics.warnings + ([metrics.error.message] if metrics.error else []),
            )
        return AgentResult(
            agent_type="asset_allocation",
            status=ResultStatus.SUCCESS,
            data_mode=tool.data_mode,
            summary="根据已确认画像给出可复算的基金类别配置模板；查询基金仅用于净值参考，不代表该基金适合任一配置类别。",
            structured_facts={
                "profile": profile.model_dump(mode="json"),
                "market": tool.data,
                "as_of": tool.as_of.isoformat() if tool.as_of else None,
                "strategy_version": "transparent-category-template-v1",
                "strategy_weights": TEMPLATES[
                    RISK_ALIASES.get(profile.risk_tolerance, profile.risk_tolerance)
                ],
            },
            calculations={
                "allocation": allocation,
                "historical_metrics": metrics.data["metrics"],
                "metric_basis": metrics.data["metric_basis"],
            },
            evidence=tool.evidence,
            warnings=tool.warnings
            + [
                "配置为固定策略模板，未预测收益。",
                "历史指标仅单位净值变化及已观测最大回撤，不含分红再投资或费用，不等于总收益率。",
                "负债与现有资产已记录；模板未评估负债利率及穿透持仓风险。",
                "短于12个月时模板不配置权益类别；流动资金需求优先。",
            ],
        )
