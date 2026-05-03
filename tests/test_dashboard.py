"""Section 18: Dashboard route tests — no IBKR connection needed."""

import os
import subprocess as sp_module
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
