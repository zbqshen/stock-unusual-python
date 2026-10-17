from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path

from .config import Settings, load_env_file
from .tdx import TDXClient


def market_prefix(market: int) -> str:
    return {1: "sh", 2: "bj"}.get(market, "sz")


def write_snapshot(output: str, names: dict[str, str]) -> None:
    path = Path(output); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    os.close(fd)
    try:
        with sqlite3.connect(tmp_name) as db:
            db.execute("CREATE TABLE stockname (code TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.executemany("INSERT INTO stockname(code,name,updated_at) VALUES(?,?,datetime('now'))", names.items())
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise RuntimeError("snapshot integrity check failed")
        os.chmod(tmp_name, 0o640)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name): os.unlink(tmp_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh stock-name SQLite snapshot from TDX")
    parser.add_argument("--env", default="/etc/stock-unusual-python.env")
    parser.add_argument("--output", default="")
    args = parser.parse_args(); load_env_file(args.env)
    settings = Settings.from_env(); output = args.output or settings.stock_name_db
    client = TDXClient(settings.tdx_hosts, max(settings.tdx_timeout, 8)); client.connect()
    names: dict[str, str] = {}
    try:
        for market in (0, 1, 2):
            count = client.get_security_count(market)
            for start in range(0, count, 1600):
                rows = client.get_security_list(market, start, 1600)
                for row in rows:
                    if len(row.code.strip()) == 6 and row.name.strip(): names[market_prefix(market) + row.code.strip()] = row.name.strip()
                if len(rows) < 1600: break
    finally: client.close()
    if len(names) < 4000: raise RuntimeError(f"refusing incomplete stock-name snapshot: {len(names)} rows")
    write_snapshot(output, names)
    print(f"stock-name snapshot updated rows={len(names)} path={output}")


if __name__ == "__main__": main()
