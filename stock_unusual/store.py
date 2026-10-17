from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
import redis

from .tdx import Unusual

REDIS_PREFIX = "stock-unusual:events"


def shanghai_today() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def is_trading_time(now: datetime | None = None) -> bool:
    from zoneinfo import ZoneInfo
    value = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Shanghai"))
    if value.weekday() >= 5: return False
    minutes = value.hour * 60 + value.minute
    return 9 * 60 + 15 <= minutes <= 11 * 60 + 30 or 13 * 60 <= minutes <= 15 * 60 + 5


def normalize_time(value: str) -> str:
    value = (value or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try: return datetime.strptime(value, fmt).strftime("%H:%M:%S")
        except ValueError: pass
    return "00:00:00"


def is_a_stock(market: int, code: str) -> bool:
    code = code.strip()
    if len(code) != 6 or not code.isdigit(): return False
    if market == 0: return code.startswith(("0", "30"))
    if market == 1: return code.startswith("6")
    if market == 2: return code.startswith(("82", "83", "87", "88", "92"))
    return False


@dataclass
class Event:
    trade_date: str
    market: int
    index: int
    code: str
    name: str = ""
    time: str = "00:00:00"
    desc: str = ""
    value: str = ""
    unusual_type: int = 0
    source_start: int = 0
    seen_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketState:
    trade_date: str
    market: int
    max_index: int = 0
    row_count: int = 0
    next_start: int = 0


class Store:
    def __init__(self, database_url: str, redis_url: str = ""):
        if not database_url: raise ValueError("DATABASE_URL 未配置")
        self.database_url = database_url
        self.redis = redis.from_url(redis_url, decode_responses=True) if redis_url else None

    def connection(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def ping(self) -> None:
        with self.connection() as conn:
            conn.execute("SELECT 1")
        if self.redis: self.redis.ping()

    def ensure_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS stock_unusual_events (
                trade_date date NOT NULL, market smallint NOT NULL, event_index integer NOT NULL,
                code text NOT NULL, event_time time NOT NULL, desc_text text NOT NULL DEFAULT '',
                value_text text NOT NULL DEFAULT '', unusual_type smallint NOT NULL,
                source_start integer NOT NULL DEFAULT 0, seen_at timestamptz NOT NULL DEFAULT now(),
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (trade_date, market, event_index))""",
            "CREATE INDEX IF NOT EXISTS idx_stock_unusual_events_date_time ON stock_unusual_events (trade_date, event_time, market, event_index)",
            "CREATE INDEX IF NOT EXISTS idx_stock_unusual_events_code_date ON stock_unusual_events (code, trade_date, event_time)",
            """CREATE TABLE IF NOT EXISTS stock_unusual_market_state (
                trade_date date NOT NULL, market smallint NOT NULL, max_index integer NOT NULL DEFAULT 0,
                row_count integer NOT NULL DEFAULT 0, next_start integer NOT NULL DEFAULT 0,
                last_scan_at timestamptz, last_full_scan_at timestamptz, last_error text NOT NULL DEFAULT '',
                updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (trade_date, market))""",
            """CREATE TABLE IF NOT EXISTS stock_unusual_gaps (
                id bigserial PRIMARY KEY, trade_date date NOT NULL, market smallint NOT NULL,
                expected_index integer NOT NULL, actual_index integer NOT NULL, missing_count integer NOT NULL,
                detected_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz,
                UNIQUE (trade_date, market, expected_index, actual_index))""",
        ]
        with self.connection() as conn:
            for sql in statements: conn.execute(sql)

    def latest_trade_date(self) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT trade_date::text AS value FROM stock_unusual_events ORDER BY trade_date DESC LIMIT 1").fetchone()
        return row["value"] if row else None

    def get_state(self, trade_date: str, market: int) -> tuple[MarketState, bool]:
        with self.connection() as conn:
            row = conn.execute("SELECT max_index,row_count,next_start FROM stock_unusual_market_state WHERE trade_date=%s AND market=%s", (trade_date, market)).fetchone()
        if not row: return MarketState(trade_date, market), False
        return MarketState(trade_date, market, row["max_index"], row["row_count"], row["next_start"]), True

    def upsert_state(self, state: MarketState, full: bool, last_error: str = "") -> None:
        with self.connection() as conn:
            if last_error:
                conn.execute("""INSERT INTO stock_unusual_market_state (trade_date,market,last_error,updated_at) VALUES (%s,%s,%s,now())
                    ON CONFLICT (trade_date,market) DO UPDATE SET last_error=%s,updated_at=now()""", (state.trade_date, state.market, last_error, last_error))
            else:
                conn.execute("""INSERT INTO stock_unusual_market_state (trade_date,market,max_index,row_count,next_start,last_scan_at,last_full_scan_at,last_error)
                    VALUES (%s,%s,%s,%s,%s,now(),CASE WHEN %s THEN now() ELSE NULL END,'')
                    ON CONFLICT (trade_date,market) DO UPDATE SET max_index=%s,row_count=%s,next_start=%s,last_scan_at=now(),
                    last_full_scan_at=CASE WHEN %s THEN now() ELSE stock_unusual_market_state.last_full_scan_at END,last_error='',updated_at=now()""",
                    (state.trade_date, state.market, state.max_index, state.row_count, state.next_start, full, state.max_index, state.row_count, state.next_start, full))

    def record_gap(self, trade_date: str, market: int, expected: int, actual: int) -> None:
        if actual <= expected: return
        with self.connection() as conn:
            conn.execute("""INSERT INTO stock_unusual_gaps (trade_date,market,expected_index,actual_index,missing_count)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (trade_date, market, expected, actual, actual - expected))

    def upsert_events(self, events: list[Event]) -> int:
        if not events: return 0
        with self.connection() as conn:
            for e in events:
                conn.execute("""INSERT INTO stock_unusual_events
                    (trade_date,market,event_index,code,event_time,desc_text,value_text,unusual_type,source_start,seen_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (trade_date,market,event_index) DO UPDATE SET code=EXCLUDED.code,event_time=EXCLUDED.event_time,
                    desc_text=EXCLUDED.desc_text,value_text=EXCLUDED.value_text,unusual_type=EXCLUDED.unusual_type,
                    source_start=EXCLUDED.source_start,seen_at=EXCLUDED.seen_at,updated_at=now()""",
                    (e.trade_date, e.market, e.index, e.code, e.time, e.desc, e.value, e.unusual_type, e.source_start, e.seen_at or datetime.now(timezone.utc)))
        return len(events)

    def query(self, trade_date: str, markets: list[int], code: str = "", codes: list[str] | None = None,
              limit: int = 0, offset: int = 0, prefer_redis: bool = True) -> tuple[list[Event], str, int]:
        if prefer_redis and self.redis:
            try:
                values, found = self._query_redis(trade_date, markets, code, limit, offset)
                if found:
                    total = self._redis_count(trade_date, markets, code) if limit or offset else len(values)
                    return values, "redis", total
            except Exception:
                pass
        clauses = ["trade_date=%s"]
        args: list[Any] = [trade_date]
        if markets:
            clauses.append("market = ANY(%s)"); args.append(markets)
        if code: clauses.append("code=%s"); args.append(code)
        elif codes: clauses.append("code = ANY(%s)"); args.append(codes)
        where = " AND ".join(clauses)
        with self.connection() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM stock_unusual_events WHERE {where}", args).fetchone()["n"]
            sql = f"SELECT trade_date::text AS trade_date,market,event_index AS index,code,event_time::text AS time,desc_text AS desc,value_text AS value,unusual_type,source_start,seen_at::text AS seen_at FROM stock_unusual_events WHERE {where} ORDER BY event_time DESC,market DESC,event_index DESC,code DESC"
            params = list(args)
            if limit: sql += " LIMIT %s"; params.append(limit)
            if offset: sql += " OFFSET %s"; params.append(offset)
            rows = conn.execute(sql, params).fetchall()
        events = [self._row_event(row) for row in rows]
        return sorted(events, key=_event_sort_key), "postgres", int(total)

    def refresh_redis(self, trade_date: str, markets: list[int]) -> None:
        if not self.redis: return
        for market in markets or [0, 1, 2]:
            events, _, _ = self.query(trade_date, [market], prefer_redis=False)
            key = redis_key(trade_date, market)
            pipe = self.redis.pipeline()
            pipe.delete(key)
            if events:
                pipe.zadd(key, {json.dumps(e.to_dict(), ensure_ascii=False): _redis_score(e) for e in events})
            pipe.expire(key, 48 * 3600)
            pipe.execute()

    def _query_redis(self, trade_date: str, markets: list[int], code: str, limit: int, offset: int) -> tuple[list[Event], bool]:
        result: list[Event] = []; found = False
        for market in markets or [0, 1, 2]:
            key = redis_key(trade_date, market)
            if not self.redis.exists(key): continue
            found = True
            stop = offset + limit - 1 if limit else -1
            for raw in self.redis.zrevrange(key, 0, stop):
                data = json.loads(raw); event = Event(**data)
                if not code or event.code == code: result.append(event)
        result.sort(key=_event_sort_key, reverse=True)
        if offset: result = result[offset:]
        if limit: result = result[:limit]
        return sorted(result, key=_event_sort_key), found

    def _redis_count(self, trade_date: str, markets: list[int], code: str) -> int:
        if not code: return sum(self.redis.zcard(redis_key(trade_date, m)) for m in markets or [0, 1, 2])
        values, _ = self._query_redis(trade_date, markets, code, 0, 0)
        return len(values)

    @staticmethod
    def _row_event(row: dict[str, Any]) -> Event:
        return Event(trade_date=row["trade_date"], market=int(row["market"]), index=int(row["index"]), code=row["code"],
                     time=normalize_time(str(row["time"]).split("+")[0]), desc=row["desc"], value=row["value"],
                     unusual_type=int(row["unusual_type"]), source_start=int(row["source_start"]), seen_at=str(row.get("seen_at") or ""))


def redis_key(trade_date: str, market: int) -> str:
    return f"{REDIS_PREFIX}:{trade_date}:market:{market}:order"


def _event_sort_key(event: Event) -> tuple[str, int, int, str]:
    return event.time, event.market, event.index, event.code


def _redis_score(event: Event) -> float:
    h, m, s = (int(x) for x in event.time.split(":"))
    return (h * 3600 + m * 60 + s) * 100000 + event.market * 10000 + event.index


def events_from_tdx(trade_date: str, source_start: int, rows: list[Unusual]) -> list[Event]:
    seen_at = datetime.now(timezone.utc).isoformat()
    return [Event(trade_date, r.market, r.index, r.code, time=normalize_time(r.time), desc=r.desc, value=r.value,
                  unusual_type=r.unusual_type, source_start=source_start, seen_at=seen_at) for r in rows if is_a_stock(r.market, r.code)]
