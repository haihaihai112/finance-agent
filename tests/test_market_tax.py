import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from finance_agents.agents.asset_allocation import AssetAllocationAgent
from finance_agents.contracts.common import AuthContext
from finance_agents.data_jobs.models import MarketRecord
from finance_agents.workflow.state import SessionState


def run(coro):
    return asyncio.run(coro)


def record(value="1.1234", day=None):
    return MarketRecord(
        source_id="eastmoney_fund_nav",
        instrument_id="000001",
        trade_date=day or date.today(),
        record_type="fund_nav",
        value=Decimal(value),
        currency="CNY",
        source_uri="https://fundf10.eastmoney.com/jjjz_000001.html",
        fetched_at=datetime.now(timezone.utc),
        content_hash=value,
    )


def test_incomplete_investor_is_asked_before_mcp():
    class NoCall:
        async def call(self, *args):
            pytest.fail("incomplete profile must not query market")

    result = run(
        AssetAllocationAgent(NoCall()).run(SessionState(run_id="r", user_id="u", session_id="s"))
    )
    assert result.status == "needs_input"
    assert "investment_profile.investable_amount" in result.missing_fields


def test_market_upsert_duplicate_revision_and_watermark():
    from finance_agents.storage.market_store import MarketStore

    store = MarketStore(create_engine("sqlite://"))
    first = run(store.upsert_batch([record()], "one"))
    duplicate = run(store.upsert_batch([record()], "two"))
    revised = run(store.upsert_batch([record("1.2222")], "three"))
    assert (first.inserted, duplicate.inserted, duplicate.updated, revised.updated) == (1, 0, 0, 1)
    assert store.get_watermark("eastmoney_fund_nav", "000001") == date.today()
    assert len(store.revisions()) == 1
    assert store.query(["000001"])[0].value == Decimal("1.2222")


def test_pipeline_failure_does_not_move_watermark():
    from finance_agents.storage.market_store import MarketStore
    from finance_agents.data_jobs.pipeline import MarketIngestionPipeline

    class Broken:
        source_id = "broken"

        async def fetch(self, *args):
            raise TimeoutError("source unavailable")

    store = MarketStore(create_engine("sqlite://"))
    with pytest.raises(TimeoutError):
        run(
            MarketIngestionPipeline(Broken(), store).run(["000001"], date(2025, 1, 1), date.today())
        )
    assert store.get_watermark("broken", "000001") is None
    assert store.ingestion_logs()[0]["status"] == "failed"


@pytest.mark.parametrize(
    "taxable,expected",
    [
        (0, "0.00"),
        (36000, "1080.00"),
        (144000, "11880.00"),
        (300000, "43080.00"),
        (420000, "73080.00"),
        (660000, "145080.00"),
        (960000, "250080.00"),
        (1000000, "268080.00"),
        (36000.01, "1080.00"),
    ],
)
def test_annual_tax_bracket_boundaries(taxable, expected):
    from finance_agents.mcp_services.tax import calculate_annual_tax

    result = calculate_annual_tax(Decimal(str(taxable)) + Decimal("60000"), Decimal("0"))
    assert result["annual_tax"] == expected


def test_policy_effective_filter_and_no_unsupported_year():
    from finance_agents.storage.policy_store import PolicyStore
    from finance_agents.mcp_services.tax import TaxMCPService

    store = PolicyStore(create_engine("sqlite://"))
    store.seed_verified_rules()
    assert store.search("综合所得", "中国大陆", date(2018, 1, 1)) == []
    assert store.search("综合所得", "中国大陆", date(2025, 1, 1))
    result = run(
        TaxMCPService(store).call(
            "tax.calculate",
            {
                "jurisdiction": "中国大陆",
                "tax_year": 2018,
                "annual_income": "100000",
                "annual_deductions": "0",
                "currency": "CNY",
                "resident_status": "resident",
                "income_type": "resident_comprehensive_annual",
            },
            AuthContext(user_id="u", session_id="s"),
        )
    )
    assert result.status == "unavailable"
    assert result.data is None


def test_no_market_data_cannot_return_metrics():
    from finance_agents.storage.market_store import MarketStore
    from finance_agents.mcp_services.market import MarketMCPService

    service = MarketMCPService(MarketStore(create_engine("sqlite://")))
    result = run(
        service.call(
            "market.compute_metrics",
            {"instrument_ids": ["000001"]},
            AuthContext(user_id="u", session_id="s"),
        )
    )
    assert result.status == "unavailable"
    assert result.data is None


def test_allocation_is_conserved_and_currency_checked():
    from finance_agents.agents.asset_allocation import allocate_amount

    for risk in ["low", "medium", "high"]:
        result = allocate_amount(Decimal("100000.01"), risk, Decimal("12000"), 12)
        assert sum(Decimal(x["amount"]) for x in result) == Decimal("100000.01")
        assert sum(Decimal(x["weight"]) for x in result) == Decimal("1")


def test_source_rejects_url_instrument():
    from finance_agents.data_jobs.sources import EastMoneyFundNavSource

    with pytest.raises(ValueError):
        run(EastMoneyFundNavSource().fetch(["http://127.0.0.1"], date(2025, 1, 1), date.today()))


def test_batch_database_failure_rolls_back_records_and_watermark(monkeypatch):
    import finance_agents.storage.market_store as module

    store = module.MarketStore(create_engine("sqlite://"))
    original = module.conflict_upsert

    def broken(conn, table, values, keys, fields):
        if values.get("value") == Decimal("2"):
            raise RuntimeError("database rejected second record")
        return original(conn, table, values, keys, fields)

    monkeypatch.setattr(module, "conflict_upsert", broken)
    with pytest.raises(RuntimeError):
        run(store.upsert_batch([record(), record("2", date(2025, 1, 1))], "failure"))
    assert store.query(["000001"]) == []
    assert store.get_watermark("eastmoney_fund_nav", "000001") is None


def test_snapshot_is_explicit_and_retains_real_source():
    from finance_agents.storage.market_store import MarketStore

    store = MarketStore(create_engine("sqlite://"))
    assert store.query(["510300"]) == []
    report = run(store.load_verified_snapshot())
    assert report.inserted > 0
    records = store.query(["510300"])
    assert records[-1].trade_date == date(2026, 10, 13)
    assert records[-1].value == Decimal("4.3479")
    assert all(r.source_id == "eastmoney_fund_nav" for r in records)


def test_policy_version_uses_latest_effective_document():
    from finance_agents.storage.policy_store import PolicyStore

    store = PolicyStore(create_engine("sqlite://"))
    values = dict(
        source_id="source",
        title="综合所得",
        jurisdiction="中国大陆",
        source_uri="https://www.gov.cn/",
        retrieved_at=datetime.now(timezone.utc),
    )
    store.put(version="old", text="old", effective_from=date(2020, 1, 1), **values)
    store.put(version="new", text="new", effective_from=date(2025, 1, 1), **values)
    assert store.search("综合所得", "中国大陆", date(2024, 1, 1))[0]["version"] == "old"
    assert [r["version"] for r in store.search("综合所得", "中国大陆", date(2025, 1, 1))] == ["new"]


def test_allocation_rejects_fractional_cent():
    from finance_agents.agents.asset_allocation import allocate_amount

    with pytest.raises(ValueError):
        allocate_amount(Decimal("0.005"), "high", Decimal("0"), 12)


def test_tax_rejects_nonfinite_and_negative():
    from finance_agents.mcp_services.tax import calculate_annual_tax

    for income, deduction in [("NaN", "0"), ("10000", "-1"), ("Infinity", "0")]:
        with pytest.raises(ValueError):
            calculate_annual_tax(income, deduction)


def test_full_profile_without_market_does_not_invent_allocation():
    from finance_agents.storage.market_store import MarketStore
    from finance_agents.mcp_services.market import MarketMCPService
    from finance_agents.contracts.profiles import InvestmentProfile

    profile = InvestmentProfile(
        jurisdiction="中国大陆",
        investable_amount={"amount": "100000", "currency": "CNY"},
        horizon_months=24,
        risk_tolerance="medium",
        liquidity_need={"amount": "20000", "currency": "CNY"},
        liabilities={"amount": "0", "currency": "CNY"},
        existing_assets=[],
        instrument_ids=["510300"],
        confirmed_fields=[
            "jurisdiction",
            "investable_amount",
            "horizon_months",
            "risk_tolerance",
            "liquidity_need",
            "liabilities",
            "existing_assets",
            "instrument_ids",
        ],
    )
    result = run(
        AssetAllocationAgent(MarketMCPService(MarketStore(create_engine("sqlite://")))).run(
            SessionState(run_id="r", user_id="u", session_id="s", investment_profile=profile)
        )
    )
    assert result.status == "unavailable"
    assert result.calculations == {}


def test_source_paginates_and_bounds_incomplete_history(monkeypatch):
    from finance_agents.data_jobs.sources import EastMoneyFundNavSource
    import finance_agents.data_jobs.sources as module

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "ErrCode": 0,
                "TotalCount": 2,
                "Data": {"LSJZList": [{"FSRQ": "2026-10-12", "DWJZ": "1.2"}]},
            }

    pages = []

    def get(url, **kwargs):
        assert url == "https://api.fund.eastmoney.com/f10/lsjz"
        assert kwargs["allow_redirects"] is False
        pages.append(kwargs["params"]["pageIndex"])
        return Response()

    monkeypatch.setattr(module.requests, "get", get)
    rows = run(
        EastMoneyFundNavSource(page_size=1, max_pages=2).fetch(
            ["000001"], date(2026, 10, 12), date(2026, 10, 13)
        )
    )
    assert pages == [1, 2] and len(rows) == 2
    with pytest.raises(ValueError, match="cap"):
        run(
            EastMoneyFundNavSource(page_size=1, max_pages=1).fetch(
                ["000001"], date(2026, 10, 12), date(2026, 10, 13)
            )
        )


def test_out_of_scope_policy_query_not_answered_with_comprehensive_tax():
    from finance_agents.storage.policy_store import PolicyStore
    from finance_agents.mcp_services.tax import TaxMCPService

    store = PolicyStore(create_engine("sqlite://"))
    store.seed_verified_rules()
    result = run(
        TaxMCPService(store).call(
            "tax.search_rules",
            {"jurisdiction": "中国大陆", "tax_year": 2026, "query": "企业增值税政策"},
            AuthContext(user_id="u", session_id="s"),
        )
    )
    assert result.status == "unavailable"


def test_tools_classify_database_outage():
    from sqlalchemy.exc import OperationalError
    from finance_agents.mcp_services.market import MarketMCPService

    class Broken:
        def query(self, *args):
            raise OperationalError("select", {}, Exception("database offline"))

    result = run(
        MarketMCPService(Broken()).call(
            "market.query_prices",
            {"instrument_ids": ["510300"]},
            AuthContext(user_id="u", session_id="s"),
        )
    )
    assert result.status == "unavailable"
    assert result.error.retryable is True


def test_retriever_byte_budget_keeps_valid_reference():
    from finance_agents.storage.policy_store import PolicyStore
    from finance_agents.tools.retrieval import EvidenceRetriever

    store = PolicyStore(create_engine("sqlite://"))
    store.seed_verified_rules()
    refs = run(
        EvidenceRetriever(store).search("综合所得", AuthContext(user_id="u", session_id="s"), 700)
    )
    assert refs
    assert sum(len(r.model_dump_json().encode("utf-8")) for r in refs) <= 700


def test_scheduler_once_and_reviewed_policy_version_import():
    from finance_agents.data_jobs.scheduler import run_market_update, run_policy_update
    from finance_agents.storage.market_store import MarketStore
    from finance_agents.storage.policy_store import PolicyStore

    engine = create_engine("sqlite://")
    store = MarketStore(engine)

    class Source:
        source_id = "eastmoney_fund_nav"

        async def fetch(self, *args):
            return [record()]

    report = run_market_update(store=store, source=Source(), instrument_ids=["000001"])
    assert report.inserted == 1
    policy = PolicyStore(engine)
    run_policy_update(store=policy)
    assert policy.search("综合所得", "中国大陆", date(2026, 1, 1))


def test_asset_returns_historical_metrics_from_real_nav_snapshot():
    from finance_agents.storage.market_store import MarketStore
    from finance_agents.mcp_services.market import MarketMCPService
    from finance_agents.contracts.profiles import InvestmentProfile

    store = MarketStore(create_engine("sqlite://"))
    run(store.load_verified_snapshot())
    fields = [
        "jurisdiction",
        "investable_amount",
        "horizon_months",
        "risk_tolerance",
        "liquidity_need",
        "liabilities",
        "existing_assets",
        "instrument_ids",
    ]
    profile = InvestmentProfile(
        jurisdiction="中国大陆",
        investable_amount={"amount": "100000", "currency": "CNY"},
        horizon_months=24,
        risk_tolerance="medium",
        liquidity_need={"amount": "20000", "currency": "CNY"},
        liabilities={"amount": "0", "currency": "CNY"},
        existing_assets=[],
        instrument_ids=["510300"],
        confirmed_fields=fields,
    )
    result = run(
        AssetAllocationAgent(MarketMCPService(store)).run(
            SessionState(run_id="r", user_id="u", session_id="s", investment_profile=profile)
        )
    )
    assert result.status == "success"
    assert Decimal(result.calculations["historical_metrics"]["510300"]["nav_change"]) < 0
    assert result.calculations["historical_metrics"]["510300"]["observations"] == 16
    assert sum(Decimal(row["amount"]) for row in result.calculations["allocation"]) == Decimal(
        "100000"
    )


def test_official_source_observations_detect_changes_without_changing_rules():
    from finance_agents.storage.policy_store import PolicyStore

    store = PolicyStore(create_engine("sqlite://"))
    store.seed_verified_rules()
    before = store.search("综合所得", "中国大陆", date(2026, 1, 1))
    kwargs = dict(
        source_id="cn_iit_law",
        source_uri=before[0]["source_uri"],
        retrieved_at=datetime.now(timezone.utc),
        text="observed content",
    )
    assert store.observe_source(content_hash="first", **kwargs)["status"] == "baseline_recorded"
    assert store.observe_source(content_hash="first", **kwargs)["status"] == "unchanged"
    assert (
        store.observe_source(content_hash="second", **kwargs)["status"] == "changed_requires_review"
    )
    assert store.search("综合所得", "中国大陆", date(2026, 1, 1)) == before


def test_tax_calculation_task_survives_form_only_resume():
    from finance_agents.agents.tax_policy import TaxPolicyAgent
    from finance_agents.contracts.profiles import TaxProfile

    class NoCall:
        async def call(self, *args):
            pytest.fail("Annual calculation remains incomplete")

    state = SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        message="已逐项核对并确认提交的表单字段，请按当前流程继续。",
        tax_profile=TaxProfile(
            jurisdiction="中国大陆", tax_year=2026, confirmed_fields=["jurisdiction", "tax_year"]
        ),
    )
    state = state.model_copy(update={"initial_message": "请试算我的年度个税税额"})
    result = run(TaxPolicyAgent(NoCall()).run(state))
    assert result.status == "needs_input"
    assert "tax_profile.annual_income" in result.missing_fields


def test_out_of_scope_tax_question_survives_generic_resume():
    from finance_agents.agents.tax_policy import TaxPolicyAgent
    from finance_agents.storage.policy_store import PolicyStore
    from finance_agents.mcp_services.tax import TaxMCPService
    from finance_agents.contracts.profiles import TaxProfile

    store = PolicyStore(create_engine("sqlite://"))
    store.seed_verified_rules()
    state = SessionState(
        run_id="r",
        user_id="u",
        session_id="s",
        message="已确认",
        tax_profile=TaxProfile(
            jurisdiction="中国大陆", tax_year=2026, confirmed_fields=["jurisdiction", "tax_year"]
        ),
    )
    state = state.model_copy(update={"initial_message": "查询企业增值税政策"})
    result = run(TaxPolicyAgent(TaxMCPService(store)).run(state))
    assert result.status == "unavailable"


def test_scheduler_uses_api_default_database(monkeypatch, tmp_path):
    from finance_agents.data_jobs.scheduler import run_policy_update

    monkeypatch.setenv("FINANCE_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    run_policy_update()
    assert (tmp_path / "runtime" / "finance.db").exists()
