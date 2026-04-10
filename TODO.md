# TradeBot — Project Task Tracker

Legend: `[ ]` pending · `[x]` done · `[~]` in progress · `[!]` blocked

---

## Sprint 1 — Foundation ✅ COMPLETE

| # | Status | Priority | Task |
|---|--------|----------|------|
| 1.1 | [x] | P0 | Connect to IBKR TWS via API (`ib_insync`) |
| 1.2 | [x] | P0 | Paper trading account verified |
| 1.3 | [x] | P0 | `IBKRClient` — connection, market data, contract qualification |
| 1.4 | [x] | P0 | `OrderManager` — place, cancel, deduplicate, event callbacks |
| 1.5 | [x] | P0 | Real-time TWS sync (catches external/manual order changes) |
| 1.6 | [x] | P0 | Delayed market data mode auto-set for paper accounts |
| 1.7 | [x] | P1 | Structured logging (console + rotating file) |
| 1.8 | [x] | P1 | Data models (`OrderRequest`, `OrderResult`, `Position`) |
| 1.9 | [x] | P1 | Project structure, README, .gitignore, .env.example |
| 1.10 | [x] | P1 | `main.py` entry point with event loop |
| 1.11 | [x] | P1 | `BaseStrategy` abstract class |
| 1.12 | [x] | P0 | Git repo → github.com/gzion2719/Trad_Bot_wClaude |
| 1.13 | [x] | P0 | `CLAUDE.md` — full context handoff for new Claude sessions |
| 1.14 | [ ] | P1 | Review and improve all documentation |
| 1.15 | [x] | P1 | Define and document test plan → `TEST_PLAN.md` |
| 1.16 | [x] | P1 | Execute test plan — 40/40 passing (Run 4, 2026-04-10) |

---

## QA Audit Fixes ✅ COMPLETE (both rounds)

### Round 1 — 25 issues (all Critical + High fixed)

| # | Status | Issue |
|---|--------|-------|
| QA-01 | [x] | Race condition: `sleep(0.5)` before cache write reduces window |
| QA-02 | [x] | Thread safety: `threading.Lock` on `self._orders` |
| QA-03 | [x] | `connect()` waits for account state before returning |
| QA-04 | [x] | Dead code in `_best_price()` — midpoint fallback fixed |
| QA-05 | [x] | Heartbeat via `is_alive()` / `reqCurrentTime()` |
| QA-06 | [x] | `connect()` failure caught + retry with backoff in `main.py` |
| QA-07 | [x] | Market data polling loop with timeout instead of fixed sleep |
| QA-08 | [x] | Live port (7496) warning logged loudly on connect |
| QA-09 | [ ] | No risk management (addressed in Sprint 2.2) |
| QA-10 | [x] | `get_positions()` and `get_open_orders()` guard on is_connected |
| QA-11 | [x] | `avg_fill_price` returns None (not 0.0/NaN) for unfilled orders |
| QA-12 | [x] | Error 202 moved to `_WARNING_CODES`, cache updated via events |
| QA-13 | [x] | `submitted_at` set at `place_order()` time, not object creation |
| QA-14 | [x] | `qualify_contract()` prefers `primaryExchange` |
| QA-15 | [ ] | Delayed data staleness warning to strategies (Sprint 3.1) |
| QA-16 | [ ] | Market hours check for DAY orders (Sprint 2.5) |
| QA-17 | [ ] | Backtester stubs marked WIP (Sprint 3.3) |
| QA-18 | [x] | Fractional quantity warning in `OrderRequest.__post_init__` |

### Round 2 — 13 issues (all fixed 2026-04-10)

| # | Status | Issue |
|---|--------|-------|
| R2-01 | [x] | `get_positions()` was hardcoded 0.0 — now reads real IBKR values via `ib.portfolio()` |
| R2-02 | [x] | `_handle_order_status()` double-lock race eliminated — single lock block |
| R2-03 | [x] | `_handle_cancel_order()` snapshots Trade inside lock |
| R2-04 | [x] | `get_market_price()` try/finally guarantees `cancelMktData()` on exception |
| R2-05 | [x] | `sync()` calls `openTrades()` before acquiring lock |
| R2-06 | [x] | Unknown IBKR status logged as WARNING; `PendingCancel` added to enum |
| R2-07 | [x] | Error codes 502/503/504 classified as connection errors, forwarded to callbacks |
| R2-08 | [x] | `_clear_callbacks()` added; called in tests before each callback registration |
| R2-09 | [x] | `Position.fetched_at` timestamp added |
| R2-10 | [x] | Startup test cleanup reports count and waits appropriately |
| R2-11 | [x] | `connect()` retry loop uses `total_attempts` variable, logs "attempt N/M" |
| R2-12 | [x] | Tests use `logging.disable(INFO)` — WARNING/ERROR/CRITICAL visible |
| R2-13 | [x] | `_best_price(ticker: Ticker)` type hint added |

---

## Sprint 2 — Stability & Risk
### Architect plan logged 2026-04-10. Ready to implement.

| # | Status | Priority | Task | Detail |
|---|--------|----------|------|--------|
| 2.1 | [ ] | P0 | Auto-reconnect on TWS disconnect | New class `ReconnectManager` in `broker/reconnect.py`. Background daemon thread watches for disconnect event, retries `connect()` with backoff [5,10,30,60,120]s. On success: calls `sync()`, fires `on_reconnected` callback. All strategies pause via `threading.Event.wait()` during gap. Max attempts configurable. |
| 2.2 | [ ] | P0 | RiskManager class | New class `RiskManager` in `risk/risk_manager.py`. Sits between Strategy and OrderManager — Strategy calls `risk_manager.check(request, price)` before `place_order()`. Three enforcement levels: (1) per-order max USD value, (2) per-symbol max exposure, (3) daily loss ceiling. Raises `RiskViolationError` on breach. `record_fill()` updates daily P&L via `on_fill` callback. `reset_daily()` called at market open. `is_halted()` returns True if ceiling breached. |
| 2.3 | [ ] | P1 | PositionSizer helper | New static class `PositionSizer` in `risk/position_sizer.py`. Three methods: `fixed(shares)`, `percent_of_equity(equity, price, pct)`, `kelly(win_rate, win_loss_ratio, equity, price, max_fraction=0.25)`. All return int (number of shares). |
| 2.4 | [x] | P1 | Heartbeat / health check | Done — `is_alive()` via `reqCurrentTime()` |
| 2.5 | [ ] | P1 | Config validation on startup | New function `validate_config()` + `ConfigError` in `config/validator.py`. Checks: IB_HOST non-empty, IB_PORT is 7496 or 7497, IB_CLIENT_ID is positive int, loud warning if live port. Called as first line of `main()`. |
| 2.6 | [ ] | P2 | Virtual environment setup | Add `docs/setup.md`. Commands: `python -m venv venv`, `venv\Scripts\activate`, `pip install ib_insync python-dotenv`. Update `.gitignore` to exclude `venv/`. |
| 2.7 | [ ] | P2 | Alert system (email/Slack on fill, error, loss breach) | Defer to after Sprint 3 |

**New file layout for Sprint 2:**
```
risk/
  __init__.py
  risk_manager.py      # RiskManager, RiskViolationError
  position_sizer.py    # PositionSizer (static)
broker/
  reconnect.py         # ReconnectManager (NEW)
config/
  validator.py         # validate_config(), ConfigError (NEW)
docs/
  setup.md             # venv instructions (NEW)
```

**Updated `main.py` wiring order:**
1. `validate_config()`
2. `client = IBKRClient()`
3. `client.connect(retries=3)`
4. `om = OrderManager(client)`
5. `rm = RiskManager(client, ...)`
6. `reconnect = ReconnectManager(client, om)`
7. `reconnect.start()`
8. `om.on_fill(rm.record_fill)`
9. Load and start strategy

**Updated `BaseStrategy.__init__` signature:**
```python
def __init__(self, client, order_manager, risk_manager, reconnect)
```
Typical `on_tick()` guard at top:
```python
self.reconnect.wait_for_connection(timeout=30)
if self.risk_manager.is_halted(): return
```

---

## Sprint 3 — Data & Backtesting
### Architect plan logged 2026-04-10. Starts after Sprint 2 complete.

| # | Status | Priority | Task | Detail |
|---|--------|----------|------|--------|
| 3.1 | [ ] | P0 | DataFeed abstraction + IBKRFeed | New `data/feed.py` + `data/bar.py`. `Bar` dataclass: symbol, timestamp, OHLCV, is_delayed. Abstract `DataFeed`: subscribe/unsubscribe/get_latest/is_live. `IBKRFeed` uses `reqRealTimeBars` or `reqHistoricalData(keepUpToDate=True)`. `BarScheduler` timer wrapper calls `strategy.on_tick()` every N seconds. Abstraction allows Polygon.io/Alpaca to be plugged in later without changing strategy code. |
| 3.2 | [ ] | P0 | Historical data loader | New `data/historical.py`. `HistoricalDataLoader` with two methods: `load_yfinance(symbol, start, end, interval)` and `load_ibkr(symbol, duration, bar_size, client)`. Both return `pd.DataFrame` with standard OHLCV columns + DatetimeIndex. Add `yfinance` to dependencies. |
| 3.3 | [ ] | P0 | Backtesting engine | New `backtester/engine.py` + `backtester/portfolio.py`. `MockOrderManager` implements same interface as real `OrderManager` — strategies run unchanged. `BacktestPortfolio` tracks cash, positions, equity curve, trade history. `BacktestEngine.run()`: injects `MockOrderManager` + `BacktestDataFeed`, replays bars, calls `strategy.on_tick()` each bar. Returns `BacktestResult`. Same strategy class runs live and in backtest — no code changes needed. |
| 3.4 | [ ] | P1 | Performance metrics | New `backtester/metrics.py`. Pure functions: `sharpe_ratio()`, `max_drawdown()`, `win_rate()`, `profit_factor()`, `summary()`. `summary()` prints formatted table. |
| 3.5 | [ ] | P1 | Trade history persistence | New `data/trade_log.py`. `TradeLog` class backed by SQLite (stdlib `sqlite3`, no extra dependency). Schema: id, strategy_name, symbol, action, quantity, fill_price, filled_at, pnl, account. Methods: `record(result, strategy_name)`, `get_history(symbol, since, strategy)`, `daily_summary()`. Wired via `om.on_fill(lambda r: trade_log.record(r, "StrategyName"))`. |
| 3.6 | [ ] | P2 | Paper simulation mode (no TWS required) | Defer — covered by BacktestEngine |

**New file layout for Sprint 3:**
```
data/
  __init__.py
  bar.py           # Bar dataclass
  feed.py          # DataFeed (abstract), IBKRFeed, BarScheduler
  historical.py    # HistoricalDataLoader
  trade_log.py     # TradeLog (SQLite)
backtester/
  __init__.py
  engine.py        # BacktestEngine, MockOrderManager
  portfolio.py     # BacktestPortfolio
  metrics.py       # pure metric functions
```

---

## Sprint 4 — Strategies
### Blocked on: Sprint 2 complete + owner Decision A (data source)

| # | Status | Priority | Task |
|---|--------|----------|------|
| 4.1 | [ ] | P0 | Discuss and select first strategy |
| 4.2 | [ ] | P0 | Implement and backtest strategy #1 |
| 4.3 | [ ] | P1 | Implement and backtest strategy #2 |
| 4.4 | [ ] | P2 | Strategy parameter management (config-driven, no code changes) |
| 4.5 | [!] | P2 | Multi-strategy runner — blocked on Decision B (see below) |

---

## Sprint 5 — Deployment

| # | Status | Priority | Task |
|---|--------|----------|------|
| 5.1 | [ ] | P1 | Hostinger VPS setup guide |
| 5.2 | [ ] | P1 | IBC (headless IB Gateway) setup on VPS |
| 5.3 | [ ] | P1 | `systemd` process supervisor for auto-restart |
| 5.4 | [ ] | P2 | Monitoring dashboard (simple web UI or Grafana) |
| 5.5 | [ ] | P2 | CI/CD pipeline (auto-run tests on push) |

**VPS readiness checklist (must be done before first live strategy):**

| Requirement | Sprint |
|---|---|
| Virtual environment | 2.6 |
| Config validation (no accidental live trading) | 2.5 |
| RiskManager with daily loss ceiling | 2.2 |
| Auto-reconnect + strategy pause during gap | 2.1 |
| IBC (headless IB Gateway on VPS) | 5.2 |
| systemd process supervisor | 5.3 |
| Strategy backtested and validated | 3.3 |

---

## Sprint 6 — Intelligence & Tooling

| # | Status | Priority | Task |
|---|--------|----------|------|
| 6.1 | [ ] | P1 | Research best MCP servers / APIs for live and historical market data (Polygon.io, Alpaca, FMP, yfinance) |
| 6.2 | [ ] | P1 | Research best sources for trading logic: books, papers, quant blogs |
| 6.3 | [ ] | P2 | Evaluate connecting Claude to a financial data MCP for real-time reasoning during sessions |
| 6.4 | [ ] | P2 | Build `RESOURCES.md` with vetted sources for strategies, risk management, market microstructure |

---

## Owner Decisions Required

These two questions are not blocking Sprint 2 or 3, but must be answered before Sprint 4.

| # | Decision | Options | Deadline |
|---|----------|---------|----------|
| A | Live market data subscription (~$10–25/month via IBKR)? | **Yes** = real-time signals, works for intraday. **No** = 15-min delayed, fine for end-of-day strategies, free. | Before Sprint 4 |
| B | Multi-strategy position behavior: when two strategies both want to buy the same stock, do they act independently (each gets own position) or combine into one? | **Independent (default)** = Strategy A buys 10, Strategy B buys 10 → 20 shares total. **Combined** = shared cap, more complex. | Before Sprint 4.5 |

---

## Bugs & Improvements Log

| # | Severity | Description | Status |
|---|----------|-------------|--------|
| B-01 | S1 | `limit_price=0` and negative `limit_price` not rejected | Fixed |
| B-02 | S1 | `OrderManager.__init__` crashed when not connected | Fixed |
| B-03 | S2 | `cancel_order()` returned `True` for already-cancelled orders | Fixed |
| B-04 | S1 | `get_positions()` returned hardcoded 0.0 for all P&L fields | Fixed |
| B-05 | S1 | `cancelMktData()` not called if exception during price polling | Fixed |
| B-06 | S1 | Double-lock race in `_handle_order_status()` Cancelled branch | Fixed |
| B-07 | S2 | `PendingCancel` not in `OrderStatus` enum — logged false warnings | Fixed |

---

## Notes

- **Priority:** P0 = must have · P1 = should have · P2 = nice to have
- **Severity (bugs):** S1 = critical · S2 = major · S3 = minor
- Update this file at the start of every session
- See `CLAUDE.md` for full context when starting a new Claude session
- Architect plan for Sprint 2 & 3 logged 2026-04-10 — full detail in each sprint row above
