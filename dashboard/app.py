"""FastAPI app for the TradeBot dashboard.

Read-only endpoints:
    GET /                  — serves static/index.html (auto-polling UI)
    GET /api/health        — bot liveness from data/health.txt
    GET /api/today         — TradeLog.daily_summary() for today UTC
    GET /api/recent-fills  — last N rows from TradeLog.get_history()
    GET /api/info          — static metadata (account, version, started_at)
    GET /api/system        — bot PID/uptime, IB Gateway service + port 4001 status

Control-plane endpoints (require Authorization: Bearer <DASHBOARD_TOKEN>):
    POST /api/bot/restart  — sudo systemctl restart tradebot.service
    POST /api/bot/stop     — sudo systemctl stop tradebot.service

Bind: Tailscale IP only (e.g. 100.113.140.69:8080). Reach via Tailscale or
SSH tunnel. UFW also blocks 8080 on the public NIC; the Tailscale-only bind
is defense-in-depth so the socket cannot accept on 0.0.0.0 at all.
Never expose publicly without TLS. Tailscale provides transport encryption.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
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
_trade_log = TradeLog()

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
    return _trade_log.daily_summary()


@app.get("/api/recent-fills")
def api_recent_fills(limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 200))
    return _trade_log.get_history(limit=limit)


# ---------------------------------------------------------------------------
# Control plane (Phase 3) — token-gated POST endpoints
# ---------------------------------------------------------------------------


# Per-IP rate limit + lockout state for /api/bot/* endpoints (CR-05).
# In-memory only — restarting the dashboard clears state, which is fine since
# legitimate operators have the token and unauthenticated callers benefit from
# the bind being Tailscale-only (CR-04).
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_ATTEMPTS = 3  # 3 control-plane requests per minute per IP
_LOCKOUT_FAILED_THRESHOLD = 10  # after 10 invalid-token attempts in window
_LOCKOUT_DURATION_SECONDS = 300  # 5 min lockout

_rate_state: Dict[str, Dict[str, Any]] = {}
_rate_lock = threading.Lock()

# Session store — HttpOnly cookie replaces localStorage (CR-10).
# Sessions expire after 24h; in-memory only (cleared on dashboard restart).
_SESSION_COOKIE = "dashboard_session"
_SESSION_DURATION_SECONDS = 24 * 3600
_sessions: Dict[str, float] = {}  # session_id -> expiry (monotonic)
_sessions_lock = threading.Lock()


def _create_session() -> str:
    sid = secrets.token_hex(32)
    with _sessions_lock:
        _sessions[sid] = time.monotonic() + _SESSION_DURATION_SECONDS
    return sid


def _is_valid_session(sid: str) -> bool:
    with _sessions_lock:
        expiry = _sessions.get(sid, 0.0)
        if time.monotonic() > expiry:
            _sessions.pop(sid, None)
            return False
        return True


def _delete_session(sid: str) -> None:
    with _sessions_lock:
        _sessions.pop(sid, None)


def _enforce_rate_limit(ip: str) -> None:
    """Sliding-window rate limit + sticky lockout. Raises 429 if exceeded."""
    now = time.monotonic()
    with _rate_lock:
        s = _rate_state.setdefault(ip, {"attempts": [], "fails": [], "lockout_until": 0.0})
        if s["lockout_until"] > now:
            wait = int(s["lockout_until"] - now)
            raise HTTPException(status_code=429, detail=f"locked out, retry in {wait}s")
        cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
        s["attempts"] = [t for t in s["attempts"] if t > cutoff]
        if len(s["attempts"]) >= _RATE_LIMIT_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="too many requests")
        s["attempts"].append(now)


def _record_auth_failure(ip: str) -> None:
    """Track 401s; trip a 5-min lockout after _LOCKOUT_FAILED_THRESHOLD fails."""
    now = time.monotonic()
    with _rate_lock:
        s = _rate_state.setdefault(ip, {"attempts": [], "fails": [], "lockout_until": 0.0})
        cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
        s["fails"] = [t for t in s["fails"] if t > cutoff]
        s["fails"].append(now)
        if len(s["fails"]) >= _LOCKOUT_FAILED_THRESHOLD:
            s["lockout_until"] = now + _LOCKOUT_DURATION_SECONDS
            logger.warning(
                "Dashboard control-plane lockout for IP %s (%d invalid-token attempts in %ds)",
                ip,
                len(s["fails"]),
                _RATE_LIMIT_WINDOW_SECONDS,
            )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    dashboard_session: Optional[str] = Cookie(default=None),
) -> None:
    """Auth + per-IP rate limit/lockout for control endpoints.

    Accepts either:
      - A valid HttpOnly session cookie (set by POST /api/login) — preferred.
      - An Authorization: Bearer <DASHBOARD_TOKEN> header — for API/script callers.

    Returns 503 if DASHBOARD_TOKEN is not configured (fail-closed), 429 on
    rate-limit/lockout, 401 if neither credential is valid.
    """
    ip = _client_ip(request)
    _enforce_rate_limit(ip)

    expected = os.getenv("DASHBOARD_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="DASHBOARD_TOKEN not configured")

    # Session cookie path (UI)
    if dashboard_session and _is_valid_session(dashboard_session):
        return

    # Bearer token path (scripts / API callers)
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer ") :].strip()
        if hmac.compare_digest(provided, expected):
            return

    _record_auth_failure(ip)
    raise HTTPException(status_code=401, detail="authentication required")


@app.post("/api/login")
def api_login(request: Request, body: Dict[str, str], response: Response) -> Dict[str, Any]:
    """Exchange DASHBOARD_TOKEN for an HttpOnly session cookie.

    Body: {"token": "<DASHBOARD_TOKEN>"}
    On success: sets dashboard_session cookie (HttpOnly, SameSite=Strict) and returns {"ok": true}.
    On failure: 401.
    """
    ip = _client_ip(request)
    _enforce_rate_limit(ip)

    expected = os.getenv("DASHBOARD_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="DASHBOARD_TOKEN not configured")

    provided = (body.get("token") or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        _record_auth_failure(ip)
        raise HTTPException(status_code=401, detail="invalid token")

    sid = _create_session()
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=sid,
        httponly=True,
        samesite="strict",
        max_age=_SESSION_DURATION_SECONDS,
        path="/",
    )
    logger.info("Dashboard session created for IP %s", ip)
    return {"ok": True}


@app.post("/api/logout")
def api_logout(
    response: Response, dashboard_session: Optional[str] = Cookie(default=None)
) -> Dict[str, Any]:
    """Invalidate the current session cookie."""
    if dashboard_session:
        _delete_session(dashboard_session)
    response.delete_cookie(key=_SESSION_COOKIE, path="/")
    return {"ok": True}


_ALLOWED_ACTIONS = ("restart", "stop")


def _systemctl_action(action: str) -> Dict[str, Any]:
    """Execute `sudo -n /bin/systemctl <action> tradebot.service`.

    The sudoers rule at /etc/sudoers.d/tradebot-dashboard scopes NOPASSWD
    privilege to exactly these two commands for the `tradebot` user. Anything
    else returns 400.
    """
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=400, detail=f"unsupported action {action!r}")
    try:
        result = subprocess.run(
            ["sudo", "-n", "/bin/systemctl", action, "tradebot.service"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error("systemctl %s tradebot.service failed: %s", action, exc)
        raise HTTPException(status_code=500, detail=f"systemctl {action} failed: {exc}")
    if result.returncode != 0:
        logger.error(
            "systemctl %s tradebot.service rc=%d stderr=%s",
            action,
            result.returncode,
            result.stderr,
        )
        raise HTTPException(
            status_code=500,
            detail=f"systemctl {action} rc={result.returncode}: {result.stderr.strip()}",
        )
    logger.warning("systemctl %s tradebot.service succeeded (dashboard-initiated)", action)
    return {"ok": True, "action": action, "stdout": result.stdout.strip()}


@app.post("/api/bot/restart")
def api_bot_restart(_: None = Depends(_check_token)) -> Dict[str, Any]:
    return _systemctl_action("restart")


@app.post("/api/bot/stop")
def api_bot_stop(_: None = Depends(_check_token)) -> Dict[str, Any]:
    return _systemctl_action("stop")


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
