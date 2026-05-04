"""Section 18: Dashboard route tests — no IBKR connection needed."""

import os
import subprocess as sp_module
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from dashboard import app as dashboard_app

# ── helpers ───────────────────────────────────────────────��──────────────────


def _fake_request(ip: str = "10.0.0.1"):
    class _C:
        host = ip

    class _R:
        client = _C()

    return _R()


def _reset_rate_state() -> None:
    with dashboard_app._rate_lock:
        dashboard_app._rate_state.clear()


# ── route shape tests ─────────────────────────────────────────────────────────


def test_db01_api_info_keys():
    info = dashboard_app.api_info()
    for key in ("account", "host", "port", "dashboard_started_at", "version"):
        assert key in info
    assert isinstance(info["port"], int)


def test_db02_api_health_missing_file():
    original = dashboard_app._HEALTH_FILE
    dashboard_app._HEALTH_FILE = original.parent / "health_definitely_missing_xyz.txt"
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "missing"
        assert result["last_tick"] is None
        assert result["age_seconds"] is None
    finally:
        dashboard_app._HEALTH_FILE = original


def test_db03_api_health_fresh_tick_ok():
    original = dashboard_app._HEALTH_FILE
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(datetime.now(timezone.utc).isoformat())
    tmp.close()
    dashboard_app._HEALTH_FILE = Path(tmp.name)
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "ok"
        assert result["age_seconds"] is not None
        assert result["age_seconds"] < 60
    finally:
        dashboard_app._HEALTH_FILE = original
        Path(tmp.name).unlink(missing_ok=True)


def test_db04_api_health_old_tick_stale():
    original = dashboard_app._HEALTH_FILE
    old = datetime.now(timezone.utc) - timedelta(
        seconds=dashboard_app._WEEKEND_STALE_SECONDS + 3600
    )
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(old.isoformat())
    tmp.close()
    dashboard_app._HEALTH_FILE = Path(tmp.name)
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "stale"
    finally:
        dashboard_app._HEALTH_FILE = original
        Path(tmp.name).unlink(missing_ok=True)


def test_db05_api_health_garbage_unreadable():
    original = dashboard_app._HEALTH_FILE
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write("not-a-datetime")
    tmp.close()
    dashboard_app._HEALTH_FILE = Path(tmp.name)
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "unreadable"
    finally:
        dashboard_app._HEALTH_FILE = original
        Path(tmp.name).unlink(missing_ok=True)


def test_db06_api_recent_fills_clamps_limit():
    assert isinstance(dashboard_app.api_recent_fills(limit=99999), list)
    assert isinstance(dashboard_app.api_recent_fills(limit=-5), list)


def test_db07_api_system_keys():
    result = dashboard_app.api_system()
    for key in (
        "bot_service_status",
        "bot_pid",
        "bot_active_since",
        "bot_uptime_seconds",
        "gateway_service_status",
        "gateway_pid",
        "gateway_active_since",
        "gateway_uptime_seconds",
        "gateway_port_open",
    ):
        assert key in result


def test_db08_api_system_gateway_port_open_is_bool():
    result = dashboard_app.api_system()
    assert isinstance(result["gateway_port_open"], bool)


# ── auth / rate-limit tests ───────────────────────────────────────────────────


def test_db09_control_rejects_when_token_unset():
    os.environ.pop("DASHBOARD_TOKEN", None)
    _reset_rate_state()
    with pytest.raises(HTTPException) as exc_info:
        dashboard_app._check_token(_fake_request("10.0.0.9"), authorization="Bearer anything")
    assert exc_info.value.status_code == 503


def test_db10_control_rejects_missing_or_wrong_token():
    os.environ["DASHBOARD_TOKEN"] = "secret-xyz"
    _reset_rate_state()
    try:
        with pytest.raises(HTTPException) as exc_info:
            dashboard_app._check_token(_fake_request("10.0.0.10"), authorization=None)
        assert exc_info.value.status_code == 401

        with pytest.raises(HTTPException) as exc_info:
            dashboard_app._check_token(_fake_request("10.0.0.11"), authorization="Bearer wrong")
        assert exc_info.value.status_code == 401

        with pytest.raises(HTTPException) as exc_info:
            dashboard_app._check_token(
                _fake_request("10.0.0.12"), authorization="NotBearer secret-xyz"
            )
        assert exc_info.value.status_code == 401

        # correct token must not raise
        dashboard_app._check_token(_fake_request("10.0.0.13"), authorization="Bearer secret-xyz")
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


def test_db11_systemctl_action_ok_on_rc0(monkeypatch):
    class _FakeDone:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(dashboard_app.subprocess, "run", lambda *a, **kw: _FakeDone())
    result = dashboard_app._systemctl_action("restart")
    assert result["ok"] is True
    assert result["action"] == "restart"


def test_db12_systemctl_action_raises_500_on_nonzero(monkeypatch):
    class _FailDone:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    monkeypatch.setattr(dashboard_app.subprocess, "run", lambda *a, **kw: _FailDone())
    with pytest.raises(HTTPException) as exc_info:
        dashboard_app._systemctl_action("stop")
    assert exc_info.value.status_code == 500
    assert "rc=1" in str(exc_info.value.detail)


def test_db13_systemctl_action_rejects_unsupported():
    with pytest.raises(HTTPException) as exc_info:
        dashboard_app._systemctl_action("nuke")
    assert exc_info.value.status_code == 400


def test_db14_rate_limit_429_after_three_per_ip():
    os.environ["DASHBOARD_TOKEN"] = "secret-xyz"
    _reset_rate_state()
    ip = "10.0.0.14"
    try:
        for _ in range(dashboard_app._RATE_LIMIT_MAX_ATTEMPTS):
            dashboard_app._check_token(_fake_request(ip), authorization="Bearer secret-xyz")
        with pytest.raises(HTTPException) as exc_info:
            dashboard_app._check_token(_fake_request(ip), authorization="Bearer secret-xyz")
        assert exc_info.value.status_code == 429
        # different IP is unaffected
        dashboard_app._check_token(_fake_request("10.0.0.15"), authorization="Bearer secret-xyz")
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


def test_db15_lockout_after_failed_threshold():
    os.environ["DASHBOARD_TOKEN"] = "secret-xyz"
    _reset_rate_state()
    ip = "10.0.0.16"
    try:
        for _ in range(dashboard_app._LOCKOUT_FAILED_THRESHOLD):
            dashboard_app._record_auth_failure(ip)
        with pytest.raises(HTTPException) as exc_info:
            dashboard_app._check_token(_fake_request(ip), authorization="Bearer secret-xyz")
        assert exc_info.value.status_code == 429
        assert "locked out" in str(exc_info.value.detail)
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


# ── HTTP-layer tests (TestClient) ─────────────────────────────────────────────


def test_db16_http_missing_auth_header_401():
    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart")
        assert r.status_code == 401
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


def test_db17_http_wrong_scheme_401():
    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "Token tc-secret"})
        assert r.status_code == 401
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


def test_db18_http_wrong_token_401():
    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "Bearer bad"})
        assert r.status_code == 401
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


def test_db19_http_lowercase_bearer_401():
    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "bearer tc-secret"})
        assert r.status_code == 401
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


def test_db20_http_valid_token_200(monkeypatch):
    class _FakeDone:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sp_module, "run", lambda *a, **kw: _FakeDone())
    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "Bearer tc-secret"})
        assert r.status_code == 200
        assert r.json().get("ok") is True
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


# ── stale threshold branch tests (DB-21..DB-25) ──────────────────────────────
# Patch dashboard_app.datetime so _stale_threshold_seconds() sees a fixed time.


def _fake_dt(fake_now_et):
    """Return a datetime-shaped object whose .now(tz) returns fake_now_et.astimezone(tz)."""

    class _FakeDatetime:
        @staticmethod
        def now(tz=None):
            return fake_now_et.astimezone(tz) if tz else fake_now_et

        fromisoformat = staticmethod(datetime.fromisoformat)

    return _FakeDatetime


def _et(year, month, day, hour, minute):
    from zoneinfo import ZoneInfo

    return datetime(year, month, day, hour, minute, 0, tzinfo=ZoneInfo("America/New_York"))


def test_db21_stale_threshold_saturday():
    # 2026-05-02 is a Saturday → weekend threshold
    with patch("dashboard.app.datetime", _fake_dt(_et(2026, 5, 2, 12, 0))):
        assert dashboard_app._stale_threshold_seconds() == dashboard_app._WEEKEND_STALE_SECONDS


def test_db22_stale_threshold_sunday():
    # 2026-05-03 is a Sunday → weekend threshold
    with patch("dashboard.app.datetime", _fake_dt(_et(2026, 5, 3, 23, 59))):
        assert dashboard_app._stale_threshold_seconds() == dashboard_app._WEEKEND_STALE_SECONDS


def test_db23_stale_threshold_monday_before_tick():
    # Monday 16:09 ET → still in weekend gap, bot hasn't ticked today yet
    with patch("dashboard.app.datetime", _fake_dt(_et(2026, 5, 4, 16, 9))):
        assert dashboard_app._stale_threshold_seconds() == dashboard_app._WEEKEND_STALE_SECONDS


def test_db24_stale_threshold_monday_after_tick():
    # Monday 16:11 ET → bot has ticked, back to normal trading-day threshold
    with patch("dashboard.app.datetime", _fake_dt(_et(2026, 5, 4, 16, 11))):
        assert dashboard_app._stale_threshold_seconds() == dashboard_app._WEEKDAY_STALE_SECONDS


def test_db25_stale_threshold_midweek():
    # Wednesday midday → normal trading-day threshold
    with patch("dashboard.app.datetime", _fake_dt(_et(2026, 5, 6, 12, 0))):
        assert dashboard_app._stale_threshold_seconds() == dashboard_app._WEEKDAY_STALE_SECONDS


# ── security tests (DB-26..DB-28) ─────────────────────────────────────────────


def test_db26_client_ip_ignores_xff_without_trusted_proxies():
    # When TRUSTED_PROXIES is not set, X-Forwarded-For must not influence the key.
    os.environ.pop("TRUSTED_PROXIES", None)

    class _Headers(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    class _FakeClient:
        host = "10.0.0.1"

    class _FakeRequest:
        client = _FakeClient()
        headers = _Headers({"x-forwarded-for": "1.2.3.4"})

    ip = dashboard_app._client_ip(_FakeRequest())
    assert ip == "10.0.0.1", f"Expected peer IP 10.0.0.1, got {ip!r} — XFF must be ignored"


def test_db27_lockout_persists_for_valid_token_on_attempt_11(monkeypatch):
    # HTTP-layer lockout: 10 wrong tokens → attempt 11 with correct token returns 429.
    # Raise _RATE_LIMIT_MAX_ATTEMPTS so rate-per-minute doesn't fire before the lockout.
    monkeypatch.setattr(dashboard_app, "_RATE_LIMIT_MAX_ATTEMPTS", 100)
    os.environ["DASHBOARD_TOKEN"] = "lock-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        for _ in range(dashboard_app._LOCKOUT_FAILED_THRESHOLD):
            client.post("/api/bot/restart", headers={"Authorization": "Bearer wrong"})
        r = client.post("/api/bot/restart", headers={"Authorization": "Bearer lock-secret"})
        assert r.status_code == 429, f"Expected 429 lockout, got {r.status_code}"
        assert "locked out" in r.json().get("detail", "")
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


def test_db28_cookie_login_flow_authorises_control_endpoint(monkeypatch):
    # Login with valid token → receive session cookie → call /api/bot/restart with cookie only.
    class _FakeDone:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sp_module, "run", lambda *a, **kw: _FakeDone())
    os.environ["DASHBOARD_TOKEN"] = "cookie-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        login = client.post("/api/login", json={"token": "cookie-secret"})
        assert login.status_code == 200, f"Login failed: {login.status_code} {login.text}"
        # TestClient carries cookies automatically; no Authorization header sent
        r = client.post("/api/bot/restart")
        assert r.status_code == 200, f"Expected 200 with cookie auth, got {r.status_code}"
        assert r.json().get("ok") is True
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


# ── Static guards: catch regressions from a churning popup-features story ────

_STATIC_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "static"


def test_db29_console_button_uses_window_open_no_noopener() -> None:
    """Guard against the recurring popup-features regression.

    History: noopener was added for security, then removed because Chrome
    returns null from window.open() when noopener is set, breaking the
    popup-blocked detection. A future cleanup that re-adds noopener would
    silently re-introduce the false 'popup blocked' message on every click.
    """
    src = (_STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
    assert 'window.open("/console.html"' in src, (
        "Console button must call window.open('/console.html', ...). "
        "If you reverted to navigation, update or remove this guard."
    )
    # Allow noopener inside comments, but not in any features string the code
    # actually passes to window.open. Check the contiguous features const.
    features_lines = [
        line
        for line in src.splitlines()
        if "popup=yes" in line and "width=" in line and not line.lstrip().startswith("//")
    ]
    assert features_lines, "Could not locate popup features string."
    for line in features_lines:
        assert "noopener" not in line, (
            f"noopener in popup features breaks window.open's return value in "
            f"Chrome (returns null on success), defeating popup-blocked "
            f"detection. Offending line: {line.strip()}"
        )
        assert "noreferrer" not in line, (
            f"noreferrer has the same null-return effect as noopener in "
            f"Chrome popups. Offending line: {line.strip()}"
        )
