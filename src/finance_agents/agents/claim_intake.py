"""Upload, extract, confirm, then query user-scoped policy history and terms."""

from collections import defaultdict
from datetime import datetime, timezone

from pydantic import ValidationError

from finance_agents.contracts.common import AuthContext, DataMode, EvidenceRef, ResultStatus
from finance_agents.contracts.profiles import ClaimForm
from finance_agents.contracts.results import AgentResult
from finance_agents.tools.ocr import OCRUnavailable, parse_text_fields
from finance_agents.tools.form_filler import canonical_value
from finance_agents.workflow.state import SessionState


class ClaimIntakeAgent:
    required_fields = ("policy_id", "policy_version", "incident_type", "incident_date")

    def __init__(self, mcp, document_service=None):
        self.mcp, self.document_service = mcp, document_service

    async def run(self, state: SessionState) -> AgentResult:
        def waiting(message, missing, form=None, conflicts=None, warnings=None):
            return AgentResult(
                agent_type="claim_intake",
                status=ResultStatus.NEEDS_INPUT,
                data_mode=DataMode.REAL,
                summary=message,
                missing_fields=missing,
                structured_facts={
                    "form": form.model_dump(mode="json") if form else {},
                    "conflicts": conflicts or {},
                },
                warnings=warnings or [],
                next_action=message,
            )

        if not state.document_refs:
            return waiting(
                "请上传保单、条款版本和出险凭证（PDF、PNG 或 JPEG）。", ["document_refs"]
            )
        warnings = []
        new_document_ids = set()
        pending_fields = []
        try:
            known = {f.document_id for f in state.extracted_fields}
            for document_id in state.document_refs:
                if self.document_service is not None:
                    extraction = await self.document_service.extract(state.user_id, document_id)
                    if document_id not in known:
                        pending_fields.extend(extraction.fields)
                        new_document_ids.add(document_id)
                        known.add(document_id)
                    warnings.extend(extraction.warnings)
                elif document_id not in known:
                    raise OCRUnavailable("Document extraction service is not configured")
            state.extracted_fields.extend(pending_fields)
        except (OCRUnavailable, PermissionError, ValueError, OSError) as exc:
            return AgentResult(
                agent_type="claim_intake",
                status=ResultStatus.UNAVAILABLE,
                data_mode=DataMode.REAL,
                summary="材料读取或识别不可用，请检查材料。",
                warnings=[str(exc)],
            )
        form = (state.claim_form or ClaimForm()).model_copy(deep=True)
        data = form.model_dump(mode="json")
        candidates = defaultdict(list)
        for field in state.extracted_fields + parse_text_fields(
            state.message, None, source="user_message"
        ):
            if field.name in ClaimForm.model_fields and field.name != "confirmed_fields":
                candidates[field.name].append(field)
        conflicts = {}
        for name, fields in candidates.items():
            if name in form.confirmed_fields:
                new = [
                    f
                    for f in fields
                    if f.source == "user_message" or f.document_id in new_document_ids
                ]
                if not new or all(
                    canonical_value(f.value) == canonical_value(data[name]) for f in new
                ):
                    continue
                conflicts[name] = [
                    {"value": data[name], "source": "confirmed_form", "confirmed": True},
                    *[f.model_dump(mode="json") for f in fields],
                ]
                data[name] = None
                data["confirmed_fields"] = [n for n in data["confirmed_fields"] if n != name]
                continue
            unique = {canonical_value(f.value) for f in fields}
            if len(unique) > 1 or (
                data.get(name) is not None
                and canonical_value(data[name]) != canonical_value(fields[0].value)
            ):
                conflicts[name] = [f.model_dump(mode="json") for f in fields]
                if data.get(name) is not None:
                    conflicts[name].append({"value": data[name], "source": "current_form"})
                data[name] = None
                data["confirmed_fields"] = [n for n in data["confirmed_fields"] if n != name]
            else:
                data[name] = fields[0].value
        try:
            form = ClaimForm.model_validate(data)
        except ValidationError as exc:
            return waiting(
                "提取字段格式无效，请核对事故日期与金额并修正。",
                ["confirmed_fields"],
                form,
                warnings=[str(exc)],
            )
        state.claim_form = form
        if conflicts:
            return waiting(
                "材料与输入存在冲突，请给出正确值并确认。",
                ["confirmed_fields"],
                form,
                conflicts,
                warnings,
            )
        missing = [name for name in self.required_fields if getattr(form, name) is None]
        if missing:
            return waiting(
                "请补充理赔字段：" + "、".join(missing), missing, form, warnings=warnings
            )
        required_confirmation = list(self.required_fields) + [
            name
            for name in ("premium", "insured_amount", "insurer", "insurance_type")
            if getattr(form, name) is not None
        ]
        unconfirmed = [name for name in required_confirmation if name not in form.confirmed_fields]
        if unconfirmed:
            return waiting(
                "请确认保单、条款版本、出险信息及所有金额后再查询条款。",
                ["confirmed_fields"],
                form,
                warnings=warnings + ["待确认：" + "、".join(unconfirmed)],
            )
        auth = AuthContext(
            user_id=state.user_id, session_id=state.session_id, consent_ref=state.consent_ref
        )
        arguments = {name: data[name] for name in self.required_fields}
        history = await self.mcp.call("claims.lookup_policies", arguments, auth)
        if history.status != ResultStatus.SUCCESS:
            return AgentResult(
                agent_type="claim_intake",
                status=history.status,
                data_mode=history.data_mode,
                summary="未找到当前用户对应的有效保单记录。",
                structured_facts={"form": form.model_dump(mode="json")},
                warnings=history.warnings + ([history.error.message] if history.error else []),
            )
        clauses = await self.mcp.call("claims.match_clauses", arguments, auth)
        if clauses.status != ResultStatus.SUCCESS:
            return AgentResult(
                agent_type="claim_intake",
                status=clauses.status,
                data_mode=clauses.data_mode,
                summary="条款不可用，无法进行初步判定。",
                structured_facts={"form": form.model_dump(mode="json")},
                warnings=clauses.warnings + ([clauses.error.message] if clauses.error else []),
            )
        result = await self.mcp.call("claims.check_conditions", arguments, auth)
        document_evidence = [
            EvidenceRef(
                source_id=f"document:{f.document_id}:{f.name}",
                source_title="用户上传材料",
                document_id=f.document_id,
                page_number=f.page_number,
                quote=f.quote,
                retrieved_at=datetime.now(timezone.utc),
            )
            for f in state.extracted_fields
            if f.document_id
        ]
        outcomes = {
            "potentially_covered": (
                "该事故可能属于潜在保障范围，仍须核对完整条款与材料。",
                "请补齐出险及费用凭证，并由保险机构核对除外责任、免赔额及其他理赔条件。",
            ),
            "outside_coverage_dates": (
                "事故日期不在保障期内。",
                "请核对事故日期、保单生效和终止日期；如需修正，请重新确认后核对，并联系保险机构复核。",
            ),
            "excluded_by_provided_clause": (
                "当前提供的条款排除该事故类型。",
                "请核对条款版本、排除条款及事故分类，并联系保险机构复核适用条件。",
            ),
            "requires_manual_clause_review": (
                "未匹配到明确适用条款，需人工复核。",
                "请提供完整条款及事故说明，由保险机构人工复核是否存在适用保障或除外责任。",
            ),
        }
        label = "模拟保单初步筛查" if result.data_mode == DataMode.MOCK else "保单初步筛查"
        if result.status == ResultStatus.SUCCESS:
            outcome, next_action = outcomes.get(
                (result.data or {}).get("preliminary_result"),
                ("工具未返回明确初筛分类，需人工复核。", "请联系保险机构核对完整条款与出险材料。"),
            )
            summary = f"{label}：{outcome}实际是否赔付及赔付金额须由保险机构审核。"
        else:
            summary = "理赔工具未返回可用初筛结果。"
            next_action = "请检查保单版本、出险材料及工具状态，必要时联系保险机构。"
        return AgentResult(
            agent_type="claim_intake",
            status=result.status,
            data_mode=result.data_mode,
            summary=summary,
            next_action=next_action,
            structured_facts={
                "form": form.model_dump(mode="json"),
                "policy_history": history.data,
                "clause_check": result.data,
            },
            evidence=result.evidence + document_evidence,
            warnings=list(
                dict.fromkeys(
                    warnings
                    + history.warnings
                    + result.warnings
                    + ([result.error.message] if result.error else [])
                )
            ),
        )
