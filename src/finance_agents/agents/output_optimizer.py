"""专业结果表达：结构化事实不变，模型仅可整理已产生的结论。"""

import re
from finance_agents.contracts.common import DataMode
from finance_agents.contracts.results import AgentResult, FinalResponse


class OutputOptimizerAgent:
    def __init__(self, llm=None):
        self.llm = llm

    async def format(self, result: AgentResult) -> FinalResponse:
        conclusion = result.summary
        limitations = list(result.warnings)
        if result.data_mode == DataMode.MOCK:
            limitations.append("此结果使用模拟规则或演示资料，不代表真实授信、赔付或金融机构决定。")
        if self.llm:
            try:
                polished = await self.llm.generate_structured(
                    "只调整原文分段、空白和标点。词语及顺序、数值、结论必须完全保留，不得改写或补充。输出 JSON，只含 conclusion 字段。",
                    result.summary,
                    {
                        "type": "object",
                        "properties": {"conclusion": {"type": "string"}},
                        "required": ["conclusion"],
                        "additionalProperties": False,
                    },
                )
                text = polished["conclusion"]

                def numbers(value):
                    return re.findall(r"[-+]?\d+(?:\.\d+)?", value)

                if not isinstance(text, str) or numbers(text) != numbers(result.summary):
                    raise ValueError("模型改变了数字")
                if re.findall(r"\w", text) != re.findall(r"\w", result.summary):
                    raise ValueError("模型改变了结论内容")
                conclusion = text
            except (RuntimeError, ValueError, KeyError, TypeError):
                limitations.append("模型整理不可用或保真校验未通过，展示原始专业结论。")
        return FinalResponse(
            conclusion=conclusion,
            facts={
                "structured_facts": result.structured_facts,
                "calculations": result.calculations,
            },
            citations=result.evidence,
            limitations=limitations,
            next_action=result.next_action,
            data_mode=result.data_mode,
        )
