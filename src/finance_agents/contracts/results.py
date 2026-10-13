"""分发、专业处理、工具和最终输出的数据契约。"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from finance_agents.contracts.common import Contract, DataMode, EvidenceRef, ResultStatus


class Intent(StrEnum):
    """四类金融业务 + 其他；追问属于状态，不是第六个意图。"""

    INVESTMENT = "investment"
    CLAIM = "claim"
    LOAN = "loan"
    POLICY_TAX = "policy_tax"
    OTHER = "other"


class IntentDecision(Contract):
    intent: Intent | None = None
    confidence: float = Field(ge=0, le=1)
    requires_clarification: bool = False
    clarification: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    source: str = "rule"
    warnings: list[str] = Field(default_factory=list)


class ToolRequest(Contract):
    run_id: str
    tool_name: str
    arguments: dict[str, Any]
    idempotency_key: str
    deadline: datetime


class ToolError(Contract):
    code: str
    message: str
    retryable: bool = False


class ToolResult(Contract):
    status: ResultStatus
    data_mode: DataMode
    data: dict[str, Any] | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    as_of: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ToolError | None = None


class AgentResult(Contract):
    agent_type: str
    status: ResultStatus
    data_mode: DataMode
    summary: str
    structured_facts: dict[str, Any] = Field(default_factory=dict)
    calculations: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_action: str | None = None


class FinalResponse(Contract):
    conclusion: str
    facts: dict[str, Any] = Field(default_factory=dict)
    citations: list[EvidenceRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_action: str | None = None
    data_mode: DataMode


class WorkflowResponse(Contract):
    run_id: str
    intent: Intent | None = None
    status: str
    state_version: int
    wait_reason: str | None = None
    pending_fields: list[str] = Field(default_factory=list)
    question: str | None = None
    form: dict[str, Any] = Field(default_factory=dict)
    conflicts: dict[str, Any] = Field(default_factory=dict)
    result: FinalResponse | None = None
