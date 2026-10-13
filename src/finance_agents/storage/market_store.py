"""Transactional daily NAV storage with SQLite/MySQL native upserts."""

from datetime import datetime, timezone
import json
from pathlib import Path
from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Date,
    DateTime,
    Numeric,
    Text,
    Integer,
    UniqueConstraint,
    select,
    insert,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.mysql import insert as mysql_insert
from finance_agents.data_jobs.models import MarketRecord, IngestionReport

metadata = MetaData()
records_table = Table(
    "market_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", String(80), nullable=False),
    Column("instrument_id", String(40), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("record_type", String(30), nullable=False),
    Column("value", Numeric(24, 10), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("source_uri", Text, nullable=False),
    Column("observed_at", DateTime),
    Column("fetched_at", DateTime, nullable=False),
    Column("content_hash", String(128), nullable=False),
    UniqueConstraint(
        "source_id", "instrument_id", "trade_date", "record_type", name="uq_market_key"
    ),
)
watermarks_table = Table(
    "market_watermarks",
    metadata,
    Column("source_id", String(80), primary_key=True),
    Column("instrument_id", String(40), primary_key=True),
    Column("watermark", Date, nullable=False),
)
revisions_table = Table(
    "market_revisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", String(80)),
    Column("old_record", Text),
    Column("new_record", Text),
    Column("revised_at", DateTime),
)
logs_table = Table(
    "market_ingestion_logs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", String(80)),
    Column("source_id", String(80)),
    Column("status", String(20)),
    Column("detail", Text),
    Column("logged_at", DateTime),
)


def conflict_upsert(connection, table, values, keys, update_fields):
    dialect = connection.dialect.name
    if dialect == "sqlite":
        stmt = sqlite_insert(table).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=keys, set_={k: stmt.excluded[k] for k in update_fields}
        )
    elif dialect in {"mysql", "mariadb"}:
        stmt = mysql_insert(table).values(**values)
        stmt = stmt.on_duplicate_key_update(**{k: stmt.inserted[k] for k in update_fields})
    else:
        raise ValueError("Only SQLite and MySQL/MariaDB are supported")
    connection.execute(stmt)


class MarketStore:
    def __init__(self, engine):
        self.engine = engine
        metadata.create_all(engine)

    async def load_verified_snapshot(self):
        """Explicit opt-in dated real snapshot; freshness is still checked at the MCP boundary."""
        path = Path(__file__).parents[1] / "data_jobs" / "verified_market_snapshot.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return await self.upsert_batch(
            [MarketRecord.model_validate(r) for r in payload["records"]],
            "verified-snapshot-2026-10-13",
        )

    async def upsert_batch(self, records: list[MarketRecord], run_id: str) -> IngestionReport:
        sources = {r.source_id for r in records}
        if len(sources) > 1:
            raise ValueError("A batch must belong to one source")
        source = next(iter(sources), "unknown")
        inserted = updated = 0
        with self.engine.begin() as conn:
            for r in records:
                key = {
                    k: getattr(r, k)
                    for k in ["source_id", "instrument_id", "trade_date", "record_type"]
                }
                clause = [records_table.c[k] == v for k, v in key.items()]
                old = (
                    conn.execute(select(records_table).where(*clause).with_for_update())
                    .mappings()
                    .first()
                )
                values = r.model_dump()
                if old is None or old["content_hash"] != r.content_hash:
                    if old is None:
                        inserted += 1
                    else:
                        updated += 1
                        conn.execute(
                            insert(revisions_table).values(
                                run_id=run_id,
                                old_record=json.dumps(dict(old), default=str),
                                new_record=r.model_dump_json(),
                                revised_at=datetime.now(timezone.utc),
                            )
                        )
                    conflict_upsert(
                        conn, records_table, values, list(key), [k for k in values if k not in key]
                    )
                wkey = {"source_id": r.source_id, "instrument_id": r.instrument_id}
                existing = conn.execute(
                    select(watermarks_table.c.watermark).where(
                        watermarks_table.c.source_id == r.source_id,
                        watermarks_table.c.instrument_id == r.instrument_id,
                    )
                ).scalar()
                conflict_upsert(
                    conn,
                    watermarks_table,
                    {**wkey, "watermark": max(existing or r.trade_date, r.trade_date)},
                    list(wkey),
                    ["watermark"],
                )
            conn.execute(
                insert(logs_table).values(
                    run_id=run_id,
                    source_id=source,
                    status="success",
                    detail=json.dumps(
                        {"inserted": inserted, "updated": updated, "received": len(records)}
                    ),
                    logged_at=datetime.now(timezone.utc),
                )
            )
        return IngestionReport(
            source_id=source,
            inserted=inserted,
            updated=updated,
            rejected=0,
            watermark=max((r.trade_date for r in records), default=None),
        )

    def get_watermark(self, source_id, instrument_id):
        with self.engine.connect() as conn:
            return conn.execute(
                select(watermarks_table.c.watermark).where(
                    watermarks_table.c.source_id == source_id,
                    watermarks_table.c.instrument_id == instrument_id,
                )
            ).scalar()

    def log_failure(self, run_id, source_id, detail):
        with self.engine.begin() as conn:
            conn.execute(
                insert(logs_table).values(
                    run_id=run_id,
                    source_id=source_id,
                    status="failed",
                    detail=detail[:2000],
                    logged_at=datetime.now(timezone.utc),
                )
            )

    def query(self, instrument_ids, start=None, end=None, source_id=None):
        stmt = select(records_table).where(records_table.c.instrument_id.in_(instrument_ids))
        if start:
            stmt = stmt.where(records_table.c.trade_date >= start)
        if end:
            stmt = stmt.where(records_table.c.trade_date <= end)
        if source_id:
            stmt = stmt.where(records_table.c.source_id == source_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt.order_by(records_table.c.trade_date)).mappings().all()
        return [MarketRecord(**{k: v for k, v in r.items() if k != "id"}) for r in rows]

    def revisions(self):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(revisions_table)).mappings()]

    def ingestion_logs(self):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(logs_table)).mappings()]
