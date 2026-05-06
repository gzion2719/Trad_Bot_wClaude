"""Auth-chain tests for the gateway console endpoints.

These exercise the FastAPI side of /api/console/acquire, /api/console/release,
and /ws/console — specifically the four-step auth chain (origin, session,
step-up token, lock holder).

The WebSocket bytes-relay itself is integration-tested manually against a
live websockify on the VPS during the mid-week rehearsal — pytest cannot
spawn a real VNC peer cleanly. We test that unauthorized upgrades are
refused with the right close codes; bytes flow is left to manual.
"""

from __future__ import annotations

import os
from typing import Tuple

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dashboard import app as dashboard_app
from dashboard.console_auth import fingerprint_session


def _reset_state() -> None:
    with dashboard_app._rate_lock:
        dashboard_app._rate_state.clear()
    dashboard_app._console_lock.force_release()


def _logged_in_client_with_step_up(
    password: str = "console-pass",
) -> Tuple[TestClient, str]:
    """Return a TestClient with both dashboard session AND step-up token cookies set."""
    _reset_state()
    sid = dashboard_app._create_session()
    client = TestClient(dashboard_app.app)
    client.cookies.set(dashboard_app._SESSION_COOKIE, sid)

    os.environ["DASHBOARD_CONSOLE_PASSWORD"] = password
    r = client.post("/api/console/login", json={"password": password})
    assert r.status_code == 200, r.text
    # TestClient persists Set-Cookie on the client for subsequent requests.
    return client, sid


# ── /api/console/acquire ──────────────────────────────────────────────────


def test_ce01_acquire_requires_session() -> None:
    _reset_state()
    client = TestClient(dashboard_app.app)
    r = client.post("/api/console/acquire")
    assert r.status_code == 401


def test_ce02_acquire_requires_step_up() -> None:
    """Cookie alone is insufficient — must have console_token too."""
    _reset_state()
    sid = dashboard_app._create_session()
    client = TestClient(dashboard_app.app)
    client.cookies.set(dashboard_app._SESSION_COOKIE, sid)
    r = client.post("/api/console/acquire")
    assert r.status_code == 401
    assert "step-up" in r.json()["detail"].lower()


def test_ce03_acquire_succeeds_after_step_up() -> None:
    client, sid = _logged_in_client_with_step_up()
    r = client.post("/api/console/acquire")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["held_by"] == fingerprint_session(sid)
    assert body["reacquired"] is False
    # Cleanup
    dashboard_app._console_lock.force_release()


def test_ce04_acquire_reacquire_is_idempotent_for_same_session() -> None:
    client, _sid = _logged_in_client_with_step_up()
    r1 = client.post("/api/console/acquire")
    assert r1.status_code == 200
    r2 = client.post("/api/console/acquire")
    assert r2.status_code == 200
    assert r2.json()["reacquired"] is True
    dashboard_app._console_lock.force_release()


def test_ce05_acquire_409_when_held_by_other() -> None:
    """Second session cannot acquire while first holds the lock."""
    _reset_state()
    # Session A acquires
    client_a, _sid_a = _logged_in_client_with_step_up(password="pwA")
    r_a = client_a.post("/api/console/acquire")
    assert r_a.status_code == 200

    # Session B tries — same TestClient instance to avoid cookie bleed
    sid_b = dashboard_app._create_session()
    client_b = TestClient(dashboard_app.app)
    client_b.cookies.set(dashboard_app._SESSION_COOKIE, sid_b)
    os.environ["DASHBOARD_CONSOLE_PASSWORD"] = "pwA"
    r_login_b = client_b.post("/api/console/login", json={"password": "pwA"})
    assert r_login_b.status_code == 200
    r_b = client_b.post("/api/console/acquire")
    assert r_b.status_code == 409
    detail = r_b.json()["detail"]
    assert detail["error"] == "console held by another session"
    dashboard_app._console_lock.force_release()


# ── /api/console/release ──────────────────────────────────────────────────


def test_ce10_release_requires_session() -> None:
    _reset_state()
    client = TestClient(dashboard_app.app)
    r = client.post("/api/console/release")
    assert r.status_code == 401


def test_ce11_release_clears_lock_for_holder() -> None:
    client, _sid = _logged_in_client_with_step_up()
    client.post("/api/console/acquire")
    r = client.post("/api/console/release")
    assert r.status_code == 200
    assert r.json()["released"] is True
    assert dashboard_app._console_lock.current_holder() is None


def test_ce12_release_noop_when_not_held() -> None:
    client, _sid = _logged_in_client_with_step_up()
    r = client.post("/api/console/release")
    assert r.status_code == 200
    assert r.json()["released"] is False


# ── /ws/console — auth-only checks (bytes flow is manual integration) ─────


def _expect_ws_close_with_reason(client: TestClient, must_contain: str) -> None:
    """Connect, expect server to accept then close with a reason string.

    starlette's TestClient drops custom 4xxx close codes (real browsers
    preserve them), so we assert on close-reason text instead.
    """
    try:
        with client.websocket_connect("/ws/console") as ws:
            # receive_text raises WebSocketDisconnect on close; receive() returns
            # the close as a dict and doesn't raise.
            ws.receive_text()
        raise AssertionError("expected close, got clean exit")
    except WebSocketDisconnect as e:
        assert (
            must_contain in str(e.reason or "").lower()
        ), f"reason {e.reason!r} did not contain {must_contain!r}"


def test_ce20_ws_rejects_without_session() -> None:
    _reset_state()
    client = TestClient(dashboard_app.app)
    _expect_ws_close_with_reason(client, "session")


def test_ce21_ws_rejects_without_step_up() -> None:
    _reset_state()
    sid = dashboard_app._create_session()
    client = TestClient(dashboard_app.app)
    client.cookies.set(dashboard_app._SESSION_COOKIE, sid)
    _expect_ws_close_with_reason(client, "step-up")


def test_ce22_ws_rejects_when_lock_not_held() -> None:
    """Even with valid cookie + step-up, must call /api/console/acquire first."""
    client, _sid = _logged_in_client_with_step_up()
    _expect_ws_close_with_reason(client, "lock")


# ── /console.html serves with WS-permissive CSP ───────────────────────────


def test_ce30_console_page_csp_uses_self_not_wildcard_ws() -> None:
    """/console.html CSP must use 'self' for connect-src, not bare ws:/wss:.

    'self' covers same-origin WebSockets in CSP3-compliant browsers (Chrome 95+,
    FF 99+, Safari 15.4+). Bare ws:/wss: tokens would allow connections to ANY
    host — the H-1 finding that prompted this change.
    """
    client = TestClient(dashboard_app.app)
    r = client.get("/console.html")
    assert r.status_code == 200
    csp = r.headers["Content-Security-Policy"]
    # 'self' must cover connect-src (includes same-origin ws/wss)
    assert "connect-src 'self'" in csp
    # Bare wildcard ws: and wss: must NOT be present
    assert " ws:" not in csp
    assert " wss:" not in csp
    # Standard hardening must remain
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


def test_ce13_release_succeeds_with_session_only_no_step_up() -> None:
    """Release must work even after the 5-min step-up token has expired (M-3 fix).

    Acquire needs step-up; release needs only a valid session so the user can
    always free their own lock even if they idle past the token TTL.
    """
    client, sid = _logged_in_client_with_step_up()
    client.post("/api/console/acquire")
    # Remove the console_token cookie to simulate expiry
    client.cookies.delete("console_token")
    r = client.post("/api/console/release")
    assert r.status_code == 200
    assert r.json()["released"] is True
    assert dashboard_app._console_lock.current_holder() is None


def test_ce24_ws_rate_limited_closes_with_reason() -> None:
    """Active IP lockout must close the WS upgrade with a rate-limited reason (M-1 fix)."""
    import time as _time

    _reset_state()
    sid = dashboard_app._create_session()
    client = TestClient(dashboard_app.app)
    client.cookies.set(dashboard_app._SESSION_COOKIE, sid)
    # Force a lockout for the loopback IP that TestClient uses (127.0.0.1).
    # starlette's TestClient reports client.host as "testclient" (not 127.0.0.1).
    with dashboard_app._rate_lock:
        dashboard_app._rate_state["testclient"] = {
            "attempts": [],
            "fails": [],
            "lockout_until": _time.monotonic() + 60,
        }
    _expect_ws_close_with_reason(client, "rate limit")
    _reset_state()


def test_ce31_index_still_has_strict_csp() -> None:
    """Regression: the per-route override on /console.html must not leak."""
    client = TestClient(dashboard_app.app)
    r = client.get("/")
    csp = r.headers["Content-Security-Policy"]
    # Default CSP has no ws: in connect-src
    assert "connect-src 'self'" in csp
    # Confirms the per-route override didn't replace the default globally
    assert "connect-src 'self' ws:" not in csp
