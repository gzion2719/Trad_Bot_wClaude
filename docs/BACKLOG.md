# TradeBot — Backlog

Categorized list of open items. Updated every 5 sessions during the hygiene review.
For sprint-by-sprint detail, see `TODO.md`. For the phased roadmap, see `docs/ROADMAP.md`.

---

## Dashboard — Phase 4+ (UI & Analytics)

| # | Priority | Item |
|---|----------|------|
| DB-P4-1 | P1 | Account balance card — live NetLiquidation + UnrealizedPnL from `/api/system` (extend backend to query `client.get_account_summary()`) + equity curve graph |
| DB-P4-2 | ✅ | DONE 2026-05-12 (Session 1): Strategy column added to Recent fills table on Mission Control. |
| DB-P4-3 | ▶ | IN PROGRESS — Session 1 of 3 done 2026-05-12: backend endpoints `/api/strategies`, `/api/strategies/{name}/summary`, `/api/strategies/{name}/fills` shipped. Session 2: Strategies top-tab + secondary tabs + KPI strip + paginated history table + CSV stream. Session 3: Realized-P&L-history chart + Live-state card. |
| DB-P4-4 | P2 | UI redesign — rethink card layout, typography, and color system for a more professional look; consider sidebar nav for multi-strategy view |
| DB-X4 | P3 | Per-strategy cache key for `/api/strategies/{name}/summary`. Today cache is keyed on global `MAX(id) FROM trades`, so any strategy's fill busts all caches. Negligible with 2 strategies; revisit when N≥5 or fill volume grows. |
| DB-X5 | ✅ | DONE 2026-05-14: shared TestClient fixtures (`dashboard_token`, `dashboard_client`, `dashboard_client_unauth`) live in `tests/conftest.py`. Retrofitted 13 callers in `test_dashboard.py` + ds28; added ds50..54 covering 401 paths on the per-strategy endpoints. |
| DB-X6 | P3 | Restore `StrategyConfig` frozen-ness via `@dataclass(frozen=True, slots=True)`. Was lost when we switched from `@dataclass(frozen=True)` to a `__slots__` class to support dual constructor shapes. No active code mutates the object; defensive only. |
| DB-X7 | P2 | Decide breakeven-trade semantics for win-rate. Today `realized_pnl == 0` is excluded from both wins and losses (silent). Pick one: count as loss (conservative), bucket separately, or document the exclusion. Decision drives the docstring + tests + UI label. |
| DB-X8 | P3 | Replace OFFSET pagination with keyset pagination in `/api/strategies/{name}/fills` if fill volume ever exceeds the 10k offset cap. SQLite OFFSET is O(N) — keyset (`WHERE id < last_id`) is O(log N). |
| DB-X9 | P3 | Parallel `+inf` bug in `backtester/metrics.py:193 summary()` — `metrics["profit_factor"] = round(_pf, 3)` keeps `+inf` unchanged for only-wins backtests. Currently console-printed only (the printer at L209 special-cases `isinf`), so the JSON-wire bug doesn't surface. If a future endpoint ever serializes a backtest summary dict, it will hit the same FastAPI `+inf → null` silent conversion fixed for `_round_profit_factor` on 2026-05-13. Mirror the string-sentinel fix when that endpoint lands. |
| DB-X10 | P3 | Server↔JS column-key mirror test for `/api/strategies/{name}/fills`. `ds69` locks JS `_STRAT_HISTORY_COLS` against the static `<thead>` and `colspan` literal, but a server-side rename (e.g. `realized_pnl → pnl_realized`) would silently render `—` in every row with no test failure. Add a fixture-driven test that fetches one real fills response and asserts every JS column key is present on the response shape (or in an explicit exclusion list). |
| DB-M4 | P3 | Replace `title` attribute on the per-strategy history `params` cell with click-to-expand (or `<details>`). `title` tooltips are keyboard-inaccessible — only mouse-hover triggers them — so keyboard users with `Tab` get nothing. Single-user dashboard, but worth fixing for hand-off to teammates. |

---

## Gateway Console — Phase 2 polish (post-MVP)

| # | Priority | Item |
|---|----------|------|
| GC-1 | ✅ | DONE 2026-05-04: button is always visible in Controls card; clicking opens /console.html as a sized OS popup (window.open). Static regression test guards the popup-features string. |
| GC-2 | ✅ | DONE 2026-05-04: full 2FA login rehearsal completed via browser console; gateway logged in, bot reconnected. |
| GC-3 | P0 | Security review pass: re-audit /api/console/login rate limiter, step-up token expiry, lock idle timeout, audit-log completeness, CSP scope on /console.html |
| GC-4 | P1 | TLS for the dashboard so noVNC works without an SSH tunnel — Caddy or nginx in front of 8080 with self-signed cert (Tailscale) or Let's Encrypt + tailscale-cert. Removes the localhost-only secure-context workaround. |
| GC-5 | P2 | Console UI redesign — current page is a bare canvas + minimal header. Match the Mission Control look: header with status pill, footer hint, restyled step-up card, scaling indicator. |

---

## Infrastructure & Ops

| # | Priority | Item |
|---|----------|------|
| 5.7 | P2 | Monitoring dashboard (simple web UI or Grafana) |
| 5.9 | P1 | IBKR Trusted IP — add VPS IP `2.24.222.199` in account settings → Security → Trusted IPs |
| 5.16 | P1 | Send IBKR support inquiry: (a) switch from Interactive IL Key to push-notification IB Key? (b) any unattended weekly auth path for paper accounts? |
| 6.4 | P0 | Confirm bot recovers from Sunday 2FA reset (first test: 2026-05-03 ~09:00 IL time) |
| 6.7 | P2 | Research alternative market data APIs (Polygon.io, Alpaca, FMP) |

---

## Strategy & Backtesting

| # | Priority | Item |
|---|----------|------|
| 4.5 | P1 | Tune SMA parameters after 1+ week paper results — test sma_fast=20/sma_slow=50; validate on 2008/2022 bear regimes |
| 4.6 | P2 | Implement and backtest a second strategy |
| 4.7 | P2 | Strategy parameter management (YAML/JSON config, no code changes to switch params) |
| 4.8 | P1 | Multi-strategy runner — Decision B resolved 2026-05-06: **independent, 2% per strategy, each trade is separate**. Ready to implement when second strategy design is ready. |
| 6.1 | P0 | Monitor TradeLog.daily_summary() every trading day — check realized_pnl, trade count, fill quality |
| 6.2 | P0 | Verify fills at expected prices (compare backtest vs paper fills) |
| 6.8 | P2 | Build RESOURCES.md with vetted sources for strategies, risk management, market microstructure |
| M7 | P1 | Validate strategy on 2008/2022 bear regimes before going live |

---

## Risk & Monitoring

| # | Priority | Item |
|---|----------|------|
| 6.3 | P0 | Verify daily loss ceiling triggers correctly via simulated loss |
| 6.5 | P1 | Continue weekly log review for WARNING/ERROR patterns |
| 6.6 | P1 | Adjust risk caps (max_order, max_position, max_daily_loss) based on paper results |
| Q4 | P2 | If avg_cost == 0 on reconcile, defer `_in_position=True` until stop can be computed |
| Q6a | P2 | Consider auto-re-placing STOP in `_exit()` when SELL is rejected |
| 2.7 | P2 | Alert system (email/Slack) on fill, daily loss breach, and error codes |
| MS-A | ✅ | DONE 2026-05-09: A1 (cost_basis pipeline) + A2 (per-strategy PnLPoller wiring + sticky halt). PnLPoller now queries TradeLog per strategy; each RiskManager halts independently. The bug-of-record (one strategy's loss halting all others) is fixed and tested in `test_a2_09_independent_halt_one_strategy_breach_other_keeps_trading`. |
| MS-B | ✅ | DONE 2026-05-10: `RSI2MR_SPY._get_strategy_attributed_equity()` returns `initial_capital + own realized P&L (TradeLog) + unrealized on open position`; used at the two CB sites (peak ratchet + 8% drawdown trip). Position sizing keeps using broker NetLiq (`_get_equity()`) — Decision B is independent caps, not separate equity bases. State schema bumped to v2 with one-shot reset of contaminated peak/CB on first load. 12 new tests `test_msb_01..12`. CR-fix pass addressed H1 (state migration), L2 (test isolation), L4 (coverage). |
| MS-C | ✅ | DONE 2026-05-11: ntfy alert on persistent `_refresh_history` failures with asymmetric thresholds — held position pages on the 1st failure (exit checks are blind during the outage), flat strategy on the 2nd. One alert per outage; counter and latch reset on success. In-memory counter (NOT persisted) — restart-during-outage produces at worst a duplicate alert, not a missed one; avoided bumping state schema to v3. 6 new tests `test_msc_01..06`. |
| MS-C2 | P2 (measurement-gated) | IBKR `reqHistoricalData` fallback for `_refresh_history` — design item, NOT a 1-hour drop-in. yfinance uses `auto_adjust=True` (split/dividend adjusted closes) while IBKR `what_to_show="TRADES"` returns unadjusted prices; mixing them mid-session would silently corrupt SMA(200) and RSI(2) across any split. Real fix needs `what_to_show="ADJUSTED_LAST"` (different IBKR semantics, paper-account entitlement unverified, and per 2026-05-12 unbiased CR `ADJUSTED_LAST` does NOT match yfinance dividend handling — design needs a spike before any commit). Until then, MS-C alerts on persistent outage. **Deferred pending measurement (2026-05-12):** before designing/building, run `python3 scripts/yfinance_outage_report.py` on the VPS on or after 2026-06-12 to count actual `_refresh_history` outages in the prior 30 days. Operator sets the build threshold after seeing the first number. If outages are rare → close as won't-build; if frequent → re-open the design conversation with real data. |
| MS-C3 | ✅ | DONE 2026-05-11: `VIXFeed` now has a consecutive-failure counter + latch firing a fetch-failure ntfy alert at threshold=2 (separate from the existing stale-cache alert via independent cooldowns — the more serious "entry blocked" stale alert cannot be silenced by an earlier transient fetch-failure alert). Empty-DataFrame returns from yfinance are now treated as failures (was silent fallthrough). INFO "yfinance fetch recovered after N consecutive failures" line emitted on success after >0 failures. Gap (a) `_last_ntfy_at` in-memory tradeoff documented inline; persisting deferred to MS-C3-persist. 9 new tests `test_msc3_01..09`. |
| MS-C3-persist | P3 | Persist `_last_ntfy_at_stale` and `_last_ntfy_at_fetch_failure` so a restart during a multi-day VIX outage doesn't reset the 24h cooldown and re-fire on first failure post-restart. Today (MS-C3) the tradeoff matches MS-C's in-memory pattern: worst case is one duplicate alert per restart, never a missed alert. Worth persisting only if duplicate alerts become a real annoyance. |
| MS-D | ✅ | DONE 2026-05-09: `config/strategies._validate_registry()` raises `ConfigError` at module load on empty registry, blank/duplicate names, or shared symbols (case-insensitive). `StrategyRunner._validate_registry()` delegates to it. Caveat: narrows but does not close MS-A1's `avg_cost` ambiguity — manual same-symbol trades outside the bot still confound an account-level cost basis lookup. |
| MS-D-ext | P3 | Extend MS-D symbol key to include `exchange` and/or `contract_type` once `StrategyConfig` grows those fields, so SPY-stock vs SPY-option (or SPY/SMART vs SPY/ARCA) are not collapsed into one collision. Today `StrategyConfig.symbol` is a bare ticker string and the guard normalizes via `.strip().upper()`. (Surfaced 2026-05-09 MS-D plan review M1.) |
| MS-G | P2 | Periodic NULL `realized_pnl` detection inside the poll loop — currently `count_null_pnl_since` is checked only at startup. NULLs that land AFTER startup (e.g., a strategy SELL with `_entry_price=0` due to a race) silently sum to 0 in the daily attribution. Add a low-frequency runtime check (every N polls) that re-runs the count and surfaces a WARNING. (Surfaced 2026-05-09 MS-A2 third-pass CR.) |
| MS-H | P3 | End-to-end DST integration test — A2.06/A2.07 verify only the cutoff helper. Add a `record(submitted_at on DST date) → realized_pnl_since(DST cutoff)` round-trip so silent format drift between writer and reader is caught. (Surfaced 2026-05-09 MS-A2 third-pass CR.) |
| MS-F | P2 | Warn on state-file vs broker `avg_cost` disagreement during carry-over reconcile — when both a state file entry_price and a broker `pos.avg_cost` are present and they differ by >1%, log a `WARNING` so a hand-edited / corrupted / mis-paired state file can't silently produce wrong cost_basis on the next SELL. State still wins (it's the strategy's authority); the warning just makes the disagreement visible. (Surfaced 2026-05-09 MS-A1 second-pass CR.) |
| MS-E | P3 | Per-strategy logical position layer (enables shared-symbol cross-strategy) — proper fix for two strategies trading the same symbol: new `PositionLedger` mapping `(strategy, symbol) → shares`, rewrite of `OrderManager.get_positions` to split aggregate, restart-time reconciliation logic, ~30 tests. 1–2 weeks of work. Not needed today; adds optionality if user ever wants e.g. SMA + RSI both on QQQ. |
| MS-I | ✅ | DONE 2026-05-11: `AccountSnapshotPoller.run()` now classifies `(ConnectionError, TimeoutError)` as a single-line WARNING ("capture skipped (IBKR not connected)") without `exc_info`; other exceptions keep the full traceback. CR caught the missing `TimeoutError` path (from `fut.result(timeout=10)` when the main event loop is wedged during reconnect). 3 new tests `test_as11..13`. |
| MS-J | ✅ | DONE 2026-05-11: `_save_state` now writes to a sibling `.tmp` and `os.replace`s into place — atomic same-filesystem swap on POSIX rename / Win32 MoveFileEx. A process kill mid-write (SIGKILL, OOM, host crash, OneDrive sync race) can no longer leave a truncated JSON file that triggers the `_load_state` bare-except → silent fallback to defaults → next save re-persisting those defaults, silently re-resetting a real ratcheted peak. 3 new tests `test_msj_01..03` (no leftover tmp, recovery from truncated main, recovery from orphan tmp). |
| MS-J2 | P3 | Escalate persistent `_save_state` failure — the bare `except Exception` in `_save_state` swallows `OSError`/`PermissionError` from `os.replace` (e.g., Windows AV/OneDrive transient lock; ENOSPC on the VPS) and just emits a single WARNING. If N consecutive saves fail, the in-memory ratcheted peak silently diverges from disk and a restart resets it. Add a counter + ntfy after N consecutive failures, mirroring MS-C. Pre-existing; surfaced 2026-05-11 MS-J post-impl CR (MEDIUM). |
| MS-J3 | P3 | fsync durability for `_save_state` — MS-J makes the visibility of the new inode atomic via `os.replace`, but does not guarantee the bytes are durable across a host power-loss (no `fsync(tmp)` before rename, no `fsync(parent_dir)` after). ext4 `data=ordered` default mostly saves us, but not guaranteed. Acceptable for paper trading on a VPS with `Restart=on-failure`; revisit if we ever observe state-loss after a hard reboot. Surfaced 2026-05-11 MS-J post-impl CR (LOW). |
| MS-K | ✅ | DONE 2026-05-10 (in MS-B PR): trip-on-detect guard for partial SELL — new `_partial_fill_halt` flag (independent from CB, gates `on_tick` so exits cannot naked-short orphan shares), float-tolerant compare `(result.filled + 0.5) < _position_shares`, persisted in v2 state. Trips CB + ntfy alert defense-in-depth. Does NOT attempt proper partial handling (decrement + bracket re-sizing) — that remains future work if partial fills become common. 4 new tests (msb_13..16). |
| MS-K-full | P3 | Proper partial-fill handling for RSI2MR — decrement `_position_shares` by `result.filled`, re-size bracket STP/LMT to remaining position, log a single-line WARNING. Today (MS-K) trips a halt flag instead — operator must manually reconcile. Worth doing only if partial fills become recurrent in paper trading. |

---

## Tooling & Code Quality

| # | Priority | Item |
|---|----------|------|
| 1.14 | P1 | Review and improve all documentation |
| 2.6 | P2 | Virtual environment setup docs update (Sprint 5.2 handled VPS; local Windows venv docs pending) |
| QA-15 | P2 | Delayed data staleness warning surfaced to strategies |
| QA-16 | P2 | Market hours check for DAY orders |
| 5.11 | P1 | Split test suite — mark IBKR-dependent tests with `requires_tws` so CI can run logic-only tests without TWS (today CI test step always fails because `tests/run_tests.py:94` connects at module load) |

---

## Owner Decisions Required

| # | Decision | Options |
|---|----------|---------|
| A | Live market data subscription (~$10–25/month via IBKR)? | **Yes** = real-time, works for intraday. **No** = 15-min delayed, fine for daily-bar strategies, free. |
| B | ✅ RESOLVED 2026-05-06 | **Independent** — each strategy gets its own 2% risk cap; trades are completely independent. |
