from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str | None) -> None:
    """Load simple KEY=VALUE files without overriding explicit environment."""
    if not path:
        return
    p = Path(path)
    if not p.is_file():
        return
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        # systemd may have loaded a mode-0600 EnvironmentFile before dropping privileges.
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    web_addr: str
    tdx_hosts: tuple[str, ...]
    tdx_timeout: float
    poll_interval: float
    markets: tuple[int, ...]
    page_size: int
    max_pages: int
    overlap_rows: int
    stock_name_db: str
    custom_block_mysql_config: str
    static_dir: str

    @classmethod
    def from_env(cls) -> "Settings":
        hosts = os.getenv("STOCK_UNUSUAL_TDX_HOSTS", "").strip()
        parsed_hosts = tuple(x.strip() for x in hosts.split(",") if x.strip())
        market_value = os.getenv("STOCK_UNUSUAL_MARKETS", os.getenv("GOTDX_UNUSUAL_MARKETS", "0,1,2"))
        markets = tuple(int(x.strip()) for x in market_value.split(",") if x.strip())
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            redis_url=os.getenv("REDIS_URL", "") or _redis_url_from_legacy_env(),
            web_addr=os.getenv("STOCK_UNUSUAL_WEB_ADDR", "0.0.0.0:8898"),
            tdx_hosts=parsed_hosts,
            tdx_timeout=float(os.getenv("STOCK_UNUSUAL_TDX_TIMEOUT", "6")),
            poll_interval=_duration_seconds(os.getenv("STOCK_UNUSUAL_POLL_INTERVAL_SECONDS", os.getenv("GOTDX_UNUSUAL_POLL_INTERVAL", "1"))),
            markets=markets or (0, 1, 2),
            page_size=int(os.getenv("STOCK_UNUSUAL_PAGE_SIZE", "600")),
            max_pages=int(os.getenv("STOCK_UNUSUAL_MAX_PAGES", "128")),
            overlap_rows=int(os.getenv("STOCK_UNUSUAL_OVERLAP_ROWS", "600")),
            stock_name_db=os.getenv("STOCK_NAME_DB", "/opt/stock-unusual-python/data/stocknames.sqlite3"),
            custom_block_mysql_config=os.getenv("CUSTOM_BLOCK_MYSQL_CONFIG", "/opt/stock-unusual-python/config/mysql.ini"),
            static_dir=os.getenv("STOCK_UNUSUAL_STATIC_DIR", str(Path(__file__).parent.parent / "static")),
        )


def _duration_seconds(value: str) -> float:
    value = value.strip().lower()
    try:
        if value.endswith("ms"): return float(value[:-2]) / 1000
        if value.endswith("s"): return float(value[:-1])
        if value.endswith("m"): return float(value[:-1]) * 60
        return float(value)
    except ValueError:
        return 1.0


def _redis_url_from_legacy_env() -> str:
    addr = os.getenv("REDIS_ADDR", "").strip()
    if not addr:
        addr = f"{os.getenv('QMT_REDIS_HOST', '127.0.0.1')}:{os.getenv('QMT_REDIS_PORT', '6379')}"
    username = os.getenv("REDIS_USERNAME", "") or os.getenv("QMT_REDIS_USERNAME", "")
    password = os.getenv("REDIS_PASSWORD", "") or os.getenv("QMT_REDIS_PASSWORD", "")
    db = os.getenv("REDIS_DB", "") or os.getenv("QMT_REDIS_DB", "0")
    auth = ""
    if username or password:
        from urllib.parse import quote
        auth = f"{quote(username)}:{quote(password)}@" if username else f":{quote(password)}@"
    return f"redis://{auth}{addr}/{db}"
