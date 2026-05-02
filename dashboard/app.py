"""FastAPI app for the read-only TradeBot dashboard.

Endpoints:
    GET /                  — serves static/index.html (auto-polling UI)
    GET /api/health        — bot liveness from data/health.txt
    GET /api/today         — TradeLog.daily_summary() for today UTC
    GET /api/recent-fills  — last N rows from TradeLog.get_history()
    GET /api/info          — static metadata (account, version, started_at)
    GET /api/system        — bot PID/uptime, IB Gateway service + port 4001 status

Bind: 127.0.0.1:8080 — reach via Tailscale (http://100.113.140.69:8080) or SSH tunnel.
Never expose publicly without adding HTTP auth and TLS.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from data.trade_log import TradeLog

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_HEALTH_FILE = _ROOT / "data" / "health.txt"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

# The strategy fires on_tick() once per trading day at 16:10 ET.
# Weekend gaps: Fri 16:10 ET → Mon 16:10 ET = ~72h.
# _stale_threshold_seconds() returns a day-aware value so the dashboard
# doesn't false-alarm on weekends and holidays.
_WEEKDAY_STALE_SECONDS = 26 * 3600  # normal trading day: 26h
_WEEKEND_STALE_SECONDS = 80 * 3600  # covers Fri → Mon morning (72h + buffer)


def _stale_threshold_seconds() -> float:
    """Return the appropriate stale threshold for the current time.

    Uses US/Eastern time:
      - Sat/Sun:          80h (Fri tick is up to ~72h old by Mon morning)
      - Mon before 16:10: 80h (same weekend gap, not yet ticked today)
      - All other times:  26h (normal 24h trading-day cadence)
    """
    try:
        from zoneinfo import ZoneInfo

        et_tz = ZoneInfo("America/New_York")
    except Exception:
        from datetime import timedelta

        et_tz = timezone(timedelta(hours=-5))  # type: ignore[assignment]

    now_et = datetime.now(et_tz)
    wd = now_et.weekday()  # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    if wd in (5, 6):  # weekend
        return _WEEKEND_STALE_SECONDS
    if wd == 0 and (now_et.hour < 16 or (now_et.hour == 16 and now_et.minute < 10)):
        return _WEEKEND_STALE_SECONDS  # Monday before today's tick
    return _WEEKDAY_STALE_SECONDS


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
      * "ok"       — last tick within the day-aware stale threshold
      * "stale"    — file exists but tick is older than threshold
      * "missing"  — file does not exist (bot has not ticked since deploy)
      * "unreadable" — file exists but contents do not parse as ISO datetime
    """
    threshold = _stale_threshold_seconds()

    if not _HEALTH_FILE.exists():
        return {
            "status": "missing",
            "last_tick": None,
            "age_seconds": None,
            "stale_after_seconds": threshold,
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
            "stale_after_seconds": threshold,
        }

    if last_tick.tzinfo is None:
        last_tick = last_tick.replace(tzinfo=timezone.utc)

    age = (datetime.now(timezone.utc) - last_tick).total_seconds()
    status = "ok" if age <= threshold else "stale"
    return {
        "status": status,
        "last_tick": last_tick.isoformat(),
        "age_seconds": round(age, 1),
        "stale_after_seconds": threshold,
    }


@app.get("/api/today")
def api_today() -> Dict[str, Any]:
    return TradeLog().daily_summary()


@app.get("/api/recent-fills")
def api_recent_fills(limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 200))
    return TradeLog().get_history(limit=limit)


@app.get("/api/system")
def api_system() -> Dict[str, Any]:
    """Bot process info and IB Gateway status.

    All fields degrade gracefully: if systemctl is unavailable (dev PC / Windows)
    service fields return "unavailable" and numeric fields return None.

    Fields:
      bot_pid              — MainPID from systemd, or None
      bot_active_since     — ISO timestamp when tradebot.service entered active state
      bot_uptime_seconds   — seconds since active_since, or None
      bot_service_status   — "active" | "inactive" | "failed" | "unavailable"
      gateway_service_status — same set for ibgateway.service
      gateway_port_open    — True if a TCP connection to 127.0.0.1:4001 succeeds
    """
    return {
        **_systemctl_info("tradebot.service", prefix="bot"),
        **_systemctl_info("ibgateway.service", prefix="gateway"),
        "gateway_port_open": _probe_port("127.0.0.1", 4001),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _systemctl_info(service: str, prefix: str) -> Dict[str, Any]:
    """Return PID + uptime for *service* under the given key prefix.

    Keys returned (with prefix="bot"):
      bot_service_status, bot_pid, bot_active_since, bot_uptime_seconds
    """
    try:
        result = subprocess.run(
            ["systemctl", "show", service, "--property=MainPID,ActiveEnterTimestamp"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        props: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()

        pid: Optional[int] = int(props["MainPID"]) if props.get("MainPID", "0") != "0" else None

        active_since: Optional[str] = None
        uptime_seconds: Optional[float] = None
        raw_ts = props.get("ActiveEnterTimestamp", "")
        if raw_ts:
            try:
                # systemd format: "Fri 2026-05-02 10:00:00 UTC"
                ts = datetime.strptime(raw_ts, "%a %Y-%m-%d %H:%M:%S %Z").replace(
                    tzinfo=timezone.utc
                )
                active_since = ts.isoformat()
                uptime_seconds = round((datetime.now(timezone.utc) - ts).total_seconds(), 1)
            except ValueError:
                pass

        status_result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=3,
        )
        service_status = status_result.stdout.strip() or "unknown"

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {
            f"{prefix}_service_status": "unavailable",
            f"{prefix}_pid": None,
            f"{prefix}_active_since": None,
            f"{prefix}_uptime_seconds": None,
        }

    return {
        f"{prefix}_service_status": service_status,
        f"{prefix}_pid": pid,
        f"{prefix}_active_since": active_since,
        f"{prefix}_uptime_seconds": uptime_seconds,
    }


def _probe_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _get_app() -> FastAPI:
    """Accessor used by tests so they don't have to import module-level state."""
    return app
