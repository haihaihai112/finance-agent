"""Query current user's versioned policy history and provided clauses only."""

from datetime import date, datetime, timezone
from typing import Any

from finance_agents.contracts.common import AuthContext, DataMode, EvidenceRef, ResultStatus
from finance_agents.contracts.results import ToolError, ToolResult
from finance_agents.mcp_services.base import MCPServiceSkeleton


class ClaimsMCPService(MCPServiceSkeleton):
    name = "claims"
    tools = ("claims.lookup_policies", "claims.match_clauses", "claims.check_conditions")

    def __init__(self, store=None):
        self.store = store

    def _failure(self, status, message, code="UNAVAILABLE", data_mode=DataMode.REAL):
        return ToolResult(
            status=status, data_mode=data_mode, error=ToolError(code=code, message=message)
        )

    async def call(self, tool_name: str, arguments: dict[str, Any], auth: AuthContext):
        if tool_name not in self.tools:
            return self._failure(ResultStatus.FAILED, "Unknown claims tool", "UNKNOWN_TOOL")
        if arguments.get("user_id", auth.user_id) != auth.user_id:
            return self._failure(
                ResultStatus.FAILED, "Cross-user policy lookup denied", "FORBIDDEN"
            )
        if self.store is None:
            return self._failure(ResultStatus.UNAVAILABLE, "Policy repository is not configured")
        policies = self.store.lookup_policies(
            auth.user_id, arguments.get("policy_id"), arguments.get("policy_version")
        )
        if not policies:
            return self._failure(
                ResultStatus.UNAVAILABLE, "No policy history is available for the current user"
            )
        mode = (
            DataMode.MOCK
            if any(p["data_mode"] == "mock" for p in policies)
            else DataMode.REDACTED
            if any(p["data_mode"] == "redacted" for p in policies)
            else DataMode.REAL
        )
        evidence = [
            EvidenceRef(
                source_id=f"policy:{p['policy_id']}:{p['policy_version']}",
                source_title=p.get("source_title", "Provided versioned policy record"),
                source_uri=p.get("source_uri"),
                quote=p.get("clauses", [{}])[0].get("text") if p.get("clauses") else None,
                retrieved_at=datetime.now(timezone.utc),
            )
            for p in policies
        ]
        warnings = list(dict.fromkeys(w for p in policies for w in p.get("warnings", [])))
        if tool_name == "claims.lookup_policies":
            return ToolResult(
                status=ResultStatus.SUCCESS,
                data_mode=mode,
                data={"policies": policies},
                evidence=evidence,
                warnings=warnings,
            )
        required = [
            k for k in ("policy_id", "policy_version", "incident_type") if not arguments.get(k)
        ]
        if required or len(policies) != 1:
            return ToolResult(
                status=ResultStatus.NEEDS_INPUT,
                data_mode=mode,
                data={"missing_fields": required or ["policy_version"]},
                warnings=["Select an exact policy and terms version before checking conditions"],
            )
        policy = policies[0]
        clauses = policy.get("clauses")
        if not clauses:
            return self._failure(
                ResultStatus.UNAVAILABLE,
                "Policy clauses are missing; no coverage judgment is possible",
                data_mode=mode,
            )
        matched = [c for c in clauses if arguments["incident_type"] in c.get("incident_types", [])]
        if tool_name == "claims.match_clauses":
            return ToolResult(
                status=ResultStatus.SUCCESS,
                data_mode=mode,
                data={"matched_clauses": matched, "policy_version": policy["policy_version"]},
                evidence=evidence,
                warnings=warnings,
            )
        if not arguments.get("incident_date"):
            return ToolResult(
                status=ResultStatus.NEEDS_INPUT,
                data_mode=mode,
                data={"missing_fields": ["incident_date"]},
            )
        try:
            incident = date.fromisoformat(str(arguments["incident_date"]))
        except ValueError:
            return self._failure(
                ResultStatus.FAILED, "incident_date must be ISO YYYY-MM-DD", "INVALID_INPUT", mode
            )
        covered_date = (
            date.fromisoformat(policy["effective_from"])
            <= incident
            <= date.fromisoformat(policy["effective_to"])
        )
        exclusions = [c for c in matched if c.get("excluded") is True]
        preliminary = (
            "outside_coverage_dates"
            if not covered_date
            else "excluded_by_provided_clause"
            if exclusions
            else "potentially_covered"
            if matched
            else "requires_manual_clause_review"
        )
        return ToolResult(
            status=ResultStatus.SUCCESS,
            data_mode=mode,
            data={
                "preliminary_result": preliminary,
                "policy_id": policy["policy_id"],
                "policy_version": policy["policy_version"],
                "within_effective_dates": covered_date,
                "matched_clauses": matched,
                "incident_date": incident.isoformat(),
            },
            evidence=evidence,
            warnings=warnings
            + [
                "Preliminary screening only; documents, exclusions, deductibles and insurer review are required. No payout has been calculated."
            ],
        )
