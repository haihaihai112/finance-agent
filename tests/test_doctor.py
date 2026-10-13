import importlib.util
import json
import os
from pathlib import Path


def load_doctor():
    path = Path(__file__).resolve().parents[1] / "scripts" / "doctor.py"
    spec = importlib.util.spec_from_file_location("doctor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_dependencies_block_and_configuration_is_redacted(tmp_path):
    doctor = load_doctor()
    (tmp_path / ".env").write_text(
        "DATABASE_URL=mysql+pymysql://user:SECRET@localhost/db\nDEEPSEEK_API_KEY=SECRET\n"
    )
    report = doctor.diagnose(tmp_path, {}, available=lambda _: False, port_used=lambda _: False)
    assert report["exit_code"] == 1
    rendered = json.dumps(report)
    assert "SECRET" not in rendered and "localhost" not in rendered
    assert any(row["name"] == "database" and "MySQL" in row["detail"] for row in report["checks"])


def test_optional_configuration_and_models_do_not_block(tmp_path):
    doctor = load_doctor()
    report = doctor.diagnose(
        tmp_path, {}, available=lambda _: True, port_used=lambda _: False, model_paths=[]
    )
    assert report["exit_code"] == 0
    statuses = {row["name"]: row["status"] for row in report["checks"]}
    assert statuses["deepseek"] == "unconfigured"
    assert statuses["ocr_models"] == "optional"
    assert not (tmp_path / "data").exists()


def test_environment_precedence_and_port_conflict(tmp_path):
    doctor = load_doctor()
    (tmp_path / ".env").write_text("MCP_TRANSPORT=invalid\n")
    report = doctor.diagnose(
        tmp_path,
        {"MCP_TRANSPORT": "stdio"},
        available=lambda _: True,
        port_used=lambda _: True,
        model_paths=[],
    )
    assert report["exit_code"] == 1
    statuses = {row["name"]: row["status"] for row in report["checks"]}
    assert statuses["mcp"] == "ok"
    assert statuses["port"] == "blocking"


def test_dotenv_interpolation_matches_non_overriding_load(tmp_path):
    doctor = load_doctor()
    (tmp_path / ".env").write_text(
        "OCR_LIMIT=2\nOCR_MAX_CONCURRENCY=${OCR_LIMIT}\n"
        "TRANSPORT=stdio\nMCP_TRANSPORT=${TRANSPORT}\n"
        "PRIVATE=SECRET\nDEEPSEEK_API_KEY=${PRIVATE}\n"
    )
    before = dict(os.environ)
    report = doctor.diagnose(
        tmp_path, {}, available=lambda _: True, port_used=lambda _: False, model_paths=[]
    )
    assert report["exit_code"] == 0
    assert dict(os.environ) == before
    assert "SECRET" not in json.dumps(report)


def test_environment_wins_during_interpolation_and_skips_bare_keys(tmp_path):
    doctor = load_doctor()
    (tmp_path / ".env").write_text(
        "TRANSPORT=invalid\nMCP_TRANSPORT=${TRANSPORT}\n"
        "OCR_LIMIT=invalid\nOCR_MAX_CONCURRENCY=${OCR_LIMIT}\nLLM_ENABLED\n"
    )
    environment = {"TRANSPORT": "inprocess", "OCR_LIMIT": "3"}
    before = dict(os.environ)
    report = doctor.diagnose(
        tmp_path, environment, available=lambda _: True, port_used=lambda _: False, model_paths=[]
    )
    assert report["exit_code"] == 0
    assert dict(os.environ) == before
    assert environment == {"TRANSPORT": "inprocess", "OCR_LIMIT": "3"}
    assert any(row["name"] == "mcp" and "inprocess" in row["detail"] for row in report["checks"])


def test_explicit_empty_transport_is_invalid(tmp_path):
    doctor = load_doctor()
    (tmp_path / ".env").write_text("MCP_TRANSPORT=\n")
    report = doctor.diagnose(
        tmp_path, {}, available=lambda _: True, port_used=lambda _: False, model_paths=[]
    )
    assert report["exit_code"] == 1
    assert any(row["name"] == "mcp" and row["status"] == "blocking" for row in report["checks"])


def test_repeated_file_assignment_preserves_sequential_interpolation(tmp_path):
    doctor = load_doctor()
    (tmp_path / ".env").write_text(
        "TRANSPORT=stdio\nMCP_TRANSPORT=${TRANSPORT}\nTRANSPORT=invalid\n"
    )
    report = doctor.diagnose(
        tmp_path, {}, available=lambda _: True, port_used=lambda _: False, model_paths=[]
    )
    assert report["exit_code"] == 0
