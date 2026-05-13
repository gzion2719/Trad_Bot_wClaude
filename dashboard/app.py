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
import json
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

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from config.strategy_metadata import (
    STRATEGY_METADATA,
    DailyAt,
    Interval,
    StrategyMetadata,
    get_metadata,
)
from dashboard.console_auth import (
    ConsoleSessionLock,
    StepUpStore,
    audit_log,
    fingerprint_session,
)
from data.account_snapshot import downsample, read_equity_history, read_snapshot
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
    except (ImportError, KeyError) as exc:
        raise RuntimeError(
            "tzdata package required for _stale_threshold_seconds(). Run: pip install tzdata"
        ) from exc

    now_et = datetime.now(et_tz)
    wd = now_et.weekday()  # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    if wd in (5, 6):  # weekend
        return _WEEKEND_STALE_SECONDS
    if wd == 0 and (now_et.hour < 16 or (now_et.hour == 16 and now_et.minute < 10)):
        return _WEEKEND_STALE_SECONDS  # Monday before today's tick
    return _WEEKDAY_STALE_SECONDS


_STARTED_AT = datetime.now(timezone.utc).isoformat()
# Read from the same DB the bot writes to. `main.py` pins `paper_trades.db`;
# the dashboard had been using `TradeLog()` default (`trades.db`), which is why
# every fill-derived surface (Recent Fills, per-strategy KPIs) showed empty on
# the VPS. Live mode (Phase 7) will need this to become env-configurable.
_trade_log = TradeLog(db_path=_ROOT / "data" / "paper_trades.db")


# Strict CSP: no inline scripts/styles, no remote origins, no framing.
# When the noVNC console page lands, it gets its own per-route CSP that allows
# the vendored noVNC bundle and a same-origin WebSocket connect-src.
_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": _DEFAULT_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach a strict Content-Security-Policy and friends to every response.

    Routes that need a relaxed CSP (e.g. the noVNC console page) can override
    via response.headers after this middleware sets the defaults.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            # Don't clobber a per-route override that an endpoint already set.
            response.headers.setdefault(key, value)
        return response


app = FastAPI(title="TradeBot Dashboard", version="0.1.0")
app.add_middleware(SecurityHeadersMiddleware)

# Mount /static so the index.html can reference /static/<asset>.
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


# NOTE: _DEFAULT_CSP uses `connect-src 'self'`, which CSP3-compliant browsers
# (Chrome 95+, FF 99+, Safari 15.4+) treat as covering same-origin ws/wss.
# No separate console CSP constant is needed — the middleware applies _DEFAULT_CSP
# to all routes including /console.html. If a future change requires a different
# policy for the console page, add a per-route override here at that point.


@app.get("/console.html", include_in_schema=False)
def console_page() -> FileResponse:
    """Serve the noVNC console page. CSP is applied by SecurityHeadersMiddleware."""
    return FileResponse(str(_STATIC_DIR / "console.html"))


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


def _require_session(dashboard_session: Optional[str] = Cookie(default=None)) -> None:
    """Dependency: raises 401 if no valid session cookie present."""
    if not dashboard_session or not _is_valid_session(dashboard_session):
        raise HTTPException(status_code=401, detail="login required")


# Per-session sliding-window rate limit for /api/equity-history (10 req/min).
_SESSION_RATE_STATE: Dict[str, Dict[str, Any]] = {}
_session_rate_lock = threading.Lock()
_SESSION_EQUITY_MAX = 10
_SESSION_EQUITY_WINDOW = 60


def _enforce_session_rate_limit(sid: str) -> None:
    """Sliding-window rate limit keyed by session id. Raises 429 if exceeded."""
    now = time.monotonic()
    with _session_rate_lock:
        s = _SESSION_RATE_STATE.setdefault(sid, {"attempts": []})
        cutoff = now - _SESSION_EQUITY_WINDOW
        s["attempts"] = [t for t in s["attempts"] if t > cutoff]
        if len(s["attempts"]) >= _SESSION_EQUITY_MAX:
            raise HTTPException(status_code=429, detail="equity-history rate limit exceeded")
        s["attempts"].append(now)


def _clear_session_rate_limit(sid: str) -> None:
    """Remove a session's rate-limit entry on logout to prevent unbounded growth."""
    with _session_rate_lock:
        _SESSION_RATE_STATE.pop(sid, None)


@app.get("/api/today")
def api_today(_: None = Depends(_require_session)) -> Dict[str, Any]:
    return _trade_log.daily_summary()


@app.get("/api/recent-fills")
def api_recent_fills(limit: int = 20, _: None = Depends(_require_session)) -> List[Dict[str, Any]]:
    limit = max(1, min(limit, 200))
    return _trade_log.get_history(limit=limit)


# ---------------------------------------------------------------------------
# Per-strategy endpoints (Session 1)
# ---------------------------------------------------------------------------

# 30s TTL cache for /api/strategies/{name}/summary.
# Keyed on (name, last_fill_id) — any new fill changes last_fill_id and
# invalidates the entry instantly. The TTL only absorbs the idle-polling case
# (no new fills for 30s of 5s polls → cache serves 5/6 requests).
_SUMMARY_CACHE_TTL_SECONDS = 30.0
_summary_cache: Dict[str, Dict[str, Any]] = {}
_summary_cache_lock = threading.Lock()


def _schedule_to_dict(schedule: Any) -> Dict[str, Any]:
    """Serialize a Schedule (DailyAt | Interval) for JSON output."""
    if isinstance(schedule, DailyAt):
        return {
            "kind": "DailyAt",
            "hour": schedule.hour,
            "minute": schedule.minute,
            "tz": schedule.tz,
        }
    if isinstance(schedule, Interval):
        return {"kind": "Interval", "seconds": schedule.seconds}
    return {"kind": "unknown"}


def _metadata_to_dict(meta: StrategyMetadata) -> Dict[str, Any]:
    """Serialize a StrategyMetadata for JSON output."""
    caps = meta.risk_caps
    return {
        "name": meta.name,
        "symbol": meta.symbol,
        "schedule": _schedule_to_dict(meta.schedule),
        "risk_caps": {
            "max_order_value": caps.max_order_value,
            "max_position_value": caps.max_position_value,
            "max_daily_loss": caps.max_daily_loss,
            "max_open_orders": caps.max_open_orders,
            "max_risk_per_trade_pct": caps.max_risk_per_trade_pct,
            "min_reward_risk_ratio": caps.min_reward_risk_ratio,
        },
        "params": dict(meta.params),
        "state_file_path": meta.state_file_path,
    }


def _resolve_strategy(name: str) -> StrategyMetadata:
    """FastAPI dependency: 404 if `name` is not a registered strategy.

    Path-traversal safe: `name` is looked up against STRATEGY_METADATA by
    exact match. URL-decoded slashes / dots / mixed case all 404 because
    they don't equal any registered name. Used by every per-strategy
    endpoint so the path component is never used to build a file path.
    """
    meta = get_metadata(name)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"strategy {name!r} not registered")
    return meta


def _last_fill_id() -> int:
    """Return MAX(id) FROM trades, or 0 when empty. Cheap and indexed."""
    with _trade_log.connection() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()
    return int(row[0]) if row else 0


@app.get("/api/strategies")
def api_strategies(_: None = Depends(_require_session)) -> List[Dict[str, Any]]:
    """List every registered strategy with metadata. Read-only."""
    return [_metadata_to_dict(m) for m in STRATEGY_METADATA]


@app.get("/api/strategies/{name}/summary")
def api_strategy_summary(
    meta: StrategyMetadata = Depends(_resolve_strategy),
    _: None = Depends(_require_session),
) -> Dict[str, Any]:
    """Aggregate lifetime + today KPIs for one strategy. 30s TTL cache.

    Cache key includes MAX(id) FROM trades so a fresh fill invalidates the
    entry immediately; the TTL only absorbs idle polls.
    """
    last_id = _last_fill_id()
    now = time.monotonic()
    with _summary_cache_lock:
        entry = _summary_cache.get(meta.name)
        if entry is not None and entry["last_id"] == last_id and entry["expires_at"] > now:
            return entry["payload"]

    lifetime = _trade_log.lifetime_summary(meta.name)
    today_pnl = _trade_log.realized_pnl_today(meta.name)
    payload: Dict[str, Any] = {
        **lifetime,
        "realized_pnl_today": today_pnl,
        "symbol": meta.symbol,
        "schedule": _schedule_to_dict(meta.schedule),
    }
    with _summary_cache_lock:
        _summary_cache[meta.name] = {
            "last_id": last_id,
            "expires_at": now + _SUMMARY_CACHE_TTL_SECONDS,
            "payload": payload,
        }
    return payload


@app.get("/api/strategies/{name}/fills")
def api_strategy_fills(
    limit: int = 50,
    offset: int = 0,
    meta: StrategyMetadata = Depends(_resolve_strategy),
    _: None = Depends(_require_session),
) -> Dict[str, Any]:
    """Paginated fills for one strategy. `strategy_params` JSON parsed server-side.

    Returns {"fills": [...], "total": N, "limit": L, "offset": O}.
    """
    limit = max(1, min(limit, 500))
    # OFFSET pagination in SQLite is O(N) — the engine walks the index and
    # discards `offset` rows before returning any. 10k is ~5 years of expected
    # fill volume at one fill/day. Above that, callers should switch to keyset
    # pagination (BACKLOG DB-X8). Capping here also kills the trivial DoS of
    # an authenticated client sending `?offset=10^9`.
    offset = max(0, min(offset, 10_000))

    with _trade_log.connection(row_factory=True) as conn:
        total_row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE strategy_name = ?",
            (meta.name,),
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

        rows = conn.execute(
            "SELECT * FROM trades WHERE strategy_name = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (meta.name, limit, offset),
        ).fetchall()

    fills: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        raw = d.get("strategy_params")
        if raw:
            try:
                d["strategy_params"] = json.loads(raw)
            except (TypeError, ValueError):
                d["strategy_params"] = None
        fills.append(d)

    return {"fills": fills, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# IBKR Account tab endpoints — session-cookie gated, file-IPC only
# ---------------------------------------------------------------------------

_ACCOUNT_STALE_AFTER = 120  # seconds before a snapshot is considered stale


@app.get("/api/account")
def api_account(_: None = Depends(_require_session)) -> Dict[str, Any]:
    """Read account_snapshot.json and classify freshness.

    status values:
      "ok"          — file parsed, schema valid, age <= 120s
      "stale"       — file parsed, schema valid, age > 120s
      "unreadable"  — file exists but parse or schema-version failed
      "missing"     — file absent (poller not started or data dir missing)
    """
    snap = read_snapshot(_ROOT / "data")
    file_status = snap.get("status", "missing")
    age = snap.get("age_seconds")

    if file_status == "ok":
        status = "ok" if (age is not None and age <= _ACCOUNT_STALE_AFTER) else "stale"
    else:
        status = file_status

    snap["status"] = status
    snap["stale_after_seconds"] = _ACCOUNT_STALE_AFTER
    return snap


@app.get("/api/positions")
def api_positions(_: None = Depends(_require_session)) -> Dict[str, Any]:
    """Return current positions from the latest snapshot.

    Returns an empty list when the snapshot is missing or unreadable.
    """
    snap = read_snapshot(_ROOT / "data")
    file_status = snap.get("status", "missing")
    age = snap.get("age_seconds")

    if file_status == "ok":
        status = "ok" if (age is not None and age <= _ACCOUNT_STALE_AFTER) else "stale"
    else:
        status = file_status

    positions = snap.get("positions", []) if status in ("ok", "stale") else []
    return {"status": status, "positions": positions}


@app.get("/api/equity-history")
def api_equity_history(
    days: int = 30,
    dashboard_session: Optional[str] = Cookie(default=None),
    _: None = Depends(_require_session),
) -> Dict[str, Any]:
    """Return downsampled equity history for the requested day window.

    days is clamped to [1, 365]. Rate-limited to 10 req/min per session.
    """
    # _require_session guarantees dashboard_session is non-None here.
    # Use fingerprint (truncated hash) as the dict key — avoids storing the raw
    # 64-char secret token in _SESSION_RATE_STATE where a crash dump could leak it.
    _enforce_session_rate_limit(fingerprint_session(dashboard_session or ""))
    days = max(1, min(days, 365))
    points = read_equity_history(_ROOT / "data", days)
    orig_count = len(points)
    downsampled = downsample(points, max_points=2000)
    return {"points": downsampled, "days": days, "downsampled_from": orig_count}


# ---------------------------------------------------------------------------
# Control plane (Phase 3) — token-gated POST endpoints
# ---------------------------------------------------------------------------


# Per-IP rate limit + lockout state for /api/bot/* endpoints (CR-05).
# In-memory only — restarting the dashboard clears state, which is fine since
# legitimate operators have the token and unauthenticated callers benefit from
# the bind being Tailscale-only (CR-04).
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_ATTEMPTS = 30  # generous per-minute cap; lockout is the real gate
_LOCKOUT_FAILED_THRESHOLD = 3  # after 3 invalid-credential attempts in window
_LOCKOUT_DURATION_SECONDS = 180  # 3 min lockout

_rate_state: Dict[str, Dict[str, Any]] = {}
_rate_lock = threading.Lock()

# Session store — HttpOnly cookie replaces localStorage (CR-10).
# Sessions expire after 24h; in-memory only (cleared on dashboard restart).
_SESSION_COOKIE = "dashboard_session"
_SESSION_DURATION_SECONDS = 24 * 3600
_sessions: Dict[str, float] = {}  # session_id -> expiry (monotonic)
_sessions_lock = threading.Lock()

# Console step-up + single-session lock — gate the gateway VNC console.
# Both are process-local; restarting the dashboard releases any held lock and
# invalidates all step-up tokens (acceptable: forces a fresh re-auth).
_step_up_store = StepUpStore()
_console_lock = ConsoleSessionLock()


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
    """Track 401s; trip a lockout after _LOCKOUT_FAILED_THRESHOLD fails in the window."""
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
    """Return the real client IP for rate-limiting and logging.

    Default (TRUSTED_PROXIES not set): uses request.client.host only — X-Forwarded-For
    is ignored. Do NOT put this dashboard behind a reverse proxy without setting
    TRUSTED_PROXIES, or every request will appear to come from 127.0.0.1, making the
    per-IP lockout a global lockout (DoS) and allowing XFF rotation to bypass it.

    When TRUSTED_PROXIES is set (comma-separated IPs): honors X-Forwarded-For only
    when the direct peer is in the trusted list, then returns the leftmost non-trusted IP.
    """
    peer = request.client.host if request.client else "unknown"
    trusted_env = os.getenv("TRUSTED_PROXIES", "").strip()
    if not trusted_env:
        return peer
    trusted = {ip.strip() for ip in trusted_env.split(",") if ip.strip()}
    if peer not in trusted:
        return peer
    xff = request.headers.get("X-Forwarded-For", "")
    if not xff:
        return peer
    for candidate in (ip.strip() for ip in xff.split(",")):
        if candidate and candidate not in trusted:
            return candidate
    return peer


def _check_origin(request: Request) -> None:
    """Reject POST requests with an Origin header that doesn't match the dashboard host.

    This is CSRF defense-in-depth on top of SameSite=Strict cookies. API/script
    callers that send no Origin are allowed through — they cannot be CSRF-triggered
    from a browser. Browsers always include Origin on cross-origin POST requests.
    """
    origin = request.headers.get("origin")
    if not origin:
        return
    host = request.headers.get("host", "")
    origin_host = origin.split("://", 1)[-1].rstrip("/")
    if origin_host != host:
        raise HTTPException(status_code=403, detail="CSRF check failed: Origin mismatch")


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

    expected = os.getenv("DASHBOARD_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="DASHBOARD_TOKEN not configured")

    # Session cookie path (UI) — valid sessions bypass rate-limiting so rapid
    # restart→stop clicks from a logged-in operator don't get 429d.
    if dashboard_session and _is_valid_session(dashboard_session):
        return

    # Bearer token path (scripts / API callers) — rate-limited per IP.
    _enforce_rate_limit(ip)
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer ") :].strip()
        if hmac.compare_digest(provided, expected):
            return

    _record_auth_failure(ip)
    raise HTTPException(status_code=401, detail="authentication required")


@app.post("/api/login")
def api_login(
    request: Request,
    body: Dict[str, str],
    response: Response,
    _o: None = Depends(_check_origin),
) -> Dict[str, Any]:
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
        _step_up_store.revoke_session(dashboard_session)
        _console_lock.release(fingerprint_session(dashboard_session))
        _delete_session(dashboard_session)
        _clear_session_rate_limit(fingerprint_session(dashboard_session))
    response.delete_cookie(key=_SESSION_COOKIE, path="/")
    return {"ok": True}


@app.post("/api/console/login")
def api_console_login(
    request: Request,
    body: Dict[str, str],
    response: Response,
    _o: None = Depends(_check_origin),
    dashboard_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """Step-up password challenge. Issues a 5-minute console token.

    Requires:
      - A valid dashboard session cookie (CR-10).
      - A correct DASHBOARD_CONSOLE_PASSWORD in the request body.

    DASHBOARD_CONSOLE_PASSWORD is intentionally separate from DASHBOARD_TOKEN
    so that a leaked dashboard token does not grant trading authority via
    the gateway console.

    On success: sets short-lived `console_token` cookie (HttpOnly, SameSite=Strict).
    """
    ip = _client_ip(request)
    _enforce_rate_limit(ip)

    if not dashboard_session or not _is_valid_session(dashboard_session):
        raise HTTPException(status_code=401, detail="dashboard session required")

    expected = os.getenv("DASHBOARD_CONSOLE_PASSWORD", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="DASHBOARD_CONSOLE_PASSWORD not configured")

    provided = (body.get("password") or "").strip()
    fp = fingerprint_session(dashboard_session)
    if not provided or not hmac.compare_digest(provided, expected):
        _record_auth_failure(ip)
        audit_log("console.step_up.failure", fp, ip)
        raise HTTPException(status_code=401, detail="invalid console password")

    token = _step_up_store.issue(dashboard_session)
    response.set_cookie(
        key="console_token",
        value=token,
        httponly=True,
        samesite="strict",
        max_age=300,
        path="/",
    )
    audit_log("console.step_up.success", fp, ip)
    return {"ok": True, "expires_in": 300}


def _require_console_token(
    request: Request,
    dashboard_session: Optional[str],
    console_token: Optional[str],
) -> str:
    """Validate the full console auth chain. Returns the session fingerprint.

    Raises 401 (no session / bad step-up) or 403 (origin mismatch) on any
    failure. Logs an audit-failure event before raising.
    """
    if not dashboard_session or not _is_valid_session(dashboard_session):
        raise HTTPException(status_code=401, detail="dashboard session required")
    if not console_token or not _step_up_store.validate(console_token, dashboard_session):
        ip = _client_ip(request)
        fp = fingerprint_session(dashboard_session)
        audit_log("console.auth.no_step_up", fp, ip)
        raise HTTPException(status_code=401, detail="console step-up required")
    return fingerprint_session(dashboard_session)


@app.post("/api/console/acquire")
def api_console_acquire(
    request: Request,
    _o: None = Depends(_check_origin),
    dashboard_session: Optional[str] = Cookie(default=None),
    console_token: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """Acquire the single-session console lock.

    Returns 409 if another operator holds the lock (with their fingerprint
    + acquired_at_iso so the UI can explain the wait). Idempotent — calling
    while already holding returns the existing lock.
    """
    fp = _require_console_token(request, dashboard_session, console_token)
    ip = _client_ip(request)
    acquired_iso = datetime.now(timezone.utc).isoformat()

    holder = _console_lock.acquire(fp, ip, acquired_iso)
    if holder is None:
        current = _console_lock.current_holder()
        if current is not None and current.session_fingerprint == fp:
            audit_log("console.lock.reacquire", fp, ip)
            return {
                "ok": True,
                "held_by": current.session_fingerprint,
                "held_since": current.acquired_at_iso,
                "reacquired": True,
            }
        contender_fp = current.session_fingerprint if current else "unknown"
        audit_log("console.lock.contended", fp, ip, detail=f"held_by={contender_fp}")
        raise HTTPException(
            status_code=409,
            detail={
                "error": "console held by another session",
                "held_by": contender_fp,
                "held_since": current.acquired_at_iso if current else None,
            },
        )

    audit_log("console.lock.acquired", fp, ip)
    return {"ok": True, "held_by": fp, "held_since": acquired_iso, "reacquired": False}


@app.post("/api/console/release")
def api_console_release(
    request: Request,
    _o: None = Depends(_check_origin),
    dashboard_session: Optional[str] = Cookie(default=None),
) -> Dict[str, Any]:
    """Release the lock if the caller currently holds it. 200 even if not held.

    Requires only a valid session cookie — NOT the step-up token. The lock is
    identified by session fingerprint, so a caller can release their own lock
    even after the 5-minute step-up token has expired (e.g. on Disconnect click
    or page unload beacon after an idle session).
    """
    if not dashboard_session or not _is_valid_session(dashboard_session):
        raise HTTPException(status_code=401, detail="dashboard session required")
    fp = fingerprint_session(dashboard_session)
    ip = _client_ip(request)
    released = _console_lock.release(fp)
    if released:
        audit_log("console.lock.released", fp, ip)
    return {"ok": True, "released": released}


@app.websocket("/ws/console")
async def ws_console(
    websocket: WebSocket,
    dashboard_session: Optional[str] = Cookie(default=None),
    console_token: Optional[str] = Cookie(default=None),
) -> None:
    """Reverse-proxy a browser WebSocket to websockify (127.0.0.1:6080).

    Auth chain (all required, in order):
      1. Per-IP rate limit
      2. Same-origin check on the WebSocket upgrade
      3. Valid dashboard session cookie
      4. Valid console step-up token bound to that session
      5. Caller currently holds the console lock

    On any failure we close with a specific code rather than 1000 so the
    UI can distinguish "log in again" from "wait, someone else is on" from
    "upstream gone". 4001 = need step-up, 4003 = lock held by other,
    4029 = rate limited, 1011 = upstream connect failure.

    Every rejection branch emits an audit_log event so failed-auth attempts
    are visible in journalctl alongside successful connects.
    """
    # We must accept() before sending custom close codes — pre-accept close
    # is downgraded to HTTP 403 by ASGI servers and the browser sees only
    # generic 1008. Accepting first costs ~one round-trip; the WS is open for
    # a few ms before we may close, which is acceptable for our threat model.
    # Subprotocol "binary" matches what noVNC's RFB client requests.
    await websocket.accept(subprotocol="binary")

    ip = websocket.client.host if websocket.client is not None else "unknown"

    # 1. Per-IP rate limit — same sliding-window gate as the HTTP console endpoints.
    try:
        _enforce_rate_limit(ip)
    except HTTPException:
        audit_log("console.ws.rate_limited", "unknown", ip)
        await websocket.close(code=4029, reason="rate limited")
        return

    # 2. Same-origin check on upgrade — websockets bypass the HTTP _check_origin
    #    dependency, so we re-implement the rule here.
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    if origin:
        origin_host = origin.split("://", 1)[-1].rstrip("/")
        if origin_host != host:
            _record_auth_failure(ip)
            audit_log("console.ws.origin_mismatch", "unknown", ip)
            await websocket.close(code=4403, reason="origin mismatch")
            return

    # 3. Session cookie.
    if not dashboard_session or not _is_valid_session(dashboard_session):
        _record_auth_failure(ip)
        audit_log("console.ws.no_session", "unknown", ip)
        await websocket.close(code=4401, reason="dashboard session required")
        return

    fp = fingerprint_session(dashboard_session)

    # 4. Step-up token bound to this session.
    if not console_token or not _step_up_store.validate(console_token, dashboard_session):
        _record_auth_failure(ip)
        audit_log("console.ws.no_step_up", fp, ip)
        await websocket.close(code=4001, reason="console step-up required")
        return

    # 5. Lock holder check. Acquire-then-proxy is the documented flow; if a
    #    caller didn't acquire first we refuse rather than auto-acquire (clearer
    #    audit trail; UI always calls /api/console/acquire before connecting).
    current = _console_lock.current_holder()
    if current is None or current.session_fingerprint != fp:
        audit_log("console.ws.lock_not_held", fp, ip)
        await websocket.close(code=4003, reason="lock not held by caller")
        return

    audit_log("console.ws.connect", fp, ip)

    def _on_activity(event: str) -> None:
        # Touch the lock on each activity so idle-release uses the actual
        # last-seen time, not the acquire time. Disconnect logs the event.
        if event == "connect":
            _console_lock.touch(fp)
        elif event == "disconnect":
            audit_log("console.ws.disconnect", fp, ip)

    # Late import so the module is testable without importing websockets.
    from dashboard.console_proxy import proxy_console_websocket

    await proxy_console_websocket(websocket, on_activity=_on_activity)


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
def api_bot_restart(
    _: None = Depends(_check_token), _o: None = Depends(_check_origin)
) -> Dict[str, Any]:
    return _systemctl_action("restart")


@app.post("/api/bot/stop")
def api_bot_stop(
    _: None = Depends(_check_token), _o: None = Depends(_check_origin)
) -> Dict[str, Any]:
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
      console_held_by      — session fingerprint (16 hex chars) of console lock holder, or None
      console_held_since   — ISO timestamp the lock was acquired, or None
    """
    holder = _console_lock.current_holder()
    return {
        **_systemctl_info("tradebot.service", prefix="bot"),
        **_systemctl_info("ibgateway.service", prefix="gateway"),
        "gateway_port_open": _probe_port("127.0.0.1", 4001),
        "console_held_by": holder.session_fingerprint if holder else None,
        "console_held_since": holder.acquired_at_iso if holder else None,
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
