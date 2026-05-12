"""yfinance outage report — read tradebot journalctl, summarize history-refresh outages.

Run on the VPS:
    python3 scripts/yfinance_outage_report.py [--days N]

Counts `_refresh_history` outages by parsing the strategy's existing log lines:
  - "history refresh failed (consecutive=N)" — one per failed tick
  - "history refresh recovered after N consecutive failures" — one per outage end

The strategy ticks once per trading day at 16:10 ET, so each failure ≈ one
trading day missed. This script summarizes a window so the operator can decide
whether MS-C2 (IBKR fallback) is worth building.

Decision is the operator's, not this script's — it only surfaces the numbers.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone

_RECOVERY_RE = re.compile(r"history refresh recovered after (\d+) consecutive failures")
_FAILURE_RE = re.compile(r"history refresh failed \(consecutive=(\d+)\)")


def _run_journalctl(days: int) -> str:
    since = f"{days} days ago"
    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                "tradebot",
                "--since",
                since,
                "--no-pager",
                "--output=short-iso",
                # Pre-filter on the journald side so we read ~60 lines per
                # month instead of millions. The two log lines we parse both
                # contain the literal phrase "history refresh".
                "--grep",
                "history refresh",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError:
        sys.exit("error: journalctl not found — run this on the VPS, not the dev PC")
    # journalctl --grep exits 1 when zero lines match (grep semantics).
    # That's the expected state when yfinance is healthy. journalctl also
    # prints a "-- No entries --" banner on stdout in that case, so we
    # cannot distinguish "no matches" from "real error" by stdout-emptiness.
    # The downstream parser silently skips any line that doesn't start with
    # an ISO timestamp, so returning the banner is harmless.
    if result.returncode in (0, 1):
        return result.stdout
    sys.exit(f"error: journalctl exited {result.returncode}: {result.stderr.strip()}")


def _parse_iso_ts(line: str) -> datetime | None:
    # short-iso format starts with e.g. "2026-05-11T19:22:35+0000 host ..."
    head = line.split(" ", 1)[0]
    try:
        return datetime.fromisoformat(head)
    except ValueError:
        return None


def _check_window(output: str, requested_days: int) -> None:
    """Warn if journal retention truncates the window."""
    lines = output.splitlines()
    if not lines:
        print("warning: no tradebot journal entries found at all", file=sys.stderr)
        return
    first_ts = _parse_iso_ts(lines[0])
    if first_ts is None:
        return
    now = datetime.now(timezone.utc).astimezone(first_ts.tzinfo)
    actual = (now - first_ts).days
    if actual < requested_days - 1:
        print(
            f"warning: journal only goes back {actual} days "
            f"(requested {requested_days}) — outages older than that are invisible",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=30, help="window in days (default: 30)")
    args = parser.parse_args()

    output = _run_journalctl(args.days)
    _check_window(output, args.days)

    recoveries: list[tuple[datetime, int]] = []
    last_failure_ts: datetime | None = None
    last_failure_count = 0

    for line in output.splitlines():
        ts = _parse_iso_ts(line)
        if ts is None:
            continue
        m_rec = _RECOVERY_RE.search(line)
        if m_rec:
            recoveries.append((ts, int(m_rec.group(1))))
            last_failure_ts = None
            last_failure_count = 0
            continue
        m_fail = _FAILURE_RE.search(line)
        if m_fail:
            last_failure_ts = ts
            last_failure_count = int(m_fail.group(1))

    total_outage_days = sum(n for _, n in recoveries)
    longest = max((n for _, n in recoveries), default=0)

    print(f"Window: last {args.days} days")
    print(f"Outages recovered: {len(recoveries)}")
    print(f"Total outage-days: {total_outage_days}")
    print(f"Longest outage: {longest} consecutive ticks")
    if recoveries:
        print("\nRecoveries (most recent first):")
        for ts, n in reversed(recoveries):
            print(f"  {ts.isoformat()} — {n} consecutive failures")
    if last_failure_ts is not None:
        print(
            f"\nOngoing outage: {last_failure_count} consecutive failures "
            f"as of {last_failure_ts.isoformat()} (no recovery yet)"
        )
    if not recoveries and last_failure_ts is None:
        print("\nNo outages in window — yfinance was clean.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
