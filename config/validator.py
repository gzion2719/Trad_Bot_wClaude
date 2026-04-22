from __future__ import annotations

"""
Config Validator — Task 2.5

Called as the very first thing in main() before any connection attempt.
Fails fast with a clear error message if the environment is misconfigured,
preventing silent misbehaviour or accidental live trading.

Usage:
    from config.validator import validate_config
    validate_config()   # raises ConfigError if anything is wrong
"""

import logging
import os

from config.settings import IB_HOST, IB_PORT, IB_CLIENT_ID

logger = logging.getLogger(__name__)

# Accepted IBKR API ports:
#   TWS:         7496 (live), 7497 (paper)
#   IB Gateway:  4001 (live default), 4002 (paper default)
# IB Gateway's `OverrideTwsApiPort` can put paper on 4001 too (used in this
# deployment) — paper/live is verified post-connect via account ID prefix.
_VALID_PORTS    = {7496, 7497, 4001, 4002}
_PAPER_PORTS    = {7497, 4002}
_LIVE_ONLY_PORTS = {7496}


class ConfigError(Exception):
    """Raised when a required config value is missing or invalid."""


def validate_config() -> None:
    """
    Validate all required configuration values.

    Checks (in order):
      1. IB_HOST is a non-empty string
      2. IB_PORT is either 7496 (live) or 7497 (paper)
      3. IB_CLIENT_ID is a positive integer
      4. If connecting to the live port, print a loud warning

    Raises:
        ConfigError: For hard failures that must be fixed before running.

    Logs:
        WARNING for live port detection.
        INFO on successful validation.
    """
    errors = []

    # ── Check IB_HOST ───────────────────────────────────────────────────
    if not isinstance(IB_HOST, str) or not IB_HOST.strip():
        errors.append(
            "IB_HOST is empty or not set. "
            "Set IB_HOST=127.0.0.1 in your .env file."
        )

    # ── Check IB_PORT ───────────────────────────────────────────────────
    try:
        port = int(IB_PORT)
    except (TypeError, ValueError):
        errors.append(f"IB_PORT must be an integer, got: {IB_PORT!r}")
        port = None

    if port is not None and port not in _VALID_PORTS:
        errors.append(
            f"IB_PORT={port} is not a recognised IBKR port. "
            f"Use 7497 (TWS paper), 7496 (TWS live), 4002 (Gateway paper), "
            f"or 4001 (Gateway live / paper-with-override)."
        )

    # ── Check IB_CLIENT_ID ──────────────────────────────────────────────
    try:
        client_id = int(IB_CLIENT_ID)
        if client_id < 0:
            errors.append(
                f"IB_CLIENT_ID must be a non-negative integer, got: {client_id}"
            )
    except (TypeError, ValueError):
        errors.append(
            f"IB_CLIENT_ID must be an integer, got: {IB_CLIENT_ID!r}"
        )

    # ── Fail fast if any hard errors ────────────────────────────────────
    if errors:
        msg = "\n".join(f"  • {e}" for e in errors)
        raise ConfigError(
            f"Configuration is invalid — fix these issues in your .env file "
            f"before starting the bot:\n{msg}"
        )

    # ── Live port warning ────────────────────────────────────────────────
    # Only warn on TWS live (7496). Port 4001 is ambiguous — Gateway uses it
    # for live by default but paper configs (like this deployment) can
    # override paper to 4001 via OverrideTwsApiPort. Paper/live is verified
    # post-connect by IBKRClient from the account ID prefix.
    if port in _LIVE_ONLY_PORTS:
        banner = (
            "\n"
            "╔══════════════════════════════════════════════════════╗\n"
            "║         !!!  LIVE TRADING MODE DETECTED  !!!         ║\n"
            "║                                                      ║\n"
            "║  IB_PORT=7496 connects to your REAL money account.   ║\n"
            "║  Real orders will be placed. Real money is at risk.  ║\n"
            "║                                                      ║\n"
            "║  If this is a mistake, set IB_PORT=7497 in .env      ║\n"
            "║  to use the paper trading account instead.           ║\n"
            "╚══════════════════════════════════════════════════════╝"
        )
        logger.warning(banner)

    mode_label = "PAPER" if port in _PAPER_PORTS else ("LIVE" if port in _LIVE_ONLY_PORTS else "AMBIGUOUS")
    logger.info(
        "Config validated | host=%s | port=%s (%s) | client_id=%s",
        IB_HOST, IB_PORT, mode_label, IB_CLIENT_ID,
    )
