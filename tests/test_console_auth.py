"""Tests for the console step-up auth, single-session lock, and audit log.

These exercise dashboard/console_auth.py in isolation (no FastAPI), plus the
/api/console/login endpoint and CSP/security headers wired into dashboard/app.py.
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import patch

from starlette.testclient import TestClient

from dashboard import app as dashboard_app
from dashboard.console_auth import (
    ConsoleSessionLock,
    StepUpStore,
    audit_log,
    fingerprint_session,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _reset_rate_state() -> None:
    with dashboard_app._rate_lock:
        dashboard_app._rate_state.clear()


def _client_with_session() -> tuple[TestClient, str]:
    """Return a TestClient with a valid dashboard session cookie installed."""
    _reset_rate_state()
    sid = dashboard_app._create_session()
    client = TestClient(dashboard_app.app)
    client.cookies.set(dashboard_app._SESSION_COOKIE, sid)
    return client, sid


# ── fingerprint ────────────────────────────────────────────────────────────


def test_ca01_fingerprint_is_hex_16() -> None:
    fp = fingerprint_session("some-session-id")
    assert len(fp) == 16
    assert all(c in "0123456789abcdef" for c in fp)


def test_ca02_fingerprint_is_deterministic_and_distinct() -> None:
    a = fingerprint_session("alpha")
    b = fingerprint_session("alpha")
    c = fingerprint_session("beta")
    assert a == b
    assert a != c


# ── StepUpStore ────────────────────────────────────────────────────────────


def test_ca10_step_up_issue_and_validate() -> None:
    store = StepUpStore(ttl_seconds=60)
    tok = store.issue("session-A")
    assert store.validate(tok, "session-A") is True


def test_ca11_step_up_rejects_other_session() -> None:
    """A token issued to session A cannot be replayed by session B."""
    store = StepUpStore(ttl_seconds=60)
    tok = store.issue("session-A")
    assert store.validate(tok, "session-B") is False


def test_ca12_step_up_rejects_expired() -> None:
    store = StepUpStore(ttl_seconds=1)
    tok = store.issue("session-A")
    time.sleep(1.1)
    assert store.validate(tok, "session-A") is False


def test_ca13_step_up_revoke() -> None:
    store = StepUpStore(ttl_seconds=60)
    tok = store.issue("session-A")
    store.revoke(tok)
    assert store.validate(tok, "session-A") is False


def test_ca14_step_up_revoke_session() -> None:
    store = StepUpStore(ttl_seconds=60)
    t1 = store.issue("session-A")
    t2 = store.issue("session-A")
    t3 = store.issue("session-B")
    store.revoke_session("session-A")
    assert store.validate(t1, "session-A") is False
    assert store.validate(t2, "session-A") is False
    assert store.validate(t3, "session-B") is True


def test_ca14b_step_up_reissue_revokes_prior_token() -> None:
    """Re-issuing a step-up token must invalidate the previous one (M-4 fix)."""
    store = StepUpStore(ttl_seconds=60)
    old_tok = store.issue("session-A")
    _new_tok = store.issue("session-A")
    assert store.validate(old_tok, "session-A") is False


def test_ca15_step_up_validate_rejects_blank() -> None:
    store = StepUpStore(ttl_seconds=60)
    assert store.validate("", "session-A") is False
    assert store.validate("anything", "") is False


# ── ConsoleSessionLock ─────────────────────────────────────────────────────


def test_ca20_lock_acquire_first_holder() -> None:
    lock = ConsoleSessionLock(idle_timeout=60)
    holder = lock.acquire("fp-A", "10.0.0.1", "2026-05-03T14:00:00Z")
    assert holder is not None
    assert holder.session_fingerprint == "fp-A"
    assert lock.current_holder() is not None


def test_ca21_lock_second_acquirer_blocked() -> None:
    lock = ConsoleSessionLock(idle_timeout=60)
    assert lock.acquire("fp-A", "10.0.0.1", "now") is not None
    assert lock.acquire("fp-B", "10.0.0.2", "now") is None


def test_ca22_lock_release_by_holder_succeeds() -> None:
    lock = ConsoleSessionLock(idle_timeout=60)
    lock.acquire("fp-A", "ip", "now")
    assert lock.release("fp-A") is True
    assert lock.current_holder() is None


def test_ca23_lock_release_by_non_holder_fails() -> None:
    lock = ConsoleSessionLock(idle_timeout=60)
    lock.acquire("fp-A", "ip", "now")
    assert lock.release("fp-B") is False
    assert lock.current_holder() is not None


def test_ca24_lock_idle_auto_release() -> None:
    """Idle timeout releases the lock so the next caller can acquire."""
    lock = ConsoleSessionLock(idle_timeout=1)  # 1s for fast test
    lock.acquire("fp-A", "ip", "now")
    time.sleep(1.1)
    # current_holder() purges idle-expired holder before reading
    assert lock.current_holder() is None
    # And a fresh acquire now succeeds
    assert lock.acquire("fp-B", "ip2", "later") is not None


def test_ca25_lock_touch_resets_idle_timer() -> None:
    lock = ConsoleSessionLock(idle_timeout=2)
    lock.acquire("fp-A", "ip", "now")
    time.sleep(1.0)
    assert lock.touch("fp-A") is True
    time.sleep(1.0)
    # 2 seconds total elapsed but touch reset the timer at +1s, so still held
    assert lock.current_holder() is not None


def test_ca26_lock_touch_by_non_holder_fails() -> None:
    lock = ConsoleSessionLock(idle_timeout=60)
    lock.acquire("fp-A", "ip", "now")
    assert lock.touch("fp-B") is False


def test_ca27_lock_force_release_returns_prior_holder() -> None:
    lock = ConsoleSessionLock(idle_timeout=60)
    lock.acquire("fp-A", "ip", "now")
    prior = lock.force_release()
    assert prior is not None
    assert prior.session_fingerprint == "fp-A"
    assert lock.current_holder() is None


# ── audit_log ──────────────────────────────────────────────────────────────


def test_ca30_audit_log_emits_warning(caplog: Any) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="dashboard.console_auth"):
        audit_log("console.test", "fp-A", "10.0.0.1", detail="extra")
    assert any("CONSOLE_AUDIT" in r.message for r in caplog.records)
    assert any("event=console.test" in r.message for r in caplog.records)
    assert any("session=fp-A" in r.message for r in caplog.records)
    assert any("ip=10.0.0.1" in r.message for r in caplog.records)


# ── /api/console/login endpoint ────────────────────────────────────────────


def test_ca40_console_login_requires_session() -> None:
    """No dashboard session cookie → 401."""
    _reset_rate_state()
    client = TestClient(dashboard_app.app)
    with patch.dict(os.environ, {"DASHBOARD_CONSOLE_PASSWORD": "secret"}):
        r = client.post("/api/console/login", json={"password": "secret"})
    assert r.status_code == 401


def test_ca41_console_login_503_when_password_unset() -> None:
    client, _sid = _client_with_session()
    with patch.dict(os.environ, {"DASHBOARD_CONSOLE_PASSWORD": ""}, clear=False):
        os.environ.pop("DASHBOARD_CONSOLE_PASSWORD", None)
        r = client.post("/api/console/login", json={"password": "anything"})
    assert r.status_code == 503


def test_ca42_console_login_rejects_bad_password() -> None:
    client, _sid = _client_with_session()
    with patch.dict(os.environ, {"DASHBOARD_CONSOLE_PASSWORD": "correct-horse"}):
        r = client.post("/api/console/login", json={"password": "wrong"})
    assert r.status_code == 401


def test_ca43_console_login_success_sets_console_token_cookie() -> None:
    client, _sid = _client_with_session()
    with patch.dict(os.environ, {"DASHBOARD_CONSOLE_PASSWORD": "correct-horse"}):
        r = client.post("/api/console/login", json={"password": "correct-horse"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["expires_in"] == 300
    # HttpOnly cookies aren't returned by TestClient.cookies, but Set-Cookie is in headers.
    set_cookie = r.headers.get("set-cookie", "")
    set_cookie_lower = set_cookie.lower()
    assert "console_token=" in set_cookie
    assert "httponly" in set_cookie_lower
    assert "samesite=strict" in set_cookie_lower


# ── Security headers / CSP middleware ──────────────────────────────────────


def test_ca50_security_headers_present_on_index() -> None:
    client = TestClient(dashboard_app.app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Content-Security-Policy" in r.headers
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_ca51_csp_disallows_inline_script_unsafe() -> None:
    """Defensive: CSP must not weaken to 'unsafe-inline'."""
    client = TestClient(dashboard_app.app)
    r = client.get("/")
    csp = r.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


# ── /api/system surfaces lock holder ───────────────────────────────────────


def test_ca60_api_system_reports_console_held_when_locked() -> None:
    """Lock acquisition should surface in /api/system for the UI banner."""
    # Force-release any prior holder to keep test isolated.
    dashboard_app._console_lock.force_release()
    client = TestClient(dashboard_app.app)
    r0 = client.get("/api/system")
    assert r0.status_code == 200
    assert r0.json()["console_held_by"] is None

    dashboard_app._console_lock.acquire("fp-test", "10.0.0.99", "2026-05-03T15:00:00Z")
    try:
        r1 = client.get("/api/system")
        body = r1.json()
        assert body["console_held_by"] == "fp-test"
        assert body["console_held_since"] == "2026-05-03T15:00:00Z"
    finally:
        dashboard_app._console_lock.force_release()


# ── Logout invalidates step-up tokens and lock ─────────────────────────────


def test_ca70_logout_revokes_step_up_and_releases_lock() -> None:
    """Logging out must invalidate step-up tokens AND release the console lock."""
    _reset_rate_state()
    sid = dashboard_app._create_session()
    fp = fingerprint_session(sid)
    tok = dashboard_app._step_up_store.issue(sid)
    dashboard_app._console_lock.force_release()
    dashboard_app._console_lock.acquire(fp, "ip", "now")

    client = TestClient(dashboard_app.app)
    client.cookies.set(dashboard_app._SESSION_COOKIE, sid)
    r = client.post("/api/logout")
    assert r.status_code == 200

    assert dashboard_app._step_up_store.validate(tok, sid) is False
    assert dashboard_app._console_lock.current_holder() is None


# All tests in this file are dashboard-only — no IBKR connection required.
