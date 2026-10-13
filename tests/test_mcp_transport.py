import asyncio

import pytest

from finance_agents.contracts.common import AuthContext, Money
from finance_agents.contracts.profiles import CreditForm
from finance_agents.core.settings import Settings
from finance_agents.mcp_services.credit import CreditMCPService
from finance_agents.storage.database import create_database_engine
from finance_agents.storage.runtime_store import RuntimeStore
from finance_agents.tools.form_filler import CreditFormFiller
from finance_agents.workflow.state import SessionState


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'runtime.db').as_posix()}",
        llm_enabled=False,
    )
    store = RuntimeStore(create_database_engine(settings.database_dsn))
    session = store.create_session()
    form = CreditForm(
        subject_ref="demo-applicant",
        requested_amount=Money(amount="12000", currency="CNY"),
        loan_term_months=12,
        monthly_income=Money(amount="8000", currency="CNY"),
        monthly_debt_payment=Money(amount="1000", currency="CNY"),
        employment_status="employed",
        confirmed_fields=list(CreditFormFiller.required_fields),
    )
    state = SessionState(
        run_id="transport-run",
        user_id=session["user_id"],
        session_id=session["session_id"],
        credit_form=form,
    )
    store.create_run(state)
    reference = store.grant_consent(state.user_id, state.run_id)
    state.consent_ref = form.consent_ref = reference
    store.save_run(state, state.state_version)
    auth = AuthContext(user_id=state.user_id, session_id=state.session_id, consent_ref=reference)
    row = CreditFormFiller().to_model_input(form, "demo-v1")
    args = {
        "model_input": row.model_dump(mode="json"),
        "consent_ref": reference,
        "confirmed_fields": form.confirmed_fields,
    }
    return settings, store, session, state, auth, args


@pytest.mark.parametrize("transport", ["inprocess", "stdio"])
def test_scoped_client_runs_real_service_and_rejects_forgery(runtime, transport):
    from finance_agents.mcp_services.transport import ScopedMCPClient

    settings, store, session, state, auth, args = runtime
    settings.mcp_transport = transport
    client = ScopedMCPClient(
        settings, store, session["token"], state, {"credit": CreditMCPService()}
    )
    result = asyncio.run(client.call("credit.evaluate_mock", args, auth))
    assert result.status == "success" and result.data_mode == "mock"
    assert result.data["table_row"]["requested_amount"] == "12000"
    unknown = asyncio.run(client.call("credit.execute_sql", {}, auth))
    assert unknown.status == "failed" and unknown.error.code == "UNKNOWN_TOOL"
    forged = auth.model_copy(update={"consent_ref": "forged-reference"})
    denied = asyncio.run(client.call("credit.evaluate_mock", args, forged))
    assert denied.status == "failed" and denied.error.code == "CONSENT_REQUIRED"


def test_client_rejects_other_users_session(runtime):
    from finance_agents.mcp_services.transport import ScopedMCPClient

    settings, store, _, state, auth, args = runtime
    other = store.create_session()
    client = ScopedMCPClient(settings, store, other["token"], state, {"credit": CreditMCPService()})
    result = asyncio.run(client.call("credit.evaluate_mock", args, auth))
    assert result.status == "failed" and result.error.code == "FORBIDDEN"


def test_stdio_server_independently_rechecks_consent_and_registered_allowlist(runtime):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from finance_agents.mcp_services.transport import build_server_parameters, decode_tool_result

    settings, store, session, state, auth, args = runtime

    async def exercise():
        async with stdio_client(build_server_parameters(settings, "credit")) as (read, write):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "finance-credit"
                listed = await client.list_tools()
                assert {tool.name for tool in listed.tools} == set(CreditMCPService.tools)
                envelope = {
                    "arguments": args,
                    "token": session["token"],
                    "run_id": state.run_id,
                    "auth": auth.model_dump(mode="json"),
                }
                accepted = decode_tool_result(
                    await client.call_tool("credit.evaluate_mock", envelope)
                )
                assert accepted.status == "success"
                forged_envelope = {
                    **envelope,
                    "auth": {**envelope["auth"], "consent_ref": "forged"},
                }
                forged = decode_tool_result(
                    await client.call_tool("credit.evaluate_mock", forged_envelope)
                )
                assert forged.status == "failed" and forged.error.code == "CONSENT_REQUIRED"
                altered = {
                    **args,
                    "model_input": {**args["model_input"], "monthly_income": "99999"},
                }
                tampered = decode_tool_result(
                    await client.call_tool(
                        "credit.evaluate_mock", {**envelope, "arguments": altered}
                    )
                )
                assert tampered.status == "failed" and tampered.error.code == "FORM_MISMATCH"
                store.revoke_consent(auth.consent_ref, state.user_id)
                revoked = decode_tool_result(
                    await client.call_tool("credit.evaluate_mock", envelope)
                )
                assert revoked.status == "failed" and revoked.error.code == "CONSENT_REQUIRED"
                invalid = await client.call_tool("credit.execute_sql", envelope)
                assert invalid.isError

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "service_name,tool_name,arguments",
    [
        ("claims", "claims.lookup_policies", {}),
        ("market", "market.query_prices", {"instrument_ids": ["000001"]}),
        ("tax", "tax.search_rules", {"query": "综合所得", "jurisdiction": "CN", "tax_year": 2026}),
    ],
)
def test_other_services_register_and_execute_over_stdio(
    runtime, service_name, tool_name, arguments
):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from finance_agents.mcp_services.transport import (
        TOOL_ALLOWLIST,
        build_server_parameters,
        decode_tool_result,
    )

    settings, _, session, state, auth, _ = runtime

    async def exercise():
        async with stdio_client(build_server_parameters(settings, service_name)) as (read, write):
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == f"finance-{service_name}"
                listed = await client.list_tools()
                assert {tool.name for tool in listed.tools} == set(TOOL_ALLOWLIST[service_name])
                result = decode_tool_result(
                    await client.call_tool(
                        tool_name,
                        {
                            "arguments": arguments,
                            "token": session["token"],
                            "run_id": state.run_id,
                            "auth": auth.model_dump(mode="json"),
                        },
                    )
                )
                assert result.status == "unavailable"
                forbidden = decode_tool_result(
                    await client.call_tool(
                        tool_name,
                        {
                            "arguments": arguments,
                            "token": "forged-token",
                            "run_id": state.run_id,
                            "auth": auth.model_dump(mode="json"),
                        },
                    )
                )
                assert forbidden.status == "failed" and forbidden.error.code == "FORBIDDEN"

    asyncio.run(exercise())
