"""Local-only dev launcher for the dashboard.

Sets a dev `DASHBOARD_TOKEN` if not already in the environment so the dashboard
boots without requiring the production secret on disk. Bind stays on localhost.

NOT for use on the VPS. The VPS sets `DASHBOARD_TOKEN` via systemd-environment.
"""

from __future__ import annotations

import os


def main() -> None:
    os.environ.setdefault("DASHBOARD_TOKEN", "devtoken")
    os.environ.setdefault("DASHBOARD_HOST", "127.0.0.1")
    os.environ.setdefault("DASHBOARD_PORT", "8090")
    # Late import so the env vars above are visible to dashboard module init.
    from dashboard.__main__ import main as run_dashboard

    run_dashboard()


if __name__ == "__main__":
    main()
