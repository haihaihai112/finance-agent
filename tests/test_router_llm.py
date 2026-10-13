import asyncio
import json

import httpx
import pytest


@pytest.mark.parametrize(
    "message,intent",
    [
        ("我有10万元想投资基金", "investment"),
        ("提交保险理赔申请", "claim"),
        ("我想申请贷款", "loan"),
        ("居民综合所得个税怎么计算", "policy_tax"),
        ("今天会下雨吗", "other"),
    ],
)
def test_router_routes_five_intents_without_paid_model(message, intent):
    from finance_agents.agents.intent_router import IntentRouterAgent
    from finance_agents.contracts.requests import QueryRequest

    result = asyncio.run(
        IntentRouterAgent().classify(
            QueryRequest(session_id="s", request_id="k", message=message),
        )
    )
    assert result.intent.value == intent
    assert not result.requires_clarification


def test_router_clarifies_multiple_business_intents():
    from finance_agents.agents.intent_router import IntentRouterAgent
    from finance_agents.contracts.requests import QueryRequest

    result = asyncio.run(
        IntentRouterAgent().classify(
            QueryRequest(session_id="s", request_id="k", message="基金投资与贷款怎么安排"),
        )
    )
    assert result.requires_clarification
    assert result.intent is None


def test_deepseek_uses_json_output_and_never_logs_or_returns_key():
    from finance_agents.llm.deepseek import DeepSeekClient

    observed = {}

    def respond(request):
        observed.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-only-not-a-real-key"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"intent":"loan"}'}}],
            },
        )

    client = DeepSeekClient("test-only-not-a-real-key", transport=httpx.MockTransport(respond))
    result = asyncio.run(client.generate_structured("返回 JSON", "我申请贷款", {"type": "object"}))
    assert result == {"intent": "loan"}
    assert observed["response_format"] == {"type": "json_object"}
    assert "test-only-not-a-real-key" not in repr(client)


def test_optimizer_keeps_calculations_and_mock_label():
    from finance_agents.agents.output_optimizer import OutputOptimizerAgent
    from finance_agents.contracts.common import DataMode, ResultStatus
    from finance_agents.contracts.results import AgentResult

    result = asyncio.run(
        OutputOptimizerAgent().format(
            AgentResult(
                agent_type="credit_assessment",
                status=ResultStatus.SUCCESS,
                data_mode=DataMode.MOCK,
                summary="模拟评分为650。",
                calculations={"score": 650, "amount": "10000.00"},
            )
        )
    )
    assert result.facts["calculations"]["score"] == 650
    assert result.data_mode == DataMode.MOCK
    assert any("模拟" in text for text in result.limitations)


def test_optimizer_rejects_changed_financial_meaning_without_numbers():
    from finance_agents.agents.output_optimizer import OutputOptimizerAgent
    from finance_agents.contracts.common import DataMode, ResultStatus
    from finance_agents.contracts.results import AgentResult

    class MisleadingModel:
        async def generate_structured(self, *args):
            return {"conclusion": "银行已经批准贷款。"}

    result = asyncio.run(
        OutputOptimizerAgent(MisleadingModel()).format(
            AgentResult(
                agent_type="credit_assessment",
                status=ResultStatus.SUCCESS,
                data_mode=DataMode.MOCK,
                summary="演示结果需要人工复核。",
            )
        )
    )
    assert result.conclusion == "演示结果需要人工复核。"
