"""Fetch fully before commit; incomplete pagination never advances watermarks."""

from datetime import date, timedelta
from uuid import uuid4


class MarketIngestionPipeline:
    def __init__(self, source, repository, overlap_days=7):
        self.source, self.repository, self.overlap_days = source, repository, overlap_days

    async def run(self, instrument_ids: list[str], start: date, end: date):
        run_id = str(uuid4())
        if not instrument_ids or start > end:
            raise ValueError("Require instruments and ordered date interval")
        try:
            watermarks = [
                self.repository.get_watermark(self.source.source_id, i) for i in instrument_ids
            ]
            effective_start = start
            if all(w is not None for w in watermarks):
                effective_start = max(start, min(watermarks) - timedelta(days=self.overlap_days))
            records = await self.source.fetch(instrument_ids, effective_start, end)
            if any(
                r.instrument_id not in instrument_ids
                or r.source_id != self.source.source_id
                or not effective_start <= r.trade_date <= end
                or r.currency != "CNY"
                for r in records
            ):
                raise ValueError("Source record failed scope validation")
            if not records:
                raise ValueError("No source records; watermark retained")
            return await self.repository.upsert_batch(records, run_id)
        except Exception as exc:
            self.repository.log_failure(
                run_id, self.source.source_id, f"{type(exc).__name__}: {exc}"
            )
            raise
