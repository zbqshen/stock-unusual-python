from __future__ import annotations

import configparser
import re
import time
from dataclasses import dataclass
from threading import Lock

import pymysql


@dataclass(frozen=True)
class BlockOption:
    name: str
    stock_count: int

@dataclass(frozen=True)
class Universe:
    codes: tuple[str, ...]
    block_count: int
    stock_count: int


class CustomBlocks:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self._lock = Lock()
        self._options_cache: tuple[float, list[BlockOption], int] | None = None
        self._universe_cache: dict[str, tuple[float, Universe]] = {}

    def _connect(self):
        parser = configparser.ConfigParser(); parser.read(self.config_path, encoding="utf-8")
        if not parser.has_section("mysql"): raise ValueError("mysql.ini 缺少 [mysql]")
        cfg = parser["mysql"]
        table = cfg.get("table", "").strip()
        if not all(cfg.get(k, "").strip() for k in ("host", "user", "database")) or not re.fullmatch(r"[A-Za-z0-9_]+", table):
            raise ValueError("mysql.ini 缺少 host、user、database 或合法 table")
        return pymysql.connect(host=cfg["host"].strip(), port=int(cfg.get("port", "3306")), user=cfg["user"].strip(), password=cfg.get("password", ""), database=cfg["database"].strip(), charset=cfg.get("charset", "utf8mb4"), connect_timeout=8, read_timeout=12, write_timeout=12), table

    def options(self) -> tuple[list[BlockOption], int]:
        now = time.monotonic()
        with self._lock:
            if self._options_cache and self._options_cache[0] > now: return self._options_cache[1:]
        db, table = self._connect()
        try:
            with db.cursor() as cur:
                cur.execute(f"SELECT block_name, COUNT(DISTINCT code) FROM `{table}` GROUP BY block_name ORDER BY block_name")
                items = [BlockOption(str(row[0]), int(row[1])) for row in cur.fetchall()]
                cur.execute(f"SELECT COUNT(DISTINCT code) FROM `{table}`")
                total = int(cur.fetchone()[0])
        finally: db.close()
        with self._lock: self._options_cache = (now + 30, items, total)
        return items, total

    def universe(self, mode: str, block_name: str = "", block_text: str = "", block_names: list[str] | None = None) -> Universe:
        selected_names = tuple(sorted({str(name).strip() for name in (block_names or []) if str(name).strip()}))
        selected_key = "\x1f".join(selected_names)
        key = f"{mode}\0{block_name}\0{block_text}\0{selected_key}"; now = time.monotonic()
        with self._lock:
            cached = self._universe_cache.get(key)
            if cached and cached[0] > now: return cached[1]
        db, table = self._connect()
        try:
            where, args = "", []
            if mode == "exact": where, args = " WHERE block_name=%s", [block_name]
            elif mode == "contains": where, args = " WHERE LOCATE(%s, block_name)>0", [block_text]
            elif mode == "selected":
                if selected_names:
                    placeholders = ",".join(["%s"] * len(selected_names))
                    where, args = f" WHERE block_name IN ({placeholders})", list(selected_names)
                else:
                    where = " WHERE 1=0"
            elif mode != "all": raise ValueError("不支持的板块筛选模式")
            with db.cursor() as cur:
                cur.execute(f"SELECT COUNT(DISTINCT block_name) FROM `{table}`{where}", args); block_count = int(cur.fetchone()[0])
                cur.execute(f"SELECT DISTINCT code FROM `{table}`{where}", args)
                codes = tuple(sorted({str(row[0]).strip() for row in cur.fetchall() if str(row[0]).strip().isdigit() and len(str(row[0]).strip()) == 6}))
        finally: db.close()
        result = Universe(codes, block_count, len(codes))
        with self._lock: self._universe_cache[key] = (now + 15, result)
        return result
