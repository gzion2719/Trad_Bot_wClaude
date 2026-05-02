"""Entry point so the dashboard runs as `python -m dashboard`.

Honours these env vars (with defaults):
    DASHBOARD_HOST=127.0.0.1   — never bind 0.0.0.0; reach via Tailscale or SSH tunnel.
    DASHBOARD_PORT=8080
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    uvicorn.run("dashboard.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
