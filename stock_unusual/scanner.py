from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from .config import Settings, load_env_file
from .store import Store, events_from_tdx, is_trading_time, shanghai_today
from .tdx import TDXClient

log = logging.getLogger("stock-unusual.scanner")


@dataclass
class ScanSummary:
    trade_date: str
    market: int
    mode: str = ""
    start: int = 0
    fetched: int = 0
    upserted: int = 0
    new_rows: int = 0
    max_index: int = 0
    row_count: int = 0
    next_start: int = 0
    gap_count: int = 0
    duration_ms: int = 0
    error: str = ""


class Scanner:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings

    def scan_once(self, trade_date: str, force_full: bool = False) -> list[ScanSummary]:
        client = TDXClient(self.settings.tdx_hosts, self.settings.tdx_timeout)
        client.connect()
        try:
            return [self._scan_market(client, trade_date, market, force_full) for market in self.settings.markets]
        finally:
            client.close()

    def _scan_market(self, client: TDXClient, trade_date: str, market: int, force_full: bool) -> ScanSummary:
        started = time.monotonic(); summary = ScanSummary(trade_date, market)
        state, exists = self.store.get_state(trade_date, market)
        full = force_full or not exists
        start = 0 if full else max(0, state.next_start)
        summary.mode, summary.start = ("full" if full else "incremental"), start
        try:
            raw = []
            for page in range(self.settings.max_pages):
                rows = client.get_unusual(market, start + page * self.settings.page_size, self.settings.page_size)
                if not rows: break
                raw.extend(rows)
                if len(rows) < self.settings.page_size: break
            all_events = events_from_tdx(trade_date, start, raw)
            summary.fetched = len(all_events)
            summary.upserted = self.store.upsert_events(all_events)
            indexes = sorted({e.index for e in all_events if e.index > state.max_index})
            summary.new_rows = len(indexes)
            expected, gaps = state.max_index + 1, 0
            if not full:
                for index in indexes:
                    if index > expected:
                        gaps += 1
                        self.store.record_gap(trade_date, market, expected, index)
                    if index >= expected: expected = index + 1
            max_index = max([state.max_index, *(e.index for e in all_events)], default=state.max_index)
            row_count = len(all_events) if full else max(state.row_count, start + len(all_events))
            next_start = max(0, row_count - self.settings.overlap_rows)
            self.store.upsert_state(MarketStateProxy(trade_date, market, max_index, row_count, next_start), full)
            self.store.refresh_redis(trade_date, [market])
            summary.max_index, summary.row_count, summary.next_start, summary.gap_count = max_index, row_count, next_start, gaps
        except Exception as exc:
            summary.error = str(exc)
            log.exception("market=%s scan failed", market)
            try: self.store.upsert_state(MarketStateProxy(trade_date, market), full, summary.error)
            except Exception: log.exception("failed to persist scan error")
        summary.duration_ms = int((time.monotonic() - started) * 1000)
        return summary


# Keep the scanner's state shape local while avoiding a second public model.
class MarketStateProxy:
    def __init__(self, trade_date: str, market: int, max_index: int = 0, row_count: int = 0, next_start: int = 0):
        self.trade_date, self.market = trade_date, market
        self.max_index, self.row_count, self.next_start = max_index, row_count, next_start


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent Python stock_unusual collector")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--ignore-trading-window", action="store_true")
    parser.add_argument("--env", default="/etc/stock-unusual-python.env")
    args = parser.parse_args()
    load_env_file(args.env)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    store = Store(settings.database_url, settings.redis_url)
    store.ensure_schema(); store.ping()
    scanner = Scanner(store, settings)
    force_full = args.force_full
    log.info("collector started markets=%s interval=%ss once=%s", settings.markets, settings.poll_interval, args.once)
    while True:
        if args.ignore_trading_window or is_trading_time():
            try:
                summaries = scanner.scan_once(shanghai_today(), force_full)
                log.info("scan summaries: %s", json.dumps([asdict(x) for x in summaries], ensure_ascii=False))
            except Exception as exc:
                log.exception("scan failed: %s", exc)
            force_full = False
            if args.once: break
            time.sleep(settings.poll_interval)
        else:
            if args.once:
                log.info("outside A-share trading window; skipped")
                break
            time.sleep(30)


if __name__ == "__main__": main()
