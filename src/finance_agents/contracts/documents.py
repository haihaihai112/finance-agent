"""OCR 字段附带来源，用于表单预填、冲突追问与人工确认。"""

from typing import Any

from pydantic import Field

from finance_agents.contracts.common import Contract


class ExtractedField(Contract):
    name: str
    value: Any
    document_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    quote: str | None = None
    confidence: float = Field(ge=0, le=1)
    source: str  # ocr / text_parser / user_message
    confirmed: bool = False
    bounding_box: tuple[float, float, float, float] | None = None


class DocumentExtraction(Contract):
    document_id: str
    document_type: str | None = None
    text: str
    fields: list[ExtractedField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
