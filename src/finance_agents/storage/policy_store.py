"""Versioned public policy text plus curated structured rules with original citations."""

from datetime import date, datetime, timezone
import json
from sqlalchemy import MetaData, Table, Column, String, Text, Date, DateTime, select
from finance_agents.contracts.common import EvidenceRef
from finance_agents.storage.market_store import conflict_upsert

metadata = MetaData()
policies = Table(
    "tax_policy_versions",
    metadata,
    Column("source_id", String(80), primary_key=True),
    Column("version", String(80), primary_key=True),
    Column("title", Text, nullable=False),
    Column("jurisdiction", String(80), nullable=False),
    Column("source_uri", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("rules_json", Text),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),
    Column("published_at", Date),
    Column("retrieved_at", DateTime, nullable=False),
)
observations = Table(
    "tax_policy_source_observations",
    metadata,
    Column("source_id", String(80), primary_key=True),
    Column("content_hash", String(80), primary_key=True),
    Column("source_uri", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("retrieved_at", DateTime, nullable=False),
)
VERIFIED_AT = datetime(2026, 10, 13, 11, 54, 5, tzinfo=timezone.utc)
LAW_URL = "https://fgk.chinatax.gov.cn/zcfgk/c100009/c5193028/content.html"
RATES_URL = "https://shanghai.chinatax.gov.cn/zcfw/zcfgk/grsds/202402/P020240202362406243753.pdf"
RATES_QUOTE = """| 级数 | 全年应纳税所得额 | 税率(%) | 速算扣除数 |
| --- | --- | --- | --- |
| 1 | 不超过36000元的 | 3 | 0 |
| 2 | 超过36000元至144000元的 | 10 | 2520 |
| 3 | 超过144000元至300000元的 | 20 | 16920 |
| 4 | 超过300000元至420000元的 | 25 | 31920 |
| 5 | 超过420000元至660000元的 | 30 | 52920 |
| 6 | 超过660000元至960000元的 | 35 | 85920 |
| 7 | 超过960000元的 | 45 | 181920 |"""
EFFECTIVE_URL = "https://www.moj.gov.cn/pub/sfbgw/zcjd/201812/t20181228_390204.html"
BRACKETS = [
    ["36000", "0.03", "0"],
    ["144000", "0.10", "2520"],
    ["300000", "0.20", "16920"],
    ["420000", "0.25", "31920"],
    ["660000", "0.30", "52920"],
    ["960000", "0.35", "85920"],
    [None, "0.45", "181920"],
]


class PolicyStore:
    def __init__(self, engine):
        self.engine = engine
        metadata.create_all(engine)

    def observe_source(self, *, source_id, content_hash, source_uri, retrieved_at, text):
        """Detect source changes; observations cannot become active calculation rules."""
        with self.engine.begin() as conn:
            previous = (
                conn.execute(
                    select(observations)
                    .where(observations.c.source_id == source_id)
                    .order_by(observations.c.retrieved_at.desc())
                )
                .mappings()
                .first()
            )
            status = (
                "baseline_recorded"
                if previous is None
                else "unchanged"
                if previous["content_hash"] == content_hash
                else "changed_requires_review"
            )
            values = dict(
                source_id=source_id,
                content_hash=content_hash,
                source_uri=source_uri,
                text=text,
                retrieved_at=retrieved_at,
            )
            conflict_upsert(
                conn,
                observations,
                values,
                ["source_id", "content_hash"],
                ["text", "source_uri", "retrieved_at"],
            )
        return {
            "source_id": source_id,
            "status": status,
            "content_hash": content_hash,
            "source_uri": source_uri,
            "retrieved_at": retrieved_at.isoformat(),
            "rule_activation": "manual_review_required",
        }

    def put(
        self,
        *,
        source_id,
        version,
        title,
        jurisdiction,
        source_uri,
        text,
        effective_from,
        retrieved_at,
        effective_to=None,
        published_at=None,
        rules=None,
    ):
        values = dict(
            source_id=source_id,
            version=version,
            title=title,
            jurisdiction=jurisdiction,
            source_uri=source_uri,
            text=text,
            effective_from=effective_from,
            effective_to=effective_to,
            published_at=published_at,
            retrieved_at=retrieved_at,
            rules_json=json.dumps(rules, ensure_ascii=False) if rules else None,
        )
        with self.engine.begin() as conn:
            old = (
                conn.execute(
                    select(policies).where(
                        policies.c.source_id == source_id, policies.c.version == version
                    )
                )
                .mappings()
                .first()
            )
            if old:
                # A source revision must use a new version; immutable references remain reproducible.
                if any(
                    str(old[k]) != str(values[k])
                    for k in ["text", "rules_json", "effective_from", "effective_to", "source_uri"]
                ):
                    raise ValueError("Policy version is immutable; use a new version")
                return
            conn.execute(policies.insert().values(**values))

    def seed_verified_rules(self):
        common = dict(
            jurisdiction="中国大陆", effective_from=date(2019, 1, 1), retrieved_at=VERIFIED_AT
        )
        self.put(
            source_id="cn_iit_law",
            version="2018-amendment",
            title="中华人民共和国个人所得税法（2018修正）",
            source_uri=LAW_URL,
            published_at=date(2018, 8, 31),
            text="第六条（一）居民个人的综合所得，以每一纳税年度的收入额减除费用六万元以及专项扣除、专项附加扣除和依法确定的其他扣除后的余额，为应纳税所得额。",
            rules={
                "basic_annual_deduction": "60000",
                "income_type": "resident_comprehensive_annual",
            },
            **common,
        )
        self.put(
            source_id="cn_iit_rates",
            version="2019-annual-7-brackets",
            title="个人所得税税率表（综合所得适用）",
            source_uri=RATES_URL,
            text=RATES_QUOTE,
            rules={"brackets": BRACKETS},
            **common,
        )
        self.put(
            source_id="cn_iit_effective",
            version="2018-amendment-effective",
            title="司法部财政部国家税务总局：实施条例修订答记者问",
            source_uri=EFFECTIVE_URL,
            published_at=date(2018, 12, 28),
            text="2018年8月31日，第十三届全国人大常委会第五次会议通过了《关于修改〈中华人民共和国个人所得税法〉的决定》，自2019年1月1日起施行。",
            **common,
        )

    def search(self, query, jurisdiction, on_date):
        if jurisdiction not in {"中国大陆", "CN", "CN-mainland", "中国"}:
            return []
        stmt = select(policies).where(
            policies.c.jurisdiction == "中国大陆",
            policies.c.effective_from <= on_date,
            (policies.c.effective_to.is_(None)) | (policies.c.effective_to >= on_date),
        )
        with self.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings()]
        latest = {}
        for row in sorted(
            rows, key=lambda r: (r["effective_from"], r["retrieved_at"], r["version"])
        ):
            latest[row["source_id"]] = row
        rows = list(latest.values())
        terms = [t for t in query.split() if t]
        rows.sort(key=lambda r: sum(t in r["text"] or t in r["title"] for t in terms), reverse=True)
        return rows

    @staticmethod
    def evidence(row):
        def dt(d):
            return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) if d else None

        return EvidenceRef(
            source_id=row["source_id"],
            source_title=row["title"],
            source_uri=row["source_uri"],
            chunk_id=row["version"],
            quote=row["text"],
            published_at=dt(row["published_at"]),
            effective_from=dt(row["effective_from"]),
            effective_to=dt(row["effective_to"]),
            retrieved_at=row["retrieved_at"].replace(tzinfo=timezone.utc),
        )
