"""Independent four-service MCP SDK stdio servers, without a workflow or LLM."""

import argparse

from mcp.server.fastmcp import FastMCP

from finance_agents.contracts.common import AuthContext
from finance_agents.core.settings import Settings
from finance_agents.mcp_services.transport import TOOL_ALLOWLIST, ScopedDispatcher
from finance_agents.storage.database import create_database_engine
from finance_agents.storage.runtime_store import RuntimeStore


def create_mcp_server(service_name, settings=None):
    if service_name not in TOOL_ALLOWLIST:
        raise ValueError("Unknown MCP service")
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings.database_dsn)
    runtime = RuntimeStore(engine)
    if service_name == "credit":
        from finance_agents.mcp_services.credit import CreditMCPService

        service = CreditMCPService()
    elif service_name == "claims":
        from finance_agents.mcp_services.claims import ClaimsMCPService
        from finance_agents.storage.claims_store import ClaimsStore

        service = ClaimsMCPService(ClaimsStore(engine))
    elif service_name == "market":
        from finance_agents.mcp_services.market import MarketMCPService
        from finance_agents.storage.market_store import MarketStore

        service = MarketMCPService(MarketStore(engine))
    else:
        from finance_agents.mcp_services.tax import TaxMCPService
        from finance_agents.storage.policy_store import PolicyStore

        service = TaxMCPService(PolicyStore(engine))
    dispatcher = ScopedDispatcher(runtime, {service_name: service})
    server = FastMCP(f"finance-{service_name}", log_level="ERROR")

    def register(tool_name):
        async def invoke(arguments: dict, token: str, run_id: str, auth: AuthContext) -> dict:
            result = await dispatcher.dispatch(tool_name, arguments, token, run_id, auth)
            return result.model_dump(mode="json")

        server.add_tool(
            invoke,
            name=tool_name,
            description="User-scoped financial business tool; requires authenticated run ownership",
        )

    for tool_name in TOOL_ALLOWLIST[service_name]:
        register(tool_name)
    return server


def main():
    parser = argparse.ArgumentParser(description="Run one financial MCP service via stdio")
    parser.add_argument("--service", required=True, choices=tuple(TOOL_ALLOWLIST))
    args = parser.parse_args()
    create_mcp_server(args.service).run(transport="stdio")


if __name__ == "__main__":
    main()
