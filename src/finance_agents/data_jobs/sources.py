"""Public daily fund NAV adapter; public access is not a redistribution licence."""

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import re
from typing import Protocol
import requests
from finance_agents.data_jobs.models import MarketRecord


class MarketSource(Protocol):
    source_id: str

    async def fetch(
        self, instrument_ids: list[str], start: date, end: date
    ) -> list[MarketRecord]: ...


class EastMoneyFundNavSource:
    source_id = "eastmoney_fund_nav"
    URL = "https://api.fund.eastmoney.com/f10/lsjz"

    def __init__(self, timeout=10, max_attempts=3, max_pages=40, page_size=20):
        if not 1 <= max_attempts <= 3 or not 1 <= max_pages <= 100 or not 1 <= page_size <= 100:
            raise ValueError("Invalid bounded request limits")
        self.timeout = timeout
        self.max_attempts, self.max_pages, self.page_size = max_attempts, max_pages, page_size

    async def fetch(self, instrument_ids, start, end):
        if (
            not instrument_ids
            or len(instrument_ids) > 20
            or any(not re.fullmatch(r"[0-9]{6}", i) for i in instrument_ids)
        ):
            raise ValueError("Require at most 20 six digit mainland fund codes")
        if start > end:
            raise ValueError("Invalid date range")
        return await asyncio.to_thread(self._fetch, instrument_ids, start, end)

    def _fetch(self, instrument_ids, start, end):
        records = []
        for instrument in instrument_ids:
            for page in range(1, self.max_pages + 1):
                payload = None
                for attempt in range(self.max_attempts):
                    try:
                        response = requests.get(
                            self.URL,
                            params={
                                "fundCode": instrument,
                                "pageIndex": page,
                                "pageSize": self.page_size,
                                "startDate": start.isoformat(),
                                "endDate": end.isoformat(),
                            },
                            headers={
                                "Referer": "https://fundf10.eastmoney.com/",
                                "User-Agent": "finance-agent-project/1.0",
                            },
                            timeout=self.timeout,
                            allow_redirects=False,
                        )
                        response.raise_for_status()
                        if response.status_code != 200:
                            raise ValueError("Redirects and non-200 responses refused")
                        payload = response.json()
                        if payload.get("ErrCode") not in (0, None) or not isinstance(
                            payload.get("Data"), dict
                        ):
                            raise ValueError("Unexpected public NAV response")
                        break
                    except (requests.RequestException, ValueError):
                        if attempt == self.max_attempts - 1:
                            raise
                rows = payload["Data"].get("LSJZList")
                if not isinstance(rows, list):
                    raise ValueError("Missing NAV list")
                for row in rows:
                    day = date.fromisoformat(row["FSRQ"])
                    if not start <= day <= end:
                        raise ValueError("NAV response outside requested interval")
                    value = Decimal(row["DWJZ"])
                    digest = sha256(f"{instrument}|{day}|fund_nav|{value}|CNY".encode()).hexdigest()
                    records.append(
                        MarketRecord(
                            source_id=self.source_id,
                            instrument_id=instrument,
                            trade_date=day,
                            record_type="fund_nav",
                            value=value,
                            currency="CNY",
                            source_uri=f"https://fundf10.eastmoney.com/jjjz_{instrument}.html",
                            fetched_at=datetime.now(timezone.utc),
                            content_hash=digest,
                        )
                    )
                if page * self.page_size >= int(payload.get("TotalCount", len(rows))):
                    break
            else:
                raise ValueError("Pagination cap reached; batch incomplete")
        return records
