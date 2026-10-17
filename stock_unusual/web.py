from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .blocks import CustomBlocks
from .config import Settings, load_env_file
from .store import Event, Store, shanghai_today

log = logging.getLogger("stock-unusual.web")
settings = Settings.from_env()
store = Store(settings.database_url, settings.redis_url) if settings.database_url else None
blocks = CustomBlocks(settings.custom_block_mysql_config)
name_cache: dict[str, Any] = {"path": "", "mtime_ns": 0, "names": {}}
app = FastAPI(title="stock_unusual Python", version="0.1.0")


def _parse_markets(value: str | None, scope: str, code: str) -> list[int]:
    if value:
        try: return [int(x.strip()) for x in value.split(",") if x.strip()]
        except ValueError: raise HTTPException(400, "markets 必须是逗号分隔的整数")
    if scope == "current":
        if not code: raise HTTPException(400, "scope=current 时需要 code")
        return [1 if code.startswith("6") else 2 if code.startswith(("4", "8", "9")) else 0]
    return [0, 1, 2]


def _options(request: Request) -> dict[str, Any]:
    q = request.query_params; code = q.get("code", "").strip()
    if code and (len(code) != 6 or not code.isdigit()): raise HTTPException(400, "code 必须是 6 位股票代码")
    scope = q.get("scope", "current" if code else "all").strip()
    if scope not in {"all", "custom", "current"}: raise HTTPException(400, "scope 只支持 all、custom 或 current")
    mode, name, text = q.get("block_mode", "").strip(), q.get("block_name", "").strip(), q.get("block_text", "").strip()
    if scope == "custom":
        mode = mode or "all"
        if mode not in {"all", "exact", "contains"}: raise HTTPException(400, "block_mode 只支持 all、exact 或 contains")
        if mode == "exact" and not name: raise HTTPException(400, "block_mode=exact 时需要 block_name")
        if mode == "contains" and not text: raise HTTPException(400, "block_mode=contains 时需要 block_text")
    def nonnegative(key: str, default: int, maximum: int = 0) -> int:
        value = q.get(key, "").strip()
        if not value: return default
        try: parsed = int(value)
        except ValueError: raise HTTPException(400, f"{key} 必须是非负整数")
        if parsed < 0 or maximum and parsed > maximum: raise HTTPException(400, f"{key} 参数无效")
        return parsed
    return {"code": code, "scope": scope, "markets": _parse_markets(q.get("markets"), scope, code), "date": q.get("date", "").strip(),
            "limit": nonnegative("limit", 0, 5000), "offset": nonnegative("offset", 0), "block_mode": mode, "block_name": name, "block_text": text}


def _load_names() -> dict[str, str]:
    path = Path(settings.stock_name_db)
    try: stat = path.stat()
    except OSError: return {}
    if name_cache["path"] == str(path) and name_cache["mtime_ns"] == stat.st_mtime_ns: return name_cache["names"]
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            names = {str(code).strip().lower(): str(name).strip() for code, name in db.execute("SELECT code,name FROM stockname")}
    except (OSError, sqlite3.Error): return {}
    name_cache.update(path=str(path), mtime_ns=stat.st_mtime_ns, names=names)
    return names


def _attach_names(items: list[Event]) -> None:
    names = _load_names()
    for event in items:
        prefix = "sh" if event.market == 1 else "bj" if event.market == 2 else "sz"
        event.name = names.get(prefix + event.code.lower(), "")


def _snapshot(opts: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    if store is None: raise RuntimeError("DATABASE_URL 未配置")
    trade_date = opts["date"] or store.latest_trade_date() or shanghai_today()
    custom = None
    if opts["scope"] == "custom":
        custom = blocks.universe(opts["block_mode"], opts["block_name"], opts["block_text"])
        if not custom.codes:
            events, source, total = [], "postgres", 0
        else:
            events, source, total = store.query(trade_date, opts["markets"], opts["code"], list(custom.codes), opts["limit"], opts["offset"], False)
    else:
        events, source, total = store.query(trade_date, opts["markets"], opts["code"], None, opts["limit"], opts["offset"], True)
    _attach_names(events)
    events.sort(key=lambda e: (e.time, e.market, e.index, e.code))
    warning = "Redis 实时缓冲未命中，已回退读取 PostgreSQL。" if source == "postgres" and opts["scope"] != "custom" else ""
    payload = {"scope": opts["scope"], "code": opts["code"], "trade_date": trade_date, "markets": opts["markets"],
               "items": [e.to_dict() for e in events], "total_rows": total, "limit": opts["limit"], "offset": opts["offset"],
               "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "duration_ms": int((time.monotonic() - started) * 1000),
               "warning": warning, "data_source": source, "generated_by": "stock-unusual-python"}
    if custom: payload.update(block_mode=opts["block_mode"], block_name=opts["block_name"], block_text=opts["block_text"], block_count=custom.block_count, stock_count=custom.stock_count)
    if events: payload.update(last_index=events[-1].index, last_time=events[-1].time)
    return payload


@app.get("/")
def root(request: Request):
    if request.url.path.startswith("/code="): return RedirectResponse("/unusual" + request.url.path[5:])
    return RedirectResponse("/unusual", status_code=307)


@app.get("/unusual")
def unusual_page():
    return FileResponse(Path(settings.static_dir) / "unusual.html", media_type="text/html")


@app.get("/health/live")
def live(): return {"status": "ok", "service": "stock-unusual-python"}


@app.get("/health/ready")
def ready():
    try:
        if store is None: raise RuntimeError("DATABASE_URL 未配置")
        store.ping(); return {"status": "ready", "service": "stock-unusual-python"}
    except Exception:
        return JSONResponse({"status": "error", "service": "stock-unusual-python"}, status_code=503)


@app.get("/api/stock_unusual")
@app.get("/api/stock-unusual")
def api_stock_unusual(request: Request):
    try: return _snapshot(_options(request))
    except HTTPException: raise
    except Exception as exc:
        log.exception("snapshot failed")
        raise HTTPException(502, str(exc))


@app.get("/api/stock_unusual_blocks")
@app.get("/api/stock-unusual-blocks")
def api_blocks():
    try:
        items, total_stocks = blocks.options()
        return {"items": [{"name": x.name, "stock_count": x.stock_count} for x in items], "total_blocks": len(items), "total_stocks": total_stocks, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    except Exception as exc: raise HTTPException(502, str(exc))


@app.websocket("/ws/stock_unusual")
@app.websocket("/ws/stock-unusual")
async def ws_stock_unusual(websocket: WebSocket):
    await websocket.accept(); last_signature = ""
    try:
        while True:
            opts = _options_from_query(dict(websocket.query_params))
            try:
                payload = await asyncio.to_thread(_snapshot, opts)
                signature = hashlib.sha1(json.dumps(payload["items"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                if signature != last_signature:
                    await websocket.send_json(payload); last_signature = signature
            except Exception as exc:
                await websocket.send_json({"error": str(exc), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError): pass


def _options_from_query(q: dict[str, str]) -> dict[str, Any]:
    # WebSocket has no Request object; reuse the same validation rules.
    class QueryRequest:
        query_params = q
    return _options(QueryRequest())  # type: ignore[arg-type]


def main() -> None:
    import uvicorn
    load_env_file(os.getenv("STOCK_UNUSUAL_ENV", "/etc/stock-unusual-python.env"))
    global settings, store, blocks
    settings = Settings.from_env(); store = Store(settings.database_url, settings.redis_url); store.ensure_schema()
    blocks = CustomBlocks(settings.custom_block_mysql_config)
    host, port = settings.web_addr.rsplit(":", 1)
    uvicorn.run(app, host=host, port=int(port), log_level=os.getenv("LOG_LEVEL", "info"))
