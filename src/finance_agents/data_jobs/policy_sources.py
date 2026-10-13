"""Hash monitoring for three fixed official documents; does not infer legal rule changes."""

from datetime import datetime, timezone
from hashlib import sha256
import requests
from bs4 import BeautifulSoup
from finance_agents.storage.policy_store import LAW_URL, RATES_URL, EFFECTIVE_URL


class OfficialPolicyMonitor:
    SOURCES = (
        ("cn_iit_law", LAW_URL),
        ("cn_iit_rates", RATES_URL),
        ("cn_iit_effective", EFFECTIVE_URL),
    )

    def __init__(self, timeout=10):
        if not 1 <= timeout <= 30:
            raise ValueError("Timeout must be between 1 and 30 seconds")
        self.timeout = timeout

    def check(self, store):
        result = []
        for source_id, url in self.SOURCES:
            try:
                response = requests.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=False,
                    headers={"User-Agent": "finance-agent-project/1.0"},
                )
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Redirect or non-200 official response refused")
                raw = response.content
                if not raw or len(raw) > 5_000_000:
                    raise ValueError("Official document has invalid bounded size")
                text = ""
                content = raw
                if not url.endswith(".pdf"):
                    response.encoding = response.apparent_encoding
                    soup = BeautifulSoup(response.text, "html.parser")
                    for node in soup(["script", "style"]):
                        node.decompose()
                    text = " ".join(soup.get_text(" ", strip=True).split())
                    content = text.encode("utf-8")
                digest = sha256(content).hexdigest()
                result.append(
                    store.observe_source(
                        source_id=source_id,
                        content_hash=digest,
                        source_uri=url,
                        retrieved_at=datetime.now(timezone.utc),
                        text=text or "PDF binary SHA-256 observation; review original URL",
                    )
                )
            except (requests.RequestException, ValueError) as exc:
                result.append(
                    {
                        "source_id": source_id,
                        "status": "unavailable",
                        "source_uri": url,
                        "error": f"{type(exc).__name__}: {exc}",
                        "rule_activation": "unchanged",
                    }
                )
        return result
