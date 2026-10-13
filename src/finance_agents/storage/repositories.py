"""数据库访问协议：实现时采用参数化查询，禁止 Agent 执行任意 SQL。"""

from typing import Any, Protocol

from finance_agents.data_jobs.models import IngestionReport, MarketRecord
from finance_agents.workflow.state import SessionState


class SessionRepository(Protocol):
    async def save(self, state: SessionState, expected_version: int) -> None: ...
    async def get(self, run_id: str, user_id: str) -> SessionState | None: ...


class PolicyRepository(Protocol):
    async def find_history(
        self,
        user_id: str,
        policy_id: str | None,
        insurance_type: str | None,
    ) -> list[dict[str, Any]]:
        """必须按 user_id 隔离，再匹配保单版本、险种和有效期。"""
        ...


class MarketRepository(Protocol):
    async def upsert_batch(
        self,
        records: list[MarketRecord],
        run_id: str,
    ) -> IngestionReport:
        """后续在事务内幂等写入、记录修订及推进成功水位。"""
        ...
