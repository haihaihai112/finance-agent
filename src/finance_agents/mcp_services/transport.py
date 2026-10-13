"""Scoped business dispatcher and real MCP stdio client transport."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from finance_agents.contracts.common import AuthContext, DataMode, ResultStatus
from finance_agents.contracts.profiles import CreditModelInput
from finance_agents.contracts.results import ToolError, ToolResult
from finance_agents.tools.form_filler import CreditFormFiller

TOOL_ALLOWLIST = {
    "market": ("market.query_prices", "market.search_context", "market.compute_metrics"),
    "claims": ("claims.lookup_policies", "claims.match_clauses", "claims.check_conditions"),
    "credit": ("credit.query_mock_report", "credit.evaluate_mock"),
    "tax": ("tax.search_rules", "tax.calculate"),
}
ALL_TOOLS = frozenset(tool for tools in TOOL_ALLOWLIST.values() for tool in tools)


def rejection(tool_name, code, message, unavailable=False):
    return ToolResult(
        status=ResultStatus.UNAVAILABLE if unavailable else ResultStatus.FAILED,
        data_mode=DataMode.MOCK if tool_name.startswith("credit.") else DataMode.REAL,
        error=ToolError(code=code, message=message, retryable=unavailable),
    )


class ScopedDispatcher:
    """Shared validation runs in both the app and the independent MCP process."""

    def __init__(self, runtime_store, services):
        self.runtime_store, self.services = runtime_store, services

    def validate(self, tool_name, arguments, token, run_id, auth, expected_state=None):
        if tool_name not in ALL_TOOLS:
            return rejection(tool_name, "UNKNOWN_TOOL", "Tool is not registered in the allowlist")
        if not isinstance(arguments, dict):
            return rejection(tool_name, "INVALID_INPUT", "Tool arguments must be an object")
        try:
            auth = AuthContext.model_validate(auth)
            identity = self.runtime_store.authenticate(token)
            if (identity["user_id"], identity["session_id"]) != (auth.user_id, auth.session_id):
                raise PermissionError("Auth context does not match the trusted session")
            state = self.runtime_store.get_run(run_id, identity["user_id"])
            if state.session_id != identity["session_id"]:
                raise PermissionError("Run does not belong to the authenticated session")
            if expected_state is not None and (
                expected_state.run_id != state.run_id
                or expected_state.user_id != identity["user_id"]
                or expected_state.session_id != identity["session_id"]
            ):
                raise PermissionError("Active state does not match authenticated run ownership")
            if arguments.get("user_id", identity["user_id"]) != identity["user_id"]:
                raise PermissionError("Cross-user tool invocation is denied")
            if tool_name.startswith("credit."):
                reference = auth.consent_ref
                if (
                    not reference
                    or not self.runtime_store.check_consent(reference, identity["user_id"], run_id)
                    or arguments.get("consent_ref", reference) != reference
                    or state.consent_ref != reference
                ):
                    return rejection(
                        tool_name,
                        "CONSENT_REQUIRED",
                        "Consent is missing, invalid, revoked or expired",
                    )
                form = state.credit_form
                if form is None:
                    return rejection(
                        tool_name,
                        "UNCONFIRMED_FIELDS",
                        "No confirmed credit form exists for this run",
                    )
                if form.consent_ref != reference:
                    return rejection(
                        tool_name,
                        "CONSENT_REQUIRED",
                        "Form consent does not match active authorization",
                    )
                try:
                    expected_row = CreditFormFiller().to_model_input(form, "demo-v1")
                except ValueError:
                    return rejection(
                        tool_name,
                        "UNCONFIRMED_FIELDS",
                        "The stored credit form is incomplete or unconfirmed",
                    )
                if tool_name == "credit.evaluate_mock":
                    supplied = CreditModelInput.model_validate(arguments.get("model_input", {}))
                    if supplied != expected_row:
                        return rejection(
                            tool_name,
                            "FORM_MISMATCH",
                            "Submitted model row differs from the confirmed stored form",
                        )
                    confirmed = arguments.get("confirmed_fields", [])
                    if not isinstance(confirmed, list) or any(
                        name not in confirmed for name in CreditFormFiller.required_fields
                    ):
                        return rejection(
                            tool_name, "UNCONFIRMED_FIELDS", "Confirmed field list is incomplete"
                        )
                elif arguments.get("subject_ref") != expected_row.subject_ref:
                    return rejection(
                        tool_name, "FORM_MISMATCH", "Report subject differs from the confirmed form"
                    )
            return None
        except (PermissionError, LookupError):
            return rejection(tool_name, "FORBIDDEN", "Session or run ownership verification failed")
        except (ValidationError, ValueError, TypeError, AttributeError):
            return rejection(tool_name, "INVALID_INPUT", "Invalid tool authorization or arguments")
        except SQLAlchemyError:
            return rejection(
                tool_name,
                "STORAGE_UNAVAILABLE",
                "Authorization database is unavailable",
                unavailable=True,
            )

    async def dispatch(self, tool_name, arguments, token, run_id, auth, expected_state=None):
        denied = self.validate(tool_name, arguments, token, run_id, auth, expected_state)
        if denied:
            return denied
        service = self.services.get(tool_name.split(".", 1)[0])
        if service is None:
            return rejection(
                tool_name, "SERVICE_UNAVAILABLE", "Service is not configured", unavailable=True
            )
        return await service.call(tool_name, arguments, AuthContext.model_validate(auth))


def build_server_parameters(settings, service):
    """Pass database configuration through environment; tokens stay in MCP messages."""
    from mcp import StdioServerParameters

    if service not in TOOL_ALLOWLIST:
        raise ValueError("Unknown MCP service")
    dsn = settings.database_dsn
    if dsn.startswith("sqlite") and (
        ":memory:" in dsn or dsn in ("sqlite://", "sqlite+pysqlite://")
    ):
        raise ValueError("MCP stdio requires a database shared between processes")
    source_dir = Path(__file__).resolve().parents[2]
    env = {
        "DATABASE_URL": dsn,
        "FINANCE_DATA_DIR": str(Path(settings.data_dir).resolve()),
        "LLM_ENABLED": "false",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(source_dir), os.getenv("PYTHONPATH", "")])),
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "finance_agents.mcp_services.server", "--service", service],
        env=env,
        cwd=str(source_dir.parent),
    )


def decode_tool_result(result):
    if result.isError:
        raise ValueError("MCP server rejected the protocol request")
    if result.structuredContent is not None:
        payload = result.structuredContent
        if "result" in payload and "status" not in payload:
            payload = payload["result"]
        return ToolResult.model_validate(payload)
    for block in result.content:
        if block.type == "text":
            return ToolResult.model_validate(json.loads(block.text))
    raise ValueError("MCP response did not contain a structured ToolResult")


class ScopedMCPClient:
    def __init__(self, settings, runtime_store, token, state, services):
        self.settings, self.token, self.state = settings, token, state
        self.dispatcher = ScopedDispatcher(runtime_store, services)

    async def call(self, tool_name, arguments, auth):
        denied = self.dispatcher.validate(
            tool_name, arguments, self.token, self.state.run_id, auth, self.state
        )
        if denied:
            return denied
        if self.settings.mcp_transport == "inprocess":
            return await self.dispatcher.dispatch(
                tool_name, arguments, self.token, self.state.run_id, auth, self.state
            )
        if self.settings.mcp_transport != "stdio":
            return rejection(tool_name, "INVALID_TRANSPORT", "Unsupported MCP transport")
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client

            async def invoke():
                parameters = build_server_parameters(self.settings, tool_name.split(".", 1)[0])
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(
                        read, write, read_timeout_seconds=timedelta(seconds=30)
                    ) as client:
                        await client.initialize()
                        response = await client.call_tool(
                            tool_name,
                            {
                                "arguments": arguments,
                                "token": self.token,
                                "run_id": self.state.run_id,
                                "auth": auth.model_dump(mode="json"),
                            },
                        )
                        return decode_tool_result(response)

            return await asyncio.wait_for(invoke(), timeout=45)
        except Exception:
            # Do not echo protocol envelopes, tokens or DSNs from nested SDK errors.
            return rejection(
                tool_name,
                "MCP_TRANSPORT_ERROR",
                "MCP stdio service could not complete the request",
                unavailable=True,
            )
