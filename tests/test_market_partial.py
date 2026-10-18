"""Mixed fund requests retain verified evidence without inventing missing NAV."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from finance_agents.agents.asset_allocation import AssetAllocationAgent
from finance_agents.contracts.common import AuthContext
from finance_agents.contracts.profiles import InvestmentProfile
from finance_agents.data_jobs.models import MarketRecord
from finance_agents.mcp_services.market import MarketMCPService
from finance_agents.storage.market_store import MarketStore
from finance_agents.workflow.state import SessionState


def nav(code, days_ago, value, source="eastmoney_fund_nav"):
    return MarketRecord(
        source_id=source,
        instrument_id=code,
        trade_date=date.today() - timedelta(days=days_ago),
        record_type="fund_nav",
        value=Decimal(value),
        currency="CNY",
        source_uri=f"https://fundf10.eastmoney.com/jjjz_{code}.html",
        fetched_at=datetime.now(timezone.utc),
        content_hash=f"{code}-{days_ago}-{value}-{source}",
    )


@pytest.fixture
def store():
    engine = create_engine("sqlite://")
    market = MarketStore(engine)
    asyncio.run(market.upsert_batch([nav("510300", 2, "1"), nav("510300", 0, "1.1")], "seed"))
    yield market
    engine.dispose()


def call(store, tool, codes):
    return asyncio.run(
        MarketMCPService(store).call(
            tool, {"instrument_ids": codes}, AuthContext(user_id="u", session_id="s")
        )
    )


@pytest.mark.parametrize("tool", ["market.query_prices", "market.compute_metrics"])
def test_missing_fund_does_not_discard_other_funds_verified_evidence(store, tool):
    result = call(store, tool, ["510300", "511990"])
    assert result.status == "partial"
    assert result.data["requested_instrument_ids"] == ["510300", "511990"]
    assert result.data["available_instrument_ids"] == ["510300"]
    assert result.data["missing_instrument_ids"] == ["511990"]
    assert result.data["stale_instrument_ids"] == []
    assert result.data["unavailable_instruments"] == {"511990": "NO_MARKET_DATA"}
    assert len(result.evidence) == 1
    assert "510300" in result.evidence[0].source_title
    assert "511990" not in result.evidence[0].source_title
    if tool == "market.query_prices":
        assert [row["instrument_id"] for row in result.data["records"]] == ["510300", "510300"]
    else:
        assert set(result.data["metrics"]) == {"510300"}
        assert result.data["metrics"]["510300"]["nav_change"] == "0.100000"
    assert any("511990" in warning and "未入库" in warning for warning in result.warnings)


@pytest.mark.parametrize("tool", ["market.query_prices", "market.compute_metrics"])
def test_stale_nav_is_excluded_without_being_misreported_as_missing(store, tool):
    asyncio.run(store.upsert_batch([nav("511990", 31, "2")], "stale"))
    result = call(store, tool, ["510300", "511990"])
    assert result.status == "partial"
    assert result.data["missing_instrument_ids"] == []
    assert result.data["stale_instrument_ids"] == ["511990"]
    assert result.data["unavailable_instruments"]["511990"] == "STALE_MARKET_DATA"
    assert result.data["available_instrument_ids"] == ["510300"]
    assert len(result.evidence) == 1


@pytest.mark.parametrize("tool", ["market.query_prices", "market.compute_metrics"])
def test_conflicting_sources_do_not_poison_other_fund_results(store, tool):
    asyncio.run(store.upsert_batch([nav("511990", 0, "1")], "first-source"))
    asyncio.run(store.upsert_batch([nav("511990", 0, "2", "other")], "other-source"))
    result = call(store, tool, ["510300", "511990"])
    assert result.status == "partial"
    assert result.data["available_instrument_ids"] == ["510300"]
    assert result.data["unavailable_instruments"]["511990"] == "AMBIGUOUS_MARKET_SOURCE"
    assert len(result.evidence) == 1


def test_insufficient_history_is_excluded_only_from_metrics(store):
    asyncio.run(store.upsert_batch([nav("511990", 0, "2")], "one-point"))
    prices = call(store, "market.query_prices", ["510300", "511990"])
    assert prices.status == "success"
    metrics = call(store, "market.compute_metrics", ["510300", "511990"])
    assert metrics.status == "partial"
    assert metrics.data["available_instrument_ids"] == ["510300"]
    assert metrics.data["unavailable_instruments"]["511990"] == "INSUFFICIENT_HISTORY"
    assert set(metrics.data["metrics"]) == {"510300"}
    assert len(metrics.evidence) == 1


def test_all_missing_stays_unavailable_and_names_missing_codes(store):
    result = call(store, "market.query_prices", ["511990"])
    assert result.status == "unavailable"
    assert result.data is None
    assert result.evidence == []
    assert result.error.code == "NO_FRESH_MARKET_DATA"
    assert "511990" in result.error.message


def test_all_present_preserves_success_and_real_metrics(store):
    prices = call(store, "market.query_prices", ["510300"])
    metrics = call(store, "market.compute_metrics", ["510300"])
    assert prices.status == metrics.status == "success"
    assert len(prices.data["records"]) == 2
    assert metrics.data["metrics"]["510300"]["observations"] == 2
    assert metrics.data["metrics"]["510300"]["nav_change"] == "0.100000"


def investor(codes):
    values = {
        "jurisdiction": "中国大陆",
        "investable_amount": {"amount": "100000", "currency": "CNY"},
        "horizon_months": 24,
        "risk_tolerance": "medium",
        "liquidity_need": {"amount": "10000", "currency": "CNY"},
        "liabilities": {"amount": "0", "currency": "CNY"},
        "existing_assets": [],
        "instrument_ids": codes,
    }
    return InvestmentProfile(**values, confirmed_fields=list(values))


@pytest.mark.parametrize("missing_kind", ["missing", "stale", "history"])
def test_asset_partial_keeps_category_template_and_actual_fund_metrics(store, missing_kind):
    if missing_kind == "stale":
        asyncio.run(store.upsert_batch([nav("511990", 31, "2")], "stale"))
    if missing_kind == "history":
        asyncio.run(store.upsert_batch([nav("511990", 0, "2")], "one-point"))
    profile = investor(["510300", "511990"])
    result = asyncio.run(
        AssetAllocationAgent(MarketMCPService(store)).run(
            SessionState(run_id="r", user_id="u", session_id="s", investment_profile=profile)
        )
    )
    assert result.status == "partial"
    assert result.structured_facts["profile"]["instrument_ids"] == ["510300", "511990"]
    assert set(result.calculations["historical_metrics"]) == {"510300"}
    assert [row["amount"] for row in result.calculations["allocation"]] == [
        "30000.00",
        "40000.00",
        "30000.00",
    ]
    assert result.structured_facts["metric_coverage"]["available_instrument_ids"] == ["510300"]
    assert any("511990" in warning for warning in result.warnings)
    assert "部分" in result.summary
    assert "511990" in result.next_action
    assert "更新" in result.next_action
    assert "510300" in result.next_action
    assert "选择" in result.next_action
    assert all("511990" not in evidence.quote for evidence in result.evidence)
