"""Finite synthetic DeepSeek acceptance. Never print keys, URLs or raw errors."""

import argparse
import asyncio
import json
from pathlib import Path
import time

import httpx

from finance_agents.agents.intent_router import IntentRouterAgent
from finance_agents.contracts.requests import QueryRequest
from finance_agents.contracts.results import IntentDecision
from finance_agents.core.settings import Settings
from finance_agents.llm.deepseek import DeepSeekClient


async def controlled_fallbacks():
    outcomes = {}
    for name, error_type in (("timeout", httpx.ReadTimeout), ("network_error", httpx.ConnectError)):

        def fail(request, error_type=error_type):
            raise error_type("synthetic transport failure", request=request)

        client = DeepSeekClient("synthetic-key", transport=httpx.MockTransport(fail))
        decision = await IntentRouterAgent(client).classify(
            QueryRequest(session_id="synthetic", request_id=name, message="我想申请贷款")
        )
        outcomes[name] = {
            "controlled_test": True,
            "passed": decision.source == "rule" and bool(decision.warnings),
        }
    return outcomes


async def verify(settings, calls, timeout):
    report = {
        "key_configured": bool(settings.deepseek_api_key),
        "synthetic_only": True,
        "live_requests": [],
        "model_available": None,
        "cost": None,
        "cost_status": "unknown_no_verified_unit_price",
        "controlled_fallbacks": await controlled_fallbacks(),
    }
    if not settings.deepseek_api_key:
        report["live_status"] = "unverified_missing_key"
        return report
    report["live_status"] = "attempted"
    messages = [
        "我想申请贷款，这是合成验收文本，没有身份、金额或金融材料。",
        "居民综合所得个税政策，这是合成验收文本。",
    ]
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for index in range(calls):
            started = time.perf_counter()
            item = {
                "index": index + 1,
                "status": "failed",
                "usage": None,
                "model_available": None,
                "endpoint_structured_response_valid": False,
            }
            try:
                response = await client.post(
                    settings.deepseek_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + settings.deepseek_api_key},
                    json={
                        "model": settings.deepseek_model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "只输出意图分类JSON："
                                + json.dumps(
                                    IntentDecision.model_json_schema(), ensure_ascii=False
                                ),
                            },
                            {"role": "user", "content": messages[index]},
                        ],
                        "response_format": {"type": "json_object"},
                        "stream": False,
                        "max_tokens": 128,
                    },
                )
                item["http_status"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                decision = IntentDecision.model_validate_json(
                    payload["choices"][0]["message"]["content"]
                )
                usage = payload.get("usage")
                if usage is None:
                    usage = {}
                if not isinstance(usage, dict):
                    raise ValueError("Usage metadata must be an object")
                item["usage"] = {
                    key: usage[key]
                    for key in (
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                        "prompt_cache_hit_tokens",
                        "prompt_cache_miss_tokens",
                    )
                    if type(usage.get(key)) is int
                }
                # Do not emit arbitrary provider strings or echoed response content.
                item["endpoint_structured_response_valid"] = True
                item["returned_model_matches_config"] = (
                    payload.get("model") == settings.deepseek_model
                )
                if item["returned_model_matches_config"]:
                    item["model_available"] = True
                    item["status"] = "verified_configured_model"
                elif not payload.get("model"):
                    item["status"] = "structured_response_model_missing"
                else:
                    item["status"] = "structured_response_model_mismatch"
                item["intent"] = decision.intent.value if decision.intent else None
            except httpx.TimeoutException:
                item["status"] = "timeout"
            except httpx.HTTPStatusError:
                item["status"] = "http_error"
                # 404 alone does not prove a particular model is unavailable.
            except httpx.HTTPError:
                item["status"] = "network_error"
            except (ValueError, KeyError, IndexError, TypeError):
                item["status"] = "invalid_structured_response"
            item["elapsed_seconds"] = round(time.perf_counter() - started, 4)
            report["live_requests"].append(item)
    report["model_available"] = (
        True if any(r["model_available"] for r in report["live_requests"]) else None
    )
    report["live_status"] = (
        "verified_configured_model"
        if all(r["status"] == "verified_configured_model" for r in report["live_requests"])
        else "unverified_configured_model"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, choices=(1, 2), default=1)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("timeout must be within (0, 60]")
    report = asyncio.run(verify(Settings.from_env(), args.calls, args.timeout))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return (
        0
        if all(r["passed"] for r in report["controlled_fallbacks"].values())
        and all(r["status"] == "verified_configured_model" for r in report["live_requests"])
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
