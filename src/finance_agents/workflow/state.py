"""原图中的信息追问、文件上传、表单确认与授权等待共享状态。"""

from enum import StrEnum

from pydantic import Field

from finance_agents.contracts.common import Contract
from finance_agents.contracts.documents import ExtractedField
from finance_agents.contracts.profiles import ClaimForm, CreditForm, InvestmentProfile, TaxProfile
from finance_agents.contracts.results import AgentResult, FinalResponse, Intent


class RunStatus(StrEnum):
    RECEIVED = "received"
    ROUTED = "routed"
    VALIDATING = "validating"
    WAITING_USER = "waiting_user"
    RUNNING = "running"
    FORMATTING = "formatting"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WaitReason(StrEnum):
    CLARIFY_INTENT = "clarify_intent"
    MISSING_INFORMATION = "missing_information"
    UPLOAD_DOCUMENTS = "upload_documents"
    CONFIRM_FORM = "confirm_form"
    CONSENT = "consent"


class SessionState(Contract):
    run_id: str
    user_id: str  # 可信服务端身份
    session_id: str
    intent: Intent | None = None
    active_agent: str | None = None
    status: RunStatus = RunStatus.RECEIVED
    wait_reason: WaitReason | None = None
    pending_fields: list[str] = Field(default_factory=list)
    document_refs: list[str] = Field(default_factory=list)
    extracted_fields: list[ExtractedField] = Field(default_factory=list)
    investment_profile: InvestmentProfile | None = None
    claim_form: ClaimForm | None = None
    credit_form: CreditForm | None = None
    tax_profile: TaxProfile | None = None
    branch_result: AgentResult | None = None
    consent_ref: str | None = None
    state_version: int = Field(default=1, ge=1)
    message: str = ""
    initial_message: str = ""
    final_response: FinalResponse | None = None
    last_question: str | None = None
