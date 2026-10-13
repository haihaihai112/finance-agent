"""Read daily NAV evidence and calculate only metrics supported by those observations."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from sqlalchemy.exc import SQLAlchemyError
from finance_agents.contracts.common import DataMode, ResultStatus, EvidenceRef
from finance_agents.contracts.results import ToolResult, ToolError


class MarketMCPService:
    name = "market"
    tools = ("market.query_prices", "market.search_context", "market.compute_metrics")

    def __init__(self, store, max_age_days=30):
        self.store, self.max_age_days = store, max_age_days

    async def call(self, tool_name, arguments, auth):
        def fail(status, code, message):
            return ToolResult(
                status=status, data_mode=DataMode.REAL, error=ToolError(code=code, message=message)
            )

        if not auth.user_id:
            return fail(ResultStatus.FAILED, "AUTH_REQUIRED", "User identity required")
        if tool_name not in self.tools:
            return fail(ResultStatus.FAILED, "UNKNOWN_TOOL", "Unknown market tool")
        try:
            instruments = arguments.get("instrument_ids", [])
            if (
                not isinstance(instruments, list)
                or not 1 <= len(instruments) <= 20
                or any(
                    not isinstance(i, str) or not re.fullmatch(r"[0-9]{6}", i) for i in instruments
                )
            ):
                raise ValueError("Require at most 20 six digit fund codes")
            today = date.today()
            start = (
                date.fromisoformat(arguments["start"])
                if arguments.get("start")
                else today - timedelta(days=365)
            )
            end = date.fromisoformat(arguments["end"]) if arguments.get("end") else today
            if start > end or end > today:
                raise ValueError("Invalid date interval")
            rows = self.store.query(instruments, start, end)
            grouped = {i: [r for r in rows if r.instrument_id == i] for i in instruments}
            if any(
                not rs or (today - rs[-1].trade_date).days > self.max_age_days
                for rs in grouped.values()
            ):
                return fail(
                    ResultStatus.UNAVAILABLE,
                    "NO_FRESH_MARKET_DATA",
                    "Requested fund lacks verified NAV in the past 30 days",
                )
            if any(len({r.source_id for r in rs}) > 1 for rs in grouped.values()):
                return fail(
                    ResultStatus.UNAVAILABLE,
                    "AMBIGUOUS_MARKET_SOURCE",
                    "Multiple sources need explicit reconciliation",
                )
            evidence = [
                EvidenceRef(
                    source_id=r.source_id,
                    source_title=f"公开日频基金单位净值 {r.instrument_id}",
                    source_uri=r.source_uri,
                    quote=f"{r.trade_date.isoformat()} 单位净值 {r.value} {r.currency}",
                    retrieved_at=r.fetched_at.replace(tzinfo=timezone.utc),
                )
                for rs in grouped.values()
                for r in [rs[-1]]
            ]
            as_of = datetime.combine(
                min(rs[-1].trade_date for rs in grouped.values()),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            if tool_name == "market.compute_metrics":
                metrics = {}
                for instrument, rs in grouped.items():
                    if len(rs) < 2 or any(r.value <= 0 for r in rs):
                        return fail(
                            ResultStatus.UNAVAILABLE,
                            "INSUFFICIENT_HISTORY",
                            "At least two positive NAV observations required",
                        )
                    first, last = rs[0], rs[-1]
                    peak = first.value
                    drawdown = Decimal("0")
                    for r in rs:
                        peak = max(peak, r.value)
                        drawdown = max(drawdown, 1 - r.value / peak)
                    metrics[instrument] = {
                        "first_date": first.trade_date.isoformat(),
                        "last_date": last.trade_date.isoformat(),
                        "observations": len(rs),
                        "nav_change": str(
                            (last.value / first.value - 1).quantize(Decimal(".000001"))
                        ),
                        "observed_max_drawdown": str(drawdown.quantize(Decimal(".000001"))),
                    }
                data = {
                    "metrics": metrics,
                    "metric_basis": "单位净值变化，不含分红再投资；不是总收益率或预测收益",
                }
            else:
                data = {
                    "records": [r.model_dump(mode="json") for r in rows],
                    "frequency": "daily_published_nav",
                }
            return ToolResult(
                status=ResultStatus.SUCCESS,
                data_mode=DataMode.REAL,
                data=data,
                evidence=evidence,
                as_of=as_of,
                warnings=["日频公开净值，非实时交易报价；公开接口无可用性保证。"],
            )
        except (KeyError, ValueError, TypeError) as exc:
            return fail(ResultStatus.FAILED, "INVALID_ARGUMENT", str(exc))
        except SQLAlchemyError:
            return ToolResult(
                status=ResultStatus.UNAVAILABLE,
                data_mode=DataMode.REAL,
                error=ToolError(
                    code="STORAGE_UNAVAILABLE",
                    message="Market database unavailable",
                    retryable=True,
                ),
            )
