"""六 Agent LangGraph。业务检查点由 SQL 仓储保存，补答从暂停业务重入。"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from finance_agents.contracts.common import DataMode, ResultStatus
from finance_agents.contracts.requests import QueryRequest
from finance_agents.contracts.results import FinalResponse, Intent
from finance_agents.workflow.state import RunStatus, SessionState, WaitReason

AGENT_BY_INTENT = {
    Intent.INVESTMENT: "asset_allocation",
    Intent.CLAIM: "claim_intake",
    Intent.LOAN: "credit_assessment",
    Intent.POLICY_TAX: "tax_policy",
}


class GraphState(TypedDict):
    session: SessionState


def build_graph(router, agent_factory, optimizer, record_event=None):
    def event(state, kind, payload):
        if record_event:
            record_event(state.run_id, kind, payload)

    async def route(data):
        state = data["session"]
        state.wait_reason = None
        state.pending_fields = []
        state.last_question = None
        state.final_response = None
        if state.intent is None:
            decision = await router.classify(
                QueryRequest(
                    session_id=state.session_id,
                    request_id=state.run_id,
                    message=state.message,
                )
            )
            event(
                state,
                "intent",
                {"intent": decision.intent, "mode": decision.source, "warnings": decision.warnings},
            )
            if decision.requires_clarification or decision.intent is None:
                state.status = RunStatus.WAITING_USER
                state.wait_reason = WaitReason.CLARIFY_INTENT
                state.last_question = decision.clarification
                return {"session": state}
            state.intent = decision.intent
        state.active_agent = AGENT_BY_INTENT.get(state.intent)
        state.status = RunStatus.ROUTED
        return {"session": state}

    def dispatch(data):
        state = data["session"]
        if state.wait_reason == WaitReason.CLARIFY_INTENT:
            return END
        if state.intent == Intent.OTHER:
            return "out_of_scope"
        return AGENT_BY_INTENT[state.intent]

    def specialist(name):
        async def process(data):
            state = data["session"]
            state.status = RunStatus.RUNNING
            result = await agent_factory(name, state).run(state)
            state.branch_result = result
            event(
                state,
                "agent_result",
                {"agent": name, "status": result.status, "data_mode": result.data_mode},
            )
            if result.status == ResultStatus.NEEDS_INPUT:
                state.status = RunStatus.WAITING_USER
                state.pending_fields = result.missing_fields
                state.last_question = result.next_action or result.summary
                names = set(result.missing_fields)
                if "document_refs" in names:
                    state.wait_reason = WaitReason.UPLOAD_DOCUMENTS
                elif "confirmed_fields" in names:
                    state.wait_reason = WaitReason.CONFIRM_FORM
                elif "consent_ref" in names:
                    state.wait_reason = WaitReason.CONSENT
                else:
                    state.wait_reason = WaitReason.MISSING_INFORMATION
            return {"session": state}

        return process

    def after_specialist(data):
        return END if data["session"].status == RunStatus.WAITING_USER else "output_optimizer"

    async def format_result(data):
        state = data["session"]
        state.status = RunStatus.FORMATTING
        state.final_response = await optimizer.format(state.branch_result)
        state.status = (
            RunStatus.COMPLETED
            if state.branch_result.status == ResultStatus.SUCCESS
            else RunStatus.FAILED
            if state.branch_result.status == ResultStatus.FAILED
            else RunStatus.PARTIAL
        )
        event(state, "finished", {"status": state.status})
        return {"session": state}

    def out_of_scope(data):
        state = data["session"]
        state.status = RunStatus.COMPLETED
        state.final_response = FinalResponse(
            conclusion="此问题超出当前金融服务范围。支持投资咨询、理赔申请、贷款申请和政策／税务。",
            data_mode=DataMode.REAL,
        )
        return {"session": state}

    graph = StateGraph(GraphState)
    graph.add_node("intent_router", route)
    for name in AGENT_BY_INTENT.values():
        graph.add_node(name, specialist(name))
        graph.add_conditional_edges(name, after_specialist, [END, "output_optimizer"])
    graph.add_node("output_optimizer", format_result)
    graph.add_node("out_of_scope", out_of_scope)
    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges(
        "intent_router", dispatch, [END, "out_of_scope", *AGENT_BY_INTENT.values()]
    )
    graph.add_edge("output_optimizer", END)
    graph.add_edge("out_of_scope", END)
    return graph.compile()
