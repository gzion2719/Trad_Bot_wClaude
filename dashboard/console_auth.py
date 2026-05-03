"""Step-up auth, single-session lock, and audit log for the gateway console.

This module is intentionally **independent of FastAPI** — pure data structures
and helper functions. The FastAPI integration lives in dashboard/app.py and
binds these primitives to routes.

Why a separate step-up:
    The dashboard session cookie (CR-10) gates "press Restart Bot" — bounded
    blast radius. The gateway console grants full IBKR account control via the
    rendered Java GUI. Cookie alone is insufficient; we require a fresh
    password challenge bound to the session, with a short TTL.

Why a single-session lock:
    Two operators with valid sessions could otherwise both connect to the
    gateway VNC simultaneously and fight over keyboard focus. We accept one
    holder at a time; a second connector gets a 409 with the holder's identity.

Why an audit log:
    Every console session is a security-significant event. We record connect,
    disconnect, idle-timeout, and forced-release with timestamp, session ID
    fingerprint, source IP, and duration. We never log the password or 2FA
    code — those live in the WebSocket bytestream and never touch this process.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Step-up token TTL — long enough to enter a code, short enough to limit
# exposure if the browser tab is left open. 5 minutes matches the lockout
# window from CR-05 for consistency.
STEP_UP_TTL_SECONDS = 300

# Idle timeout for an active console session — VNC bytes from the operator
# reset the timer; absence of input for this long releases the lock.
DEFAULT_IDLE_TIMEOUT_SECONDS = 300


def fingerprint_session(session_id: str) -> str:
    """Return a short, non-reversible fingerprint of a session ID for logging.

    We never write raw session IDs to the audit log — a leaked log file
    would otherwise let an attacker hijack a live session. SHA-256 + first
    16 hex chars is enough to correlate events without exposing the secret.
    """
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


@dataclass
class StepUpToken:
    """A short-lived token proving the operator passed the password challenge."""

    token: str
    session_id: str
    expires_at: float  # time.monotonic() seconds


class StepUpStore:
    """Thread-safe store for step-up tokens.

    Tokens are bound to the issuing dashboard session: a token issued to
    session A cannot be replayed by session B even if both are valid.
    """

    def __init__(self, ttl_seconds: int = STEP_UP_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._tokens: Dict[str, StepUpToken] = {}
        self._lock = threading.Lock()

    def issue(self, session_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._purge_expired_locked(now)
            self._tokens[token] = StepUpToken(
                token=token, session_id=session_id, expires_at=now + self._ttl
            )
        return token

    def validate(self, token: str, session_id: str) -> bool:
        """Return True iff the token is unexpired and bound to *session_id*.

        Uses constant-time comparison to avoid leaking token validity via
        timing.
        """
        if not token or not session_id:
            return False
        now = time.monotonic()
        with self._lock:
            self._purge_expired_locked(now)
            entry = self._tokens.get(token)
            if entry is None:
                return False
            if not hmac.compare_digest(entry.session_id, session_id):
                return False
            return entry.expires_at > now

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def revoke_session(self, session_id: str) -> None:
        """Revoke every step-up token for a given session (e.g. on logout)."""
        with self._lock:
            for tok, entry in list(self._tokens.items()):
                if entry.session_id == session_id:
                    self._tokens.pop(tok, None)

    def _purge_expired_locked(self, now: float) -> None:
        for tok, entry in list(self._tokens.items()):
            if entry.expires_at <= now:
                self._tokens.pop(tok, None)


@dataclass(frozen=True)
class LockHolder:
    session_fingerprint: str
    source_ip: str
    acquired_at: float  # time.monotonic()
    acquired_at_iso: str  # human-readable for UI


class ConsoleSessionLock:
    """Single-holder lock. Second acquirer gets None (caller raises 409)."""

    def __init__(self, idle_timeout: int = DEFAULT_IDLE_TIMEOUT_SECONDS) -> None:
        self._holder: Optional[LockHolder] = None
        self._last_activity: float = 0.0
        self._idle_timeout = idle_timeout
        self._lock = threading.Lock()

    def acquire(
        self, session_fingerprint: str, source_ip: str, acquired_at_iso: str
    ) -> Optional[LockHolder]:
        """Try to acquire the lock. Returns the new holder, or None if held."""
        now = time.monotonic()
        with self._lock:
            self._maybe_release_idle_locked(now)
            if self._holder is not None:
                return None
            holder = LockHolder(
                session_fingerprint=session_fingerprint,
                source_ip=source_ip,
                acquired_at=now,
                acquired_at_iso=acquired_at_iso,
            )
            self._holder = holder
            self._last_activity = now
            return holder

    def release(self, session_fingerprint: str) -> bool:
        """Release the lock if held by *session_fingerprint*. Returns whether released."""
        with self._lock:
            if self._holder is None:
                return False
            if not hmac.compare_digest(self._holder.session_fingerprint, session_fingerprint):
                return False
            self._holder = None
            self._last_activity = 0.0
            return True

    def force_release(self) -> Optional[LockHolder]:
        """Admin-style release without identity check. Returns the prior holder."""
        with self._lock:
            prior = self._holder
            self._holder = None
            self._last_activity = 0.0
            return prior

    def touch(self, session_fingerprint: str) -> bool:
        """Record activity for the holder. Returns True if the lock is theirs."""
        with self._lock:
            if self._holder is None:
                return False
            if not hmac.compare_digest(self._holder.session_fingerprint, session_fingerprint):
                return False
            self._last_activity = time.monotonic()
            return True

    def current_holder(self) -> Optional[LockHolder]:
        now = time.monotonic()
        with self._lock:
            self._maybe_release_idle_locked(now)
            return self._holder

    def _maybe_release_idle_locked(self, now: float) -> None:
        if self._holder is None:
            return
        if now - self._last_activity > self._idle_timeout:
            logger.warning(
                "Console lock auto-released after idle timeout: holder=%s ip=%s",
                self._holder.session_fingerprint,
                self._holder.source_ip,
            )
            self._holder = None
            self._last_activity = 0.0


def audit_log(
    event: str,
    session_fingerprint: str,
    source_ip: str,
    detail: Optional[str] = None,
) -> None:
    """Write a structured line to the standard logger at WARNING level.

    Keeps the audit trail in the same journalctl stream as the dashboard so
    operators only have one place to look. We deliberately do NOT add a
    separate file sink here — that would require ReadWritePaths changes in
    the systemd unit and a logrotate config; out of scope for this module.

    Recorded events: console.step_up.success, console.step_up.failure,
    console.lock.acquired, console.lock.released, console.lock.idle_release,
    console.lock.contended, console.ws.connect, console.ws.disconnect.

    NEVER pass a password, 2FA code, or raw session ID as *detail*. Pass
    fingerprints and structured event metadata only.
    """
    parts = [f"event={event}", f"session={session_fingerprint}", f"ip={source_ip}"]
    if detail:
        parts.append(f"detail={detail}")
    logger.warning("CONSOLE_AUDIT %s", " ".join(parts))
