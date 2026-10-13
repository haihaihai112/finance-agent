"""Synthetic transport checks; never count these as live-provider acceptance."""

import importlib.util
import json
from pathlib import Path
import sys

import httpx
import pytest

from finance_agents.core.settings import Settings


def load_verifier():
    spec = importlib.util.spec_from_file_location(
        "synthetic_deepseek_verifier", Path(__file__).parents[1] / "scripts" / "verify_deepseek.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "returned_model,status,available,exit_code",
    [
        ("intended-model", "verified_configured_model", True, 0),
        ("different-model", "structured_response_model_mismatch", None, 1),
        (None, "structured_response_model_missing", None, 1),
    ],
)
def test_synthetic_model_identity_controls_availability_and_exit(
    monkeypatch, capsys, returned_model, status, available, exit_code
):
    verifier = load_verifier()
    settings = Settings(deepseek_api_key="test-secret-never-live", deepseek_model="intended-model")
    requests = []

    def respond(request):
        requests.append(request)
        payload = {
            "choices": [{"message": {"content": '{"intent":"loan","confidence":0.95}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
        if returned_model is not None:
            payload["model"] = returned_model
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient

    def synthetic_client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(respond))
        return real_client(**kwargs)

    monkeypatch.setattr(verifier.httpx, "AsyncClient", synthetic_client)
    monkeypatch.setattr(verifier.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["verify_deepseek.py", "--calls", "1"])
    assert verifier.main() == exit_code
    output = capsys.readouterr().out
    report = json.loads(output)
    assert len(requests) == 1
    assert report["live_requests"][0]["status"] == status
    assert report["live_requests"][0]["endpoint_structured_response_valid"] is True
    assert report["live_requests"][0]["model_available"] is available
    assert report["model_available"] is available
    assert report["live_requests"][0]["usage"]["total_tokens"] == 14
    assert settings.deepseek_api_key not in output


def test_missing_key_remains_unverified_without_live_request(monkeypatch, capsys):
    verifier = load_verifier()
    settings = Settings(deepseek_api_key="")
    live_requests = []
    real_client = httpx.AsyncClient

    def reject_live(request):
        live_requests.append(request)
        pytest.fail("missing key must not trigger a live request")

    def synthetic_client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(reject_live))
        return real_client(**kwargs)

    monkeypatch.setattr(verifier.httpx, "AsyncClient", synthetic_client)
    monkeypatch.setattr(verifier.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["verify_deepseek.py", "--calls", "2"])
    assert verifier.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["live_status"] == "unverified_missing_key"
    assert report["model_available"] is None
    assert report["live_requests"] == []
    assert not live_requests
    assert all(row["passed"] for row in report["controlled_fallbacks"].values())


@pytest.mark.parametrize("usage", ["malformed", ["total_tokens"], False, {"total_tokens": True}])
def test_malformed_usage_preserves_content_facts_and_fails_acceptance(monkeypatch, capsys, usage):
    verifier = load_verifier()
    real_client = httpx.AsyncClient

    def respond(request):
        return httpx.Response(
            200,
            json={
                "model": "intended-model",
                "choices": [{"message": {"content": '{"intent":"loan","confidence":0.95}'}}],
                "usage": usage,
            },
        )

    def synthetic_client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(respond))
        return real_client(**kwargs)

    monkeypatch.setattr(verifier.httpx, "AsyncClient", synthetic_client)
    settings = Settings(deepseek_api_key="test-only", deepseek_model="intended-model")
    monkeypatch.setattr(verifier.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["verify_deepseek.py", "--calls", "1"])
    assert verifier.main() == (0 if isinstance(usage, dict) else 1)
    report = json.loads(capsys.readouterr().out)
    assert report["live_requests"][0]["endpoint_structured_response_valid"] is True
    assert report["live_requests"][0]["intent"] == "loan"
    assert report["live_requests"][0]["returned_model_matches_config"] is True
    assert report["live_requests"][0]["model_available"] is True
    assert report["model_available"] is True
    assert report["cost"] is None
    if isinstance(usage, dict):
        assert report["live_requests"][0]["status"] == "verified_configured_model"
        assert "total_tokens" not in report["live_requests"][0]["usage"]
        return
    assert report["live_requests"][0]["status"] == "invalid_usage_metadata"
    assert report["live_requests"][0]["usage"] is None
    assert report["live_status"] == "acceptance_failed"
