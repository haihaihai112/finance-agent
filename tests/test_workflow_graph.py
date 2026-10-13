import asyncio

from finance_agents.agents.intent_router import IntentRouterAgent
from finance_agents.agents.output_optimizer import OutputOptimizerAgent
from finance_agents.contracts.common import DataMode, ResultStatus
from finance_agents.contracts.results import AgentResult
from finance_agents.workflow.state import RunStatus, SessionState, WaitReason


def test_graph_waits_for_docs_without_producing_a_final_answer():
    from finance_agents.workflow.graph import build_graph

    class Agent:
        async def run(self, state):
            return AgentResult(
                agent_type="credit_assessment",
                status=ResultStatus.NEEDS_INPUT,
                data_mode=DataMode.MOCK,
                summary="请上传材料",
                missing_fields=["document_refs"],
            )

    graph = build_graph(IntentRouterAgent(), lambda name, state: Agent(), OutputOptimizerAgent())
    output = asyncio.run(
        graph.ainvoke(
            {
                "session": SessionState(
                    run_id="r",
                    user_id="u",
                    session_id="s",
                    message="申请贷款",
                )
            }
        )
    )
    state = output["session"]
    assert state.status == RunStatus.WAITING_USER
    assert state.wait_reason == WaitReason.UPLOAD_DOCUMENTS
    assert state.final_response is None


def test_graph_has_all_six_agent_nodes_and_completes_a_business_result():
    from finance_agents.workflow.graph import build_graph

    class Agent:
        async def run(self, state):
            return AgentResult(
                agent_type="asset_allocation",
                status=ResultStatus.SUCCESS,
                data_mode=DataMode.REAL,
                summary="真实证据已返回",
            )

    graph = build_graph(IntentRouterAgent(), lambda name, state: Agent(), OutputOptimizerAgent())
    assert {
        "intent_router",
        "asset_allocation",
        "claim_intake",
        "credit_assessment",
        "tax_policy",
        "output_optimizer",
    } <= set(graph.nodes)
    result = asyncio.run(
        graph.ainvoke(
            {
                "session": SessionState(
                    run_id="r",
                    user_id="u",
                    session_id="s",
                    message="投资基金",
                )
            }
        )
    )["session"]
    assert result.status == RunStatus.COMPLETED
    assert result.final_response.conclusion == "真实证据已返回"


def test_other_intent_never_calls_a_financial_agent():
    from finance_agents.workflow.graph import build_graph

    def forbidden_factory(*args):
        raise AssertionError("不应分发金融工具")

    graph = build_graph(IntentRouterAgent(), forbidden_factory, OutputOptimizerAgent())
    result = asyncio.run(
        graph.ainvoke(
            {
                "session": SessionState(
                    run_id="r",
                    user_id="u",
                    session_id="s",
                    message="今天会下雨吗",
                )
            }
        )
    )["session"]
    assert result.status == RunStatus.COMPLETED
    assert "范围" in result.final_response.conclusion
