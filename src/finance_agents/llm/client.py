"""可替换模型端口，DeepSeek适配器见deepseek.py。"""

from typing import Any, Protocol


class LLMClient(Protocol):
    async def generate_structured(
        self,
        system_prompt: str,
        user_input: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...
