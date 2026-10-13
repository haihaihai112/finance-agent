"""HTTP 请求只接受用户内容，不接受可信用户身份。"""

from typing import Any

from pydantic import Field

from finance_agents.contracts.common import Contract
from finance_agents.contracts.results import Intent


class QueryRequest(Contract):
    session_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    intent_hint: Intent | None = None


class ResumeRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    state_version: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=10000)
    confirmed_fields: dict[str, Any] = Field(default_factory=dict)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    consent_ref: str | None = None


class ConsentRequest(Contract):
    granted: bool


class CopyRunRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
