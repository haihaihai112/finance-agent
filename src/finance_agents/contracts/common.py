"""公共字段。所有可变集合使用独立默认值。"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DataMode(StrEnum):
    REAL = "real"
    REDACTED = "redacted"
    MOCK = "mock"


class ResultStatus(StrEnum):
    SUCCESS = "success"
    NEEDS_INPUT = "needs_input"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class Money(Contract):
    amount: Decimal = Field(ge=0, allow_inf_nan=False, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)


class EvidenceRef(Contract):
    source_id: str
    source_title: str
    source_uri: str | None = None
    document_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    chunk_id: str | None = None
    quote: str | None = None
    published_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    retrieved_at: datetime


class AuthContext(Contract):
    """由可信会话组件生成，不能信任客户提交的身份字段。"""

    user_id: str
    session_id: str
    consent_ref: str | None = None
