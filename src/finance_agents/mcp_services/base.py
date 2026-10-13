"""四个MCP服务业务端口，协议注册和传输见server/transport。"""

from typing import Any, Protocol

from finance_agents.contracts.common import AuthContext
from finance_agents.contracts.results import ToolResult


class MCPClient(Protocol):
    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        auth: AuthContext,
    ) -> ToolResult:
        """经 MCP 传输层调用受控工具。"""
        ...


class MCPServiceSkeleton:
    name: str
    tools: tuple[str, ...]
