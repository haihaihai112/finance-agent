"""行情／净值入库契约：交易时间与抓取时间独立。"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from finance_agents.contracts.common import Contract


class MarketRecord(Contract):
    source_id: str
    instrument_id: str
    trade_date: date
    record_type: str  # price / fund_nav
    value: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str
    source_uri: str
    observed_at: datetime | None = None
    fetched_at: datetime
    content_hash: str


class IngestionReport(Contract):
    source_id: str
    inserted: int = Field(ge=0)
    updated: int = Field(ge=0)
    rejected: int = Field(ge=0)
    watermark: date | None = None
