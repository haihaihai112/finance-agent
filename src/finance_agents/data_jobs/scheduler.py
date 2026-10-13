"""One-shot CLI for OS scheduled tasks; imports never start jobs or network requests."""

import argparse
import asyncio
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
from finance_agents.core.settings import Settings
from finance_agents.storage.database import create_database_engine
from finance_agents.data_jobs.pipeline import MarketIngestionPipeline
from finance_agents.data_jobs.sources import EastMoneyFundNavSource
from finance_agents.storage.market_store import MarketStore
from finance_agents.storage.policy_store import PolicyStore
from finance_agents.data_jobs.policy_sources import OfficialPolicyMonitor


def job_engine(database_url=None):
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return create_database_engine(database_url or settings.database_dsn)


def run_market_update(
    *, store=None, source=None, instrument_ids=None, start=None, end=None, database_url=None
):
    if store is None:
        store = MarketStore(job_engine(database_url))
    source = source or EastMoneyFundNavSource()
    instruments = instrument_ids or [
        i.strip() for i in os.getenv("MARKET_INSTRUMENT_IDS", "").split(",") if i.strip()
    ]
    if not instruments:
        raise ValueError("Explicit instrument codes required; no automatic purchase selection")
    end = end or date.today()
    start = start or end - timedelta(days=365)
    return asyncio.run(MarketIngestionPipeline(source, store).run(instruments, start, end))


def run_policy_update(*, store=None, verified_documents=None, database_url=None, check_live=False):
    """Install reviewed policy versions. Does not claim automated legal interpretation."""
    if store is None:
        store = PolicyStore(job_engine(database_url))
    if verified_documents is None:
        store.seed_verified_rules()
        return {
            "status": "verified_snapshot_installed",
            "verification_date": "2026-10-13",
            "scope": "resident_annual_comprehensive",
            "live_change_detection": check_live,
            "source_checks": OfficialPolicyMonitor().check(store) if check_live else [],
        }
    for document in verified_documents:
        item = dict(document)
        for key in ["effective_from", "effective_to", "published_at"]:
            if item.get(key) and isinstance(item[key], str):
                item[key] = date.fromisoformat(item[key])
        if isinstance(item.get("retrieved_at"), str):
            item["retrieved_at"] = datetime.fromisoformat(item["retrieved_at"])
        store.put(**item)
    return {
        "status": "reviewed_versions_installed",
        "count": len(verified_documents),
        "live_change_detection": False,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Daily NAV ingestion / reviewed policy version installation"
    )
    parser.add_argument("job", choices=["market", "policy"])
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override API Settings database; otherwise use shared .env configuration",
    )
    parser.add_argument("--instruments", nargs="+")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--reviewed-policy-file", type=Path)
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Check fixed official document hashes; changed rules require review",
    )
    args = parser.parse_args()
    try:
        if args.job == "market":
            report = run_market_update(
                database_url=args.database_url,
                instrument_ids=args.instruments,
                start=args.start,
                end=args.end,
            )
            print(report.model_dump_json())
        else:
            documents = (
                json.loads(args.reviewed_policy_file.read_text(encoding="utf-8"))
                if args.reviewed_policy_file
                else None
            )
            print(
                json.dumps(
                    run_policy_update(
                        database_url=args.database_url,
                        verified_documents=documents,
                        check_live=args.check_live,
                    ),
                    ensure_ascii=False,
                )
            )
    except Exception as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
