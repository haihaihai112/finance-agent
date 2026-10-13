"""专业 Agent 的最小协议；工具依赖由构造器注入。"""

from typing import Protocol

from finance_agents.contracts.results import AgentResult
from finance_agents.workflow.state import SessionState


class BusinessAgent(Protocol):
    async def run(self, state: SessionState) -> AgentResult: ...
