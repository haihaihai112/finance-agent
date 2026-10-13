"""Offline, read-only readiness checks; never instantiate application services."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys

CORE_PACKAGES = (
    "finance_agents",
    "fastapi",
    "pydantic",
    "uvicorn",
    "httpx",
    "dotenv",
    "multipart",
    "sqlalchemy",
    "pymysql",
    "cryptography",
    "langgraph",
    "mcp",
    "fitz",
    "PIL",
    "requests",
    "bs4",
)
OCR_MODELS = (
    "PP-OCRv6_det_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "PP-OCRv6_rec_small.onnx",
)


def available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def port_used(port):
    # Bind only to loopback, without contacting a service or changing its state.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True


def local_models():
    try:
        spec = importlib.util.find_spec("rapidocr")
        if spec and spec.origin:
            directory = Path(spec.origin).parent / "models"
            return [directory / name for name in OCR_MODELS]
    except (ImportError, ValueError, AttributeError):
        pass
    return []


def diagnose(
    root, environment=None, *, available=available, port_used=port_used, model_paths=None, port=8000
):
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    add("python", "ok" if sys.version_info >= (3, 11) else "blocking", "需要 Python >= 3.11")
    for name in CORE_PACKAGES:
        add(
            name,
            "ok" if available(name) else "blocking",
            "核心依赖已定位" if available(name) else '缺少核心依赖；请安装 pip install -e ".[dev]"',
        )
    env_file = root / ".env"
    config = {}
    if env_file.is_file():
        try:
            # Match application dotenv parsing, but do not mutate the process environment.
            from dotenv import dotenv_values

            config = dict(dotenv_values(env_file, interpolate=False))
            add("env", "ok", ".env 存在；不显示内容")
        except Exception:
            add("env", "blocking", ".env 无法读取；错误详情隐藏以避免泄露配置")
    else:
        add("env", "optional", ".env 不存在；默认 SQLite / 规则模式可运行")
    config.update(os.environ if environment is None else environment)
    database = config.get("DATABASE_URL") or ""
    mode = (
        "MySQL"
        if database.startswith("mysql")
        else "SQLite"
        if not database or database.startswith("sqlite")
        else "unsupported"
    )
    add(
        "database",
        "blocking" if mode == "unsupported" else "ok",
        f"{mode} 配置模式；未连接、未写入数据库",
    )
    llm = str(config.get("LLM_ENABLED", "true")).lower() == "true"
    configured = llm and bool(config.get("DEEPSEEK_API_KEY"))
    add(
        "deepseek",
        "optional" if configured else "unconfigured",
        "已配置但未验证真实调用" if configured else "使用规则降级；真实调用未验证",
    )
    transport = config.get("MCP_TRANSPORT") or "stdio"
    add(
        "mcp",
        "ok" if transport in {"stdio", "inprocess"} else "blocking",
        "stdio 协议模式"
        if transport == "stdio"
        else "inprocess 调试模式"
        if transport == "inprocess"
        else "无效传输模式",
    )
    try:
        valid_concurrency = int(config.get("OCR_MAX_CONCURRENCY", "2")) > 0
    except (ValueError, TypeError):
        valid_concurrency = False
    add("ocr_concurrency", "ok" if valid_concurrency else "blocking", "OCR 并发须为正整数")
    paths = local_models() if model_paths is None else model_paths
    ready = (
        available("rapidocr")
        and available("onnxruntime")
        and len(paths) == 3
        and all(path.is_file() and path.stat().st_size > 0 for path in paths)
    )
    add(
        "ocr_models",
        "ok" if ready else "optional",
        "默认三份 ONNX 文件本地存在；未验证加载或推理"
        if ready
        else "图片/扫描 OCR 本地模型或依赖未齐备；不自动下载，文本 PDF 可单独使用",
    )
    add("port", "blocking" if port_used(port) else "ok", f"127.0.0.1:{port} 本地绑定检查")
    return {
        "checks": checks,
        "exit_code": int(any(c["status"] == "blocking" for c in checks)),
        "scope": "离线只读；未连接外部服务，未运行OCR，未写数据库",
    }


def main():
    # Windows redirected stdout otherwise uses the active legacy code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    report = diagnose(Path(__file__).resolve().parents[1], port=args.port)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for row in report["checks"]:
            print(f"[{row['status']}] {row['name']}: {row['detail']}")
        print(report["scope"])
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
