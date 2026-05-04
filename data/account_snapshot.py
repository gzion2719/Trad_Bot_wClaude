"""Account snapshot writer + reader.

The bot writes data/account_snapshot.json every SNAPSHOT_INTERVAL_SECONDS and
appends one line to data/equity_history_YYYY-MM-DD.jsonl per snapshot. The
dashboard reads these files. No shared memory, no IB connection from the
dashboard process — file IPC only (mirrors data/health.txt).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class _IBClient(Protocol):
    """Structural protocol for the IB client used by AccountSnapshotPoller."""

    @property
    def account(self) -> str: ...

    def get_account_summary_threadsafe(self) -> list: ...

    def get_positions_threadsafe(self) -> list: ...


SNAPSHOT_INTERVAL_SECONDS = 30
RETENTION_DAYS = 365
PRUNE_INTERVAL_SECONDS = 3600
SCHEMA_VERSION = 1
SNAPSHOT_FILENAME = "account_snapshot.json"
EQUITY_FILENAME_PREFIX = "equity_history_"

# Required summary tags → snapshot key
_REQUIRED_TAGS: dict[str, str] = {
    "NetLiquidation": "net_liquidation",
    "SettledCash": "settled_cash",
    "UnrealizedPnL": "unrealized_pnl",
    "RealizedPnL": "realized_pnl",
    "MaintMarginReq": "maintenance_margin",
    "ExcessLiquidity": "excess_liquidity",
    "BuyingPower": "buying_power",
    "AvailableFunds": "available_funds",
    "TotalCashValue": "cash",
    "EquityWithLoanValue": "equity_with_loan",
    "InitMarginReq": "initial_margin",
}

# Optional summary tags → snapshot key
_OPTIONAL_TAGS: dict[str, str] = {
    "PreviousDayEquityWithLoanValue": "previous_day_ewl",
    "RegTEquity": "regulation_t_ewl",
    "SMA": "sma",
    "Leverage": "leverage",
    "GrossPositionValue": "gross_position_value",
}


def _safe_float(value: str) -> Optional[float]:
    """Convert string to float; return None on any failure."""
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (ValueError, TypeError):
        return None


def _build_summary(account_values: list) -> dict:
    """Build the summary dict from a list of AccountValue objects."""
    by_tag: dict[str, str] = {}
    for av in account_values:
        # Filter to USD currency only; some tags lack a currency field
        currency = getattr(av, "currency", "USD")
        if currency and currency != "USD":
            continue
        by_tag[av.tag] = av.value

    summary: dict[str, Optional[float]] = {}
    for tag, key in _REQUIRED_TAGS.items():
        summary[key] = _safe_float(by_tag.get(tag, ""))
    for tag, key in _OPTIONAL_TAGS.items():
        val = by_tag.get(tag)
        summary[key] = _safe_float(val) if val is not None else None
    return summary


def _build_positions(portfolio_items: list) -> list[dict]:
    """Build positions list from PortfolioItem objects."""
    result = []
    for item in portfolio_items:
        symbol = item.contract.symbol
        description = getattr(item.contract, "description", "") or symbol
        result.append(
            {
                "symbol": symbol,
                "name": description,
                "position": int(item.position),
                "market_value": float(item.marketValue),
                "avg_cost": float(item.averageCost),
                "market_price": float(item.marketPrice),
                "unrealized_pnl": float(item.unrealizedPNL),
                "realized_pnl": float(item.realizedPNL),
            }
        )
    return result


def _prune_old_files(data_dir: Path, retention_days: int) -> None:
    """Delete equity history files older than retention_days before today UTC."""
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=retention_days)
    for path in data_dir.glob(f"{EQUITY_FILENAME_PREFIX}*.jsonl"):
        date_str = path.stem[len(EQUITY_FILENAME_PREFIX) :]
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                path.unlink()
                logger.info("Pruned old equity file: %s", path.name)
            except OSError as exc:
                logger.warning("Could not delete %s: %s", path, exc)


def read_snapshot(data_dir: Path) -> dict:
    """Read and validate account_snapshot.json.

    Returns the snapshot dict with added fields:
      status: "ok" | "unreadable" | "missing"
      age_seconds: float (seconds since captured_at), or None on failure
    """
    snap_file = data_dir / SNAPSHOT_FILENAME
    if not snap_file.exists():
        return {"status": "missing", "age_seconds": None}

    try:
        data = json.loads(snap_file.read_text(encoding="utf-8"))
        if data.get("v") != SCHEMA_VERSION:
            raise ValueError(
                f"schema version mismatch: got {data.get('v')}, expected {SCHEMA_VERSION}"
            )
        captured_at = datetime.fromisoformat(data["captured_at"])
        age = (datetime.now(timezone.utc) - captured_at).total_seconds()
        data["age_seconds"] = round(age, 1)
        data["status"] = "ok"
        return data
    except Exception as exc:
        logger.warning("read_snapshot: could not parse %s: %s", snap_file, exc)
        return {"status": "unreadable", "age_seconds": None}


def read_equity_history(data_dir: Path, days: int) -> list[dict]:
    """Concatenate per-day equity files spanning [today-days, today] (UTC).

    Returns list of {"t": str, "net_liq": float}. Skips unparseable lines.
    """
    today = datetime.now(timezone.utc).date()
    points: list[dict] = []
    for offset in range(days, -1, -1):
        target_date = today - timedelta(days=offset)
        path = data_dir / f"{EQUITY_FILENAME_PREFIX}{target_date}.jsonl"
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if "t" in obj and "net_liq" in obj:
                        points.append({"t": obj["t"], "net_liq": float(obj["net_liq"])})
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    logger.warning(
                        "read_equity_history: skipping bad line in %s: %s", path.name, exc
                    )
        except OSError as exc:
            logger.warning("read_equity_history: could not read %s: %s", path, exc)
    return points


def downsample(points: list[dict], max_points: int = 2000) -> list[dict]:
    """Bucketed-mean downsampling.

    If len(points) <= max_points, return as-is.
    Otherwise group into ceil(len/max_points)-sized buckets, outputting one
    point per bucket: t = first.t, net_liq = mean(net_liqs).
    """
    if len(points) <= max_points:
        return points
    bucket_size = math.ceil(len(points) / max_points)
    result: list[dict] = []
    for i in range(0, len(points), bucket_size):
        bucket = points[i : i + bucket_size]
        t = bucket[0]["t"]
        net_liq = sum(p["net_liq"] for p in bucket) / len(bucket)
        result.append({"t": t, "net_liq": net_liq})
    return result


class AccountSnapshotPoller(threading.Thread):
    """Daemon thread: writes account_snapshot.json and equity history every interval."""

    def __init__(
        self,
        client: _IBClient,
        data_dir: Path,
        interval: float = SNAPSHOT_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(name="AccountSnapshotPoller", daemon=True)
        self._client = client
        self._data_dir = data_dir
        self._interval = interval
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._last_prune: float = 0.0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("AccountSnapshotPoller started — writing every %ss.", self._interval)
        while not self._stop_event.is_set():
            try:
                self._capture_and_write()
            except Exception as exc:
                logger.warning(
                    "AccountSnapshotPoller: capture error (non-fatal): %s", exc, exc_info=True
                )

            now = time.monotonic()
            if now - self._last_prune >= PRUNE_INTERVAL_SECONDS:
                try:
                    _prune_old_files(self._data_dir, RETENTION_DAYS)
                    self._last_prune = now
                except Exception as exc:
                    logger.warning(
                        "AccountSnapshotPoller: prune error (non-fatal): %s", exc, exc_info=True
                    )

            self._stop_event.wait(timeout=self._interval)

    def _capture_and_write(self) -> None:
        account_values = self._client.get_account_summary_threadsafe()
        portfolio_items = self._client.get_positions_threadsafe()

        captured_at = datetime.now(timezone.utc)
        assert captured_at.tzinfo is not None, "captured_at must be tz-aware"

        summary = _build_summary(account_values)
        snapshot: dict = {
            "v": SCHEMA_VERSION,
            "captured_at": captured_at.isoformat(),
            "account": self._client.account,
            "summary": summary,
            "positions": _build_positions(portfolio_items),
        }

        snap_file = self._data_dir / SNAPSHOT_FILENAME
        tmp_file = self._data_dir / f"{SNAPSHOT_FILENAME}.tmp"
        payload = json.dumps(snapshot, separators=(",", ":"))

        with self._write_lock:
            tmp_file.write_text(payload, encoding="utf-8")
            os.replace(str(tmp_file), str(snap_file))

            equity_line = json.dumps(
                {
                    "t": captured_at.isoformat(),
                    "net_liq": summary.get("net_liquidation"),
                },
                separators=(",", ":"),
            )
            equity_file = self._data_dir / f"{EQUITY_FILENAME_PREFIX}{captured_at.date()}.jsonl"
            with equity_file.open("a", encoding="utf-8") as fh:
                fh.write(equity_line + "\n")
