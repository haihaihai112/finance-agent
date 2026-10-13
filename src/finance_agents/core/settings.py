"""本地配置。密钥只读环境／.env，不通过 API 返回。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "runtime")
    database_url: str | None = None
    deepseek_api_key: str = field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_timeout: float = 30
    llm_enabled: bool = True
    mcp_transport: str = "stdio"
    max_upload_bytes: int = 20 * 1024 * 1024
    ocr_max_concurrency: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return cls(
            data_dir=Path(os.getenv("FINANCE_DATA_DIR", str(PROJECT_ROOT / "data" / "runtime"))),
            database_url=os.getenv("DATABASE_URL") or None,
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            llm_enabled=os.getenv("LLM_ENABLED", "true").lower() == "true",
            mcp_transport=os.getenv("MCP_TRANSPORT", "stdio"),
            ocr_max_concurrency=int(os.getenv("OCR_MAX_CONCURRENCY", "2")),
        )

    @property
    def database_dsn(self) -> str:
        return (
            self.database_url or f"sqlite+pysqlite:///{(self.data_dir / 'finance.db').as_posix()}"
        )
