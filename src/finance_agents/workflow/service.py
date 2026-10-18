"""可信身份、持久化业务检查点、确认与授权门禁。"""

import asyncio
from uuid import uuid4

from finance_agents.agents.asset_allocation import AssetAllocationAgent
from finance_agents.agents.claim_intake import ClaimIntakeAgent
from finance_agents.agents.credit_assessment import CreditAssessmentAgent
from finance_agents.agents.intent_router import IntentRouterAgent
from finance_agents.agents.output_optimizer import OutputOptimizerAgent
from finance_agents.agents.tax_policy import TaxPolicyAgent
from finance_agents.contracts.profiles import ClaimForm, CreditForm, InvestmentProfile, TaxProfile
from finance_agents.contracts.form_hints import form_hints
from finance_agents.contracts.results import Intent, WorkflowResponse
from finance_agents.llm.deepseek import DeepSeekClient
from finance_agents.mcp_services.claims import ClaimsMCPService
from finance_agents.mcp_services.credit import CreditMCPService
from finance_agents.mcp_services.market import MarketMCPService
from finance_agents.mcp_services.tax import TaxMCPService
from finance_agents.storage.claims_store import ClaimsStore
from finance_agents.storage.database import create_database_engine
from finance_agents.storage.document_store import DocumentStore
from finance_agents.storage.market_store import MarketStore
from finance_agents.storage.policy_store import PolicyStore
from finance_agents.storage.runtime_store import RuntimeStore
from finance_agents.tools.document_service import DocumentService
from finance_agents.workflow.graph import build_graph
from finance_agents.workflow.locks import KeyedLocks
from finance_agents.workflow.state import RunStatus, SessionState, WaitReason

FORMS = {
    Intent.INVESTMENT: ("investment_profile", InvestmentProfile),
    Intent.CLAIM: ("claim_form", ClaimForm),
    Intent.LOAN: ("credit_form", CreditForm),
    Intent.POLICY_TAX: ("tax_profile", TaxProfile),
}
PROTECTED_FIELDS = {"confirmed_fields", "consent_ref", "document_ids", "form_version"}


class WorkflowService:
    def __init__(self, settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_database_engine(settings.database_dsn)
        self.store = RuntimeStore(self.engine)
        self.documents = DocumentStore(
            self.engine, settings.data_dir / "documents", settings.max_upload_bytes
        )
        self.document_service = DocumentService(
            self.documents, max_concurrency=settings.ocr_max_concurrency
        )
        self.claims = ClaimsStore(self.engine)
        self.market = MarketStore(self.engine)
        self.policy = PolicyStore(self.engine)
        self.policy.seed_verified_rules()
        self.services = {
            "market": MarketMCPService(self.market),
            "tax": TaxMCPService(self.policy),
            "claims": ClaimsMCPService(self.claims),
            "credit": CreditMCPService(),
        }
        llm = (
            DeepSeekClient(
                settings.deepseek_api_key,
                settings.deepseek_model,
                settings.deepseek_base_url,
                settings.llm_timeout,
            )
            if settings.llm_enabled
            else None
        )
        self.router = IntentRouterAgent(llm)
        self.optimizer = OutputOptimizerAgent(llm)
        self.locks = KeyedLocks()

    def lock(self, key):
        return self.locks.hold(key)

    def response(self, state):
        profile = FORMS.get(state.intent)
        form = getattr(state, profile[0]) if profile else None
        if profile and form is None:
            form = profile[1]()
        facts = state.branch_result.structured_facts if state.branch_result else {}
        form_data = form.model_dump(mode="json") if form else {}
        if form_data.get("consent_ref") and not self.store.check_consent(
            form_data["consent_ref"], state.user_id, state.run_id
        ):
            form_data["consent_ref"] = None
        return WorkflowResponse(
            run_id=state.run_id,
            parent_run_id=state.parent_run_id,
            document_ids=state.document_refs,
            intent=state.intent,
            status=state.status,
            state_version=state.state_version,
            wait_reason=state.wait_reason,
            pending_fields=state.pending_fields,
            question=state.last_question,
            form=form_data,
            field_hints=form_hints(profile[1]) if profile else {},
            conflicts=facts.get("conflicts", {}),
            result=state.final_response,
        )

    def check_documents(self, user_id, document_ids):
        for document_id in document_ids:
            self.documents.get_info(user_id, document_id)

    async def execute(self, state, token):
        from finance_agents.mcp_services.transport import ScopedMCPClient

        mcp = ScopedMCPClient(self.settings, self.store, token, state, self.services)
        classes = {
            "asset_allocation": AssetAllocationAgent,
            "tax_policy": TaxPolicyAgent,
            "claim_intake": ClaimIntakeAgent,
            "credit_assessment": CreditAssessmentAgent,
        }

        def factory(name, _state):
            if name in {"claim_intake", "credit_assessment"}:
                return classes[name](mcp, self.document_service)
            return classes[name](mcp)

        pending_events = []
        graph = build_graph(
            self.router,
            factory,
            self.optimizer,
            lambda run_id, kind, payload: pending_events.append((run_id, kind, payload)),
        )
        result = await graph.ainvoke({"session": state})
        for event in pending_events:
            await asyncio.to_thread(self.store.append_event, *event)
        return result["session"]

    async def start(self, request, identity, token):
        if request.session_id != identity["session_id"]:
            raise PermissionError("提交的会话与凭据不一致")
        await asyncio.to_thread(self.check_documents, identity["user_id"], request.attachment_ids)
        intent = Intent(request.intent_hint) if request.intent_hint else None
        async with self.lock(("request", identity["user_id"], request.request_id)):
            state = SessionState(
                run_id=uuid4().hex,
                **identity,
                message=request.message,
                initial_message=request.message,
                intent=intent,
                document_refs=request.attachment_ids,
            )
            cached = await asyncio.to_thread(
                self.store.begin_run_request,
                state,
                request.request_id,
                "start:" + request.model_dump_json(),
            )
            if cached is not None:
                return WorkflowResponse.model_validate(cached)
            return await self.advance(state, token, request.request_id)

    async def advance(self, state, token, request_id):
        version = state.state_version
        try:
            state = await self.execute(state, token)
        except Exception:
            state.status = RunStatus.FAILED
            state.last_question = "执行异常，运行已保存；请查看服务端日志并新建请求。"
            await asyncio.to_thread(
                self.store.append_event,
                state.run_id,
                "execution_error",
                {"code": "EXECUTION_FAILED"},
            )
            import logging

            logging.getLogger(__name__).exception("Workflow failed: %s", state.run_id)
        response = await asyncio.to_thread(
            self.response, state.model_copy(update={"state_version": version + 1})
        )
        await asyncio.to_thread(
            self.store.complete_run_request,
            state,
            request_id,
            version,
            response.model_dump(mode="json"),
        )
        return response

    async def resume(self, run_id, request, identity, token):
        async with self.lock(("run", run_id)):
            state = await asyncio.to_thread(self.store.get_run, run_id, identity["user_id"])
            if state.session_id != identity["session_id"]:
                raise PermissionError("运行不属于当前会话")
            await asyncio.to_thread(self.check_documents, state.user_id, request.attachment_ids)
            payload = "resume:" + run_id + ":" + request.model_dump_json()
            cached = await asyncio.to_thread(
                self.store.lookup_request, state.user_id, request.request_id, payload
            )
            if cached is not None:
                return WorkflowResponse.model_validate(cached)
            if state.status != RunStatus.WAITING_USER:
                raise ValueError(
                    "运行已结束或取消，不能继续。请使用复制为新业务重新核对字段，或新建请求。"
                )
            if state.state_version != request.state_version:
                raise ValueError("运行版本冲突，请重新加载")
            updated = state.model_copy(deep=True)
            if request.confirmed_fields:
                if state.intent not in FORMS:
                    raise TypeError("先澄清意图再确认表单")
                attr, model = FORMS[state.intent]
                current = getattr(state, attr) or model()
                values = current.model_dump(mode="json")
                confirmed = set(current.confirmed_fields)
                for key, value in request.confirmed_fields.items():
                    name = key.removeprefix(attr + ".")
                    if name not in model.model_fields or name in PROTECTED_FIELDS or value is None:
                        raise TypeError("非法或空确认字段：" + key)
                    values[name] = value
                    confirmed.add(name)
                values["confirmed_fields"] = sorted(confirmed)
                setattr(updated, attr, model.model_validate(values))
            updated.message = request.message
            updated.document_refs = list(
                dict.fromkeys(state.document_refs + request.attachment_ids)
            )
            reference = request.consent_ref or state.consent_ref
            updated.consent_ref = (
                reference
                if reference
                and await asyncio.to_thread(
                    self.store.check_consent, reference, state.user_id, run_id
                )
                else None
            )
            if updated.credit_form:
                updated.credit_form.consent_ref = updated.consent_ref
            updated.status = RunStatus.RUNNING
            cached = await asyncio.to_thread(
                self.store.begin_run_request,
                updated,
                request.request_id,
                payload,
                state.state_version,
            )
            if cached is not None:
                return WorkflowResponse.model_validate(cached)
            await asyncio.to_thread(
                self.store.append_event,
                run_id,
                "resume",
                {
                    "confirmed_fields": list(request.confirmed_fields),
                    "new_documents": len(request.attachment_ids),
                },
            )
            return await self.advance(updated, token, request.request_id)

    async def cancel(self, run_id, identity):
        async with self.lock(("run", run_id)):
            state = await asyncio.to_thread(self.store.get_run, run_id, identity["user_id"])
            if state.status == RunStatus.WAITING_USER:
                version = state.state_version
                state.status = RunStatus.CANCELLED
                state.wait_reason = None
                state.last_question = None
                await asyncio.to_thread(self.store.save_run, state, version)
                await asyncio.to_thread(self.store.append_event, run_id, "cancelled", {})
            return await asyncio.to_thread(self.response, state)

    async def copy_run(self, run_id, request, identity):
        async with self.lock(("request", identity["user_id"], request.request_id)):
            parent = await asyncio.to_thread(self.store.get_run, run_id, identity["user_id"])
            payload = "copy:" + run_id + ":" + request.model_dump_json()
            cached = await asyncio.to_thread(
                self.store.lookup_request, parent.user_id, request.request_id, payload
            )
            if cached is not None:
                return WorkflowResponse.model_validate(cached)
            ended = {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED}
            if parent.intent not in FORMS or parent.status not in ended:
                raise ValueError("仅可复制已结束的金融业务运行")
            await asyncio.to_thread(self.check_documents, parent.user_id, parent.document_refs)
            copied = parent.model_copy(
                deep=True,
                update={
                    "run_id": uuid4().hex,
                    "parent_run_id": run_id,
                    "session_id": identity["session_id"],
                    "state_version": 1,
                    "status": RunStatus.WAITING_USER,
                    "wait_reason": WaitReason.CONFIRM_FORM,
                    "pending_fields": ["confirmed_fields"],
                    "consent_ref": None,
                    "branch_result": None,
                    "final_response": None,
                    "last_question": "已复制材料与字段值。请重新核对全部字段。"
                    + ("贷款还需重新授权本次模拟评估。" if parent.intent == Intent.LOAN else ""),
                    "message": "已复制业务，请重新核对字段。",
                },
            )
            for name, _ in FORMS.values():
                form = getattr(copied, name)
                if form is not None:
                    form.confirmed_fields = []
            if copied.credit_form is not None:
                copied.credit_form.consent_ref = None
            for field in copied.extracted_fields:
                field.confirmed = False
            response = await asyncio.to_thread(self.response, copied)
            cached = await asyncio.to_thread(
                self.store.begin_run_request,
                copied,
                request.request_id,
                payload,
                result=response.model_dump(mode="json"),
            )
            if cached is not None:
                return WorkflowResponse.model_validate(cached)
            return response

    def demo(self, user_id):
        import fitz

        self.claims.seed_demo(user_id)
        texts = [
            "演示保单材料（模拟）\n保单号：DEMO-POLICY-001\n条款版本：demo-v1\n出险类型：hospitalization\n出险日期：2026-10-01",
            "演示收入负债证明（模拟）\n月收入：8000元\n每月还款：1000元\n就业状态：employed",
        ]
        documents = []
        for index, text in enumerate(texts):
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((50, 60), text, fontname="china-s", fontsize=16)
                documents.append(
                    self.documents.save(
                        user_id, f"demo-{index}.pdf", pdf.tobytes(), "application/pdf"
                    )
                )
        return {
            "data_mode": "mock",
            "documents": documents,
            "document_ids": [d["document_id"] for d in documents],
            "policies": [
                {"policy_id": "DEMO-POLICY-001", "policy_version": "demo-v1", "data_mode": "mock"}
            ],
            "note": "演示保单和收入材料均为模拟。基金净值快照来自公开真实来源，非实时交易报价。",
        }
