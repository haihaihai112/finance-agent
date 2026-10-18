"""Explicit document or self-reported credit intake, confirmation and consent gates."""

from datetime import datetime, timezone

from pydantic import ValidationError

from finance_agents.contracts.common import AuthContext, DataMode, EvidenceRef, ResultStatus
from finance_agents.contracts.results import AgentResult
from finance_agents.tools.form_filler import CreditFormFiller
from finance_agents.tools.ocr import OCRUnavailable
from finance_agents.workflow.state import SessionState


class CreditAssessmentAgent:
    def __init__(self, mcp, document_service=None):
        self.mcp, self.document_service = mcp, document_service

    async def run(self, state: SessionState) -> AgentResult:
        intake_mode = state.credit_form.intake_mode if state.credit_form else "documents"
        self_reported = intake_mode == "self_reported"
        provenance = "自填、未经材料核验" if self_reported else "用户上传材料与确认字段"
        mode_warnings = (
            ["自填、未经材料核验；仅供模拟试算，不代表真实征信或银行审批。"]
            if self_reported
            else []
        )

        def describe(message):
            return f"自填、未经材料核验。{message}" if self_reported else message

        def waiting(message, missing, form=None, conflicts=None, warnings=None):
            return AgentResult(
                agent_type="credit_assessment",
                status=ResultStatus.NEEDS_INPUT,
                data_mode=DataMode.MOCK,
                summary=describe(message),
                missing_fields=missing,
                structured_facts={
                    "form": form.model_dump(mode="json") if form else {},
                    "conflicts": conflicts or {},
                    "intake_mode": intake_mode,
                    "data_provenance": provenance,
                },
                next_action=describe(message),
                warnings=mode_warnings + (warnings or []),
            )

        if not self_reported and not state.document_refs:
            return waiting(
                "请上传收入、负债等贷款材料（PDF、PNG 或 JPEG）；可选择自填试算/演示材料/取消。",
                ["document_refs"],
                state.credit_form,
            )
        warnings = []
        pending_fields = []
        try:
            known = {f.document_id for f in state.extracted_fields}
            for document_id in [] if self_reported else state.document_refs:
                if self.document_service is not None:
                    extraction = await self.document_service.extract(state.user_id, document_id)
                    if document_id not in known:
                        pending_fields.extend(extraction.fields)
                        known.add(document_id)
                    warnings.extend(extraction.warnings)
                elif document_id not in known:
                    raise OCRUnavailable("Document extraction service is not configured")
            state.extracted_fields.extend(pending_fields)
        except (OCRUnavailable, PermissionError, ValueError, OSError) as exc:
            return AgentResult(
                agent_type="credit_assessment",
                status=ResultStatus.UNAVAILABLE,
                data_mode=DataMode.MOCK,
                summary="材料读取或识别不可用，请检查材料。",
                warnings=[str(exc)],
            )
        filler = CreditFormFiller()
        try:
            form = await filler.prefill(
                [] if self_reported else state.extracted_fields, state.message, state.credit_form
            )
        except (ValidationError, ValueError) as exc:
            return waiting(
                "部分提取值格式无效，请核对并提供结构化修正。",
                ["confirmed_fields"],
                state.credit_form,
                warnings=[str(exc)],
            )
        if not self_reported:
            form.document_ids = list(dict.fromkeys(form.document_ids + state.document_refs))
        # Only the workflow's validated consent reference can authorize the service call.
        form.consent_ref = state.consent_ref
        state.credit_form = form
        if filler.conflicts:
            return waiting(
                "材料或提示中的字段存在冲突，请明确正确值并确认。",
                ["confirmed_fields"],
                form,
                filler.conflicts,
                warnings,
            )
        missing = [name for name in filler.required_fields if getattr(form, name) is None]
        if missing:
            return waiting(
                "请补充贷款表单缺失字段：" + "、".join(missing), missing, form, warnings=warnings
            )
        currency_fields = [
            name
            for name in (
                "requested_amount",
                "monthly_income",
                "monthly_debt_payment",
                "assets_total",
                "liabilities_total",
            )
            if getattr(form, name) is not None and getattr(form, name).currency.upper() == "UNK"
        ]
        if currency_fields:
            return waiting(
                "原文未注明币种，请确认金额币种：" + "、".join(currency_fields),
                ["confirmed_fields"],
                form,
                warnings=warnings,
            )
        required_confirmation = list(filler.required_fields) + [
            name
            for name in ("employment_months", "assets_total", "liabilities_total")
            if getattr(form, name) is not None
        ]
        if self_reported:
            required_confirmation.append("intake_mode")
        unconfirmed = [name for name in required_confirmation if name not in form.confirmed_fields]
        if unconfirmed:
            return waiting(
                "请核对并确认自填模式与所有字段后再试算。"
                if self_reported
                else "请核对并确认所有字段，OCR 金额未经确认不能进入风控。",
                ["confirmed_fields"],
                form,
                warnings=warnings + ["待确认：" + "、".join(unconfirmed)],
            )
        if not state.consent_ref:
            return waiting(
                "请授权本次模拟征信与风控评估。", ["consent_ref"], form, warnings=warnings
            )
        try:
            model_input = filler.to_model_input(form, "demo-v1")
        except ValueError as exc:
            return waiting(
                "表单校验未通过，请修正金额、币种或确认状态。",
                ["confirmed_fields"],
                form,
                warnings=[str(exc)],
            )
        auth = AuthContext(
            user_id=state.user_id, session_id=state.session_id, consent_ref=state.consent_ref
        )
        report = await self.mcp.call(
            "credit.query_mock_report",
            {"subject_ref": model_input.subject_ref, "consent_ref": state.consent_ref},
            auth,
        )
        if report.status != ResultStatus.SUCCESS:
            return AgentResult(
                agent_type="credit_assessment",
                status=report.status,
                data_mode=DataMode.MOCK if self_reported else report.data_mode,
                summary=describe("征信工具未返回可用结果。"),
                structured_facts={
                    "form": form.model_dump(mode="json"),
                    "intake_mode": intake_mode,
                    "data_provenance": provenance,
                },
                warnings=mode_warnings
                + report.warnings
                + ([report.error.message] if report.error else []),
            )
        result = await self.mcp.call(
            "credit.evaluate_mock",
            {
                "model_input": model_input.model_dump(mode="json"),
                "consent_ref": state.consent_ref,
                "confirmed_fields": form.confirmed_fields,
            },
            auth,
        )
        document_evidence = [
            EvidenceRef(
                source_id=f"document:{f.document_id}:{f.name}",
                source_title="用户上传材料",
                document_id=f.document_id,
                page_number=f.page_number,
                quote=f.quote,
                retrieved_at=datetime.now(timezone.utc),
            )
            for f in ([] if self_reported else state.extracted_fields)
            if f.document_id
        ]
        if result.status == ResultStatus.SUCCESS:
            assessment = result.data or {}
            outcome = {
                "demo_pass": "演示规则通过",
                "manual_review": "需人工复核",
            }.get(assessment.get("result"), "演示结果未明确，需人工复核")
            principal = assessment.get("principal_only_monthly_payment")
            principal_description = (
                f"新增贷款平均月还本金约 {principal} {model_input.currency}（未计利息及费用）"
                if principal is not None
                else "工具未返回新增贷款月还本金估算"
            )
            summary = (
                f"模拟评估：{outcome}。已确认月收入 {model_input.monthly_income} {model_input.currency}、"
                f"现有每月负债还款 {model_input.monthly_debt_payment} {model_input.currency}；"
                f"{principal_description}。结果不代表银行审批或实际授信。"
            )
            next_action = (
                "请核对收入、负债和月还本金估算；可复制为新申请后修改申请金额或贷款期限，"
                "重新确认全部字段并授权后重新评估。实际利息、费用与贷款条件须向贷款机构核实。"
            )
        else:
            summary = "风控工具未返回可用结果。"
            next_action = "请检查表单及授权有效性后重试。"
        return AgentResult(
            agent_type="credit_assessment",
            status=result.status,
            data_mode=DataMode.MOCK if self_reported else result.data_mode,
            summary=describe(summary),
            next_action=next_action,
            structured_facts={
                "form": form.model_dump(mode="json"),
                "mock_credit_report": report.data,
                "assessment": result.data,
                "intake_mode": intake_mode,
                "data_provenance": provenance,
            },
            calculations=result.data or {},
            evidence=result.evidence + document_evidence,
            warnings=list(
                dict.fromkeys(
                    mode_warnings
                    + warnings
                    + report.warnings
                    + result.warnings
                    + ([result.error.message] if result.error else [])
                )
            ),
        )
