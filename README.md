# stock-unusual-python

Independent Python replacement for the `stock_unusual` realtime UI/API and collector. It does not import or link against gotdx; the TDX binary protocol is implemented locally in `stock_unusual/tdx.py`.

## Components

- `stock-unusual-web`: FastAPI web UI, REST API, WebSocket, health endpoints.
- `stock-unusuald`: Python collector with full scan, overlap-based incremental scan, PostgreSQL persistence, Redis realtime cache, and gap tracking.
- `stock-name-sync`: TDX stock-name snapshot refresher using SQLite atomic replacement.

The API remains compatible with the existing UI routes:

- `GET /unusual`
- `GET /api/stock_unusual` and `/api/stock-unusual`
- `GET /api/stock_unusual_blocks`
- `WS /ws/stock_unusual`
- `GET /health/live`, `/health/ready`

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

Use the existing deployment environment as a starting point without printing it:

```bash
.venv/bin/python -m stock_unusual.web
.venv/bin/python -m stock_unusual.scanner --once --ignore-trading-window
```

Configuration is read from `/etc/stock-unusual-python.env` by the installed units. The project intentionally uses `STOCK_UNUSUAL_*` names for its own settings, while accepting the existing `DATABASE_URL`, `REDIS_ADDR`, `REDIS_USERNAME`, and `REDIS_PASSWORD` storage settings.

Default development listener is `0.0.0.0:8898`, so it can be validated alongside the existing gotdx listener on port 8888. Change the port only after the alternate service passes health/API/UI verification.
