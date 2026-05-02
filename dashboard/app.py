"""FastAPI app for the read-only TradeBot dashboard.

Endpoints:
    GET /                  — serves static/index.html (auto-polling UI)
    GET /api/health        — bot liveness from data/health.txt
    GET /api/today         — TradeLog.daily_summary() for today UTC
    GET /api/recent-fills  — last N rows from TradeLog.get_history()
    GET /api/info          — static metadata (account, version, started_at)

Bind: 127.0.0.1:8080 — reach via Tailscale (http://100.113.140.69:8080) or SSH tunnel.
Never expose publicly without adding HTTP auth and TLS.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from data.trade_log import TradeLog

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_HEALTH_FILE = _ROOT / "data" / "health.txt"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Threshold matching deploy/systemd/tradebot-health.service (26h tolerance for the
# weekend gap between Friday's tick and Monday's tick). Above this, we mark stale.
_STALE_AFTER_SECONDS = 93600

_STARTED_AT = datetime.now(timezone.utc).isoformat()

app = FastAPI(title="TradeBot Dashboard", version="0.1.0")

# Mount /static so the index.html can reference /static/<asset> if we add CSS/JS later.
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/api/info")
def api_info() -> Dict[str, Any]:
    return {
        "account": os.getenv("IB_ACCOUNT", "unknown"),
        "host": os.getenv("IB_HOST", "127.0.0.1"),
        "port": int(os.getenv("IB_PORT", "7497")),
        "dashboard_started_at": _STARTED_AT,
        "version": "0.1.0",
    }


@app.get("/api/health")
def api_health() -> Dict[str, Any]:
    """Read data/health.txt and report liveness.

    status values:
      * "ok"       — last tick within _STALE_AFTER_SECONDS
      * "stale"    — file exists but tick is older than threshold
      * "missing"  — file does not exist (bot has not ticked since deploy)
      * "unreadable" — file exists but contents do not parse as ISO datetime
    """
    if not _HEALTH_FILE.exists():
        return {
            "status": "missing",
            "last_tick": None,
            "age_seconds": None,
            "stale_after_seconds": _STALE_AFTER_SECONDS,
        }

    try:
        raw = _HEALTH_FILE.read_text().strip()
        last_tick = datetime.fromisoformat(raw)
    except (OSError, ValueError) as exc:
        logger.warning("api_health: could not parse %s: %s", _HEALTH_FILE, exc)
        return {
            "status": "unreadable",
            "last_tick": None,
            "age_seconds": None,
            "stale_after_seconds": _STALE_AFTER_SECONDS,
        }

    if last_tick.tzinfo is None:
        last_tick = last_tick.replace(tzinfo=timezone.utc)

    age = (datetime.now(timezone.utc) - last_tick).total_seconds()
    status = "ok" if age <= _STALE_AFTER_SECONDS else "stale"
    return {
        "status": status,
        "last_tick": last_tick.isoformat(),
        "age_seconds": round(age, 1),
        "stale_after_seconds": _STALE_AFTER_SECONDS,
    }


@app.get("/api/today")
def api_today() -> Dict[str, Any]:
    return TradeLog().daily_summary()


@app.get("/api/recent-fills")
def api_recent_fills(limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 200))
    return TradeLog().get_history(limit=limit)


def _get_app() -> FastAPI:
    """Accessor used by tests so they don't have to import module-level state."""
    return app
