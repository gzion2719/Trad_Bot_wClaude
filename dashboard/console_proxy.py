"""WebSocket reverse proxy: dashboard /ws/console <-> websockify 127.0.0.1:6080.

Why a reverse proxy instead of exposing websockify directly:
    All auth lives in the dashboard. websockify never sees the Tailnet —
    it only accepts connections from this process on localhost. A single
    point of authentication (cookie + step-up + lock + origin) keeps the
    threat model simple.

Why bidirectional bytes-only:
    noVNC speaks Remote Framebuffer protocol over WebSocket. We do not
    parse the frames; we just relay bytes. This keeps the proxy small
    and avoids becoming a man-in-the-middle for the 2FA code or password
    that the operator types — those bytes flow through us as opaque RFB
    frames and never touch any Python string we log.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Subprotocol

logger = logging.getLogger(__name__)

# Local websockify endpoint. Hard-coded because the websockify systemd unit
# binds to this exact address. If the unit changes, change both.
WEBSOCKIFY_URI = "ws://127.0.0.1:6080/"

# Per-direction relay buffer cap — defends against runaway memory if one side
# stalls. RFB frames are bounded; this is generous.
_RELAY_CHUNK_SIZE = 64 * 1024


async def _relay_browser_to_upstream(browser_ws: WebSocket, upstream_ws: Any) -> None:
    """Forward bytes from the browser to websockify until either side closes."""
    try:
        while True:
            # FastAPI's receive() returns dict; we want the raw bytes/text
            msg = await browser_ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if "bytes" in msg and msg["bytes"] is not None:
                await upstream_ws.send(msg["bytes"])
            elif "text" in msg and msg["text"] is not None:
                await upstream_ws.send(msg["text"])
    except (WebSocketDisconnect, ConnectionClosed):
        return


async def _relay_upstream_to_browser(upstream_ws: Any, browser_ws: WebSocket) -> None:
    """Forward bytes from websockify to the browser until either side closes."""
    try:
        async for chunk in upstream_ws:
            if isinstance(chunk, bytes):
                await browser_ws.send_bytes(chunk)
            else:
                await browser_ws.send_text(chunk)
    except (WebSocketDisconnect, ConnectionClosed):
        return


async def proxy_console_websocket(
    browser_ws: WebSocket,
    on_activity: Optional[Callable[[str], None]] = None,
) -> None:
    """Bridge a browser WebSocket to websockify on 127.0.0.1:6080.

    Caller is responsible for accepting the WebSocket and verifying auth
    BEFORE calling this — by the time we get here we're committed to a
    bytes-only relay.

    on_activity, if provided, is called once when the proxy starts (so the
    caller can record an audit-log connect event) and once when it ends
    (for the disconnect event). The caller passes a closure that knows
    the session fingerprint and source IP.
    """
    upstream_ws = None
    try:
        # Connect to websockify. The 'binary' subprotocol is what noVNC speaks.
        upstream_ws = await websockets.connect(  # type: ignore[arg-type]
            WEBSOCKIFY_URI,
            subprotocols=[Subprotocol("binary")],
            max_size=2 * 1024 * 1024,  # generous upper bound on RFB frames
        )
    except (OSError, ConnectionClosed) as exc:
        logger.error("Console proxy: failed to connect to websockify: %s", exc)
        await browser_ws.close(code=1011, reason="upstream unavailable")
        return

    if on_activity:
        on_activity("connect")

    try:
        # Run both relay directions concurrently; first to finish closes both.
        relay_b2u = asyncio.create_task(_relay_browser_to_upstream(browser_ws, upstream_ws))
        relay_u2b = asyncio.create_task(_relay_upstream_to_browser(upstream_ws, browser_ws))
        done, pending = await asyncio.wait(
            [relay_b2u, relay_u2b], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    finally:
        if upstream_ws is not None:
            try:
                await upstream_ws.close()
            except Exception:  # noqa: BLE001 — closing best-effort
                pass
        if on_activity:
            on_activity("disconnect")
