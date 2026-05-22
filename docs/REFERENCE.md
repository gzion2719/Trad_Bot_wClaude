# Component Reference

Steady-state architecture diagram + component contracts. Extracted from `CLAUDE.md` on 2026-05-22 (F-DOC-08). The structure here changes slowly; the session-handoff index in `CLAUDE.md` changes every session.

Read this file when the focus is **risk code**, **new or modified strategy/broker/runtime code**, or **backtest work** — see `OPEN_SESSION_PROTOCOL.md` Step 4b routing.

---

## Full project layout

```
TradeBot/
├── broker/
│   ├── ibkr_client.py      — ib_insync wrapper: connect, market data, contract qualification
│   ├── order_manager.py    — place/cancel/sync orders, thread-safe cache, event callbacks
│   └── reconnect.py        — ReconnectManager: auto-reconnect daemon with exponential backoff
│
├── risk/
│   ├── risk_manager.py     — RiskManager: pre-trade checks (order value, exposure, daily loss)
│   └── position_sizer.py   — PositionSizer: fixed, percent_of_equity, kelly (static methods)
│
├── data/
│   ├── bar.py              — Bar frozen dataclass: symbol, timestamp, OHLCV, is_delayed
│   ├── feed.py             — DataFeed (abstract), IBKRFeed (5-sec bars), BarScheduler
│   ├── historical.py       — HistoricalDataLoader: yfinance, IBKR reqHistoricalData, CSV
│   └── trade_log.py        — TradeLog: SQLite WAL, record fills, cost_basis, realized_pnl
│
├── backtester/
│   ├── engine.py           — BacktestEngine, MockOrderManager, BacktestDataFeed
│   ├── portfolio.py        — BacktestPortfolio: cash, positions, weighted avg cost, equity curve
│   └── metrics.py          — sharpe_ratio, max_drawdown, win_rate, profit_factor, summary()
│
├── models/
│   └── order.py            — OrderRequest, OrderResult (+ cost_basis field), Position, enums
│
├── config/
│   ├── settings.py         — loads .env: IB_HOST, IB_PORT, IB_CLIENT_ID
│   ├── validator.py        — validate_config(), ConfigError — called first in main()
│   └── logging_config.py   — rotating file + console logger
│
├── strategies/
│   └── base_strategy.py    — BaseStrategy ABC with full Sprint 4-ready interface
│
├── main.py                 — wiring: validate → connect → OrderManager → RiskManager → ReconnectManager
└── tests/
    ├── test_*.py           — pytest suite; canonical gate is `pytest tests/ -m "not market"`
    ├── run_tests.py        — legacy custom runner (still present; pytest is the source of truth)
    └── run_market_tests.py — tests requiring live market hours
```

---

## Architecture

```
main.py
  validate_config()
  IBKRClient  ──────────────────────────────────────────────────────────────────
    └── OrderManager                                                            │
          ├── RiskManager       (wired via om.on_fill)                          │
          └── ReconnectManager  (monitors disconnect, retries with backoff)     │
                └── Strategy(client, order_manager, risk_manager, reconnect,   │
                             feed, symbol)   ◄──────────────────────────────────┘
```

### How a live strategy tick works
```python
def on_tick(self):
    if not self.reconnect.wait_for_connection(timeout=60):
        return                                    # pause during TWS reconnect
    if self.risk_manager.is_halted():
        return                                    # daily loss ceiling hit
    bar = self.feed.get_latest(self.symbol)
    if bar is None:
        return
    # ... signal logic ...
    request = OrderRequest(symbol=self.symbol, action=OrderAction.BUY, quantity=10)
    self.safe_place_order(request, bar.close)     # risk check + place in one call
```

### How to backtest a strategy
```python
from backtester.engine import BacktestEngine
from data.historical import HistoricalDataLoader

df = HistoricalDataLoader.load_yfinance("AAPL", "2022-01-01", "2024-01-01")
engine = BacktestEngine(
    strategy_class=MyStrategy,
    data=df,
    symbol="AAPL",
    initial_capital=100_000,
    strategy_kwargs={"sma_fast": 10, "sma_slow": 30},  # passed to __init__
)
result = engine.run()
result.print_summary()
```

The **same strategy class** runs in live and backtest unchanged. The engine injects `MockOrderManager` instead of the real one. Fills happen at the next bar's open (no look-ahead bias).

---

## Key component reference

### IBKRClient (`broker/ibkr_client.py`)
- `connect(retries=3)` — connects, auto-sets delayed data for paper accounts, removes duplicate disconnect handlers
- `get_market_price(symbol)` — polls with timeout, try/finally guarantees `cancelMktData()`
- `qualify_contract(contract)` — resolves full contract, prefers `primaryExchange`
- `is_alive()` — heartbeat via `reqCurrentTime()`

### OrderManager (`broker/order_manager.py`)
- `place_order(request, allow_duplicate=False)` — validates, deduplicates, submits
- `cancel_order(order_id)` / `cancel_all(symbol)`
- `get_open_orders()` / `get_positions()` — current state (reads IBKR portfolio for full P&L)
- `sync()` — pulls all open orders from TWS via `reqAllOpenOrders`
- `on_fill(cb)` / `on_cancel(cb)` / `on_error(cb)` — register callbacks

### ReconnectManager (`broker/reconnect.py`)
- `start()` — arms the manager after initial connect
- `stop()` — disarms on clean shutdown
- `wait_for_connection(timeout)` — strategies call this at top of `on_tick()`
- `is_halted` — True if all reconnect attempts exhausted
- **Key design:** `connect()` and `sync()` are in separate try/except blocks — sync failure after good TCP connect halts immediately rather than looping

### RiskManager (`risk/risk_manager.py`)
- **`plan_trade(entry, stop, target, equity, order_action=BUY)`** — PRIMARY method for strategies. Atomically validates R/R + 2% rule, then returns correctly sized share count. Always use this instead of calling `validate_setup()` + `risk_based()` separately.
  - Example: entry $150, stop $145, target $165, equity $10k → R/R=3.0 ✓, risk/share=$5 ≤ $200 ✓ → **40 shares**
  - Short example: entry $100, stop $105, target $85, `order_action=OrderAction.SELL` → same math, correctly inverted
  - `equity` MUST be fresh from `client.get_account_summary()["NetLiquidation"]` — never cache across bars
- `validate_setup(entry, stop, target, equity, order_action=BUY)` — validates only (no sizing). Use `plan_trade()` instead.
  - **Rule A:** `(target − entry) / (entry − stop) ≥ min_reward_risk_ratio` (3.0 default)
  - **Rule B:** stop distance per share must be ≤ `equity × max_risk_per_trade_pct` (2% default)
  - Supports both longs (stop < entry) and shorts (stop > entry)
- `check(request, current_price)` — raises `RiskViolationError` if any order-level rule breached
- `update_daily_pnl(pnl)` — wired via daemon in `main.py` — ACTIVE
- `reset_daily()` — wired via daemon in `main.py`, fires at 9:30 AM ET each day — ACTIVE
- `is_halted()` — True if daily loss ceiling breached
- `record_fill(result)` — logging-only hook (wired via `om.on_fill`), does NOT update P&L

**Constructor parameters (main.py):**
```
max_risk_per_trade_pct=0.02   # 2% of equity max risk per trade
min_reward_risk_ratio=3.0     # minimum 1:3 R/R required for every trade
```

### PositionSizer (`risk/position_sizer.py`)
- **`PositionSizer.risk_based(equity, entry_price, stop_price, risk_pct=0.02)`** — do not call directly from strategies; use `rm.plan_trade()` instead so sizing and validation always use the same `risk_pct`. If calling directly, MUST pass `risk_pct=rm.max_risk_per_trade_pct`.
- `PositionSizer.fixed(shares)` — fixed quantity
- `PositionSizer.percent_of_equity(equity, price, pct)` — e.g., 2% of $50k at $150 = 6 shares
- `PositionSizer.kelly(win_rate, win_loss_ratio, equity, price, max_fraction=0.25)` — capped Kelly

### BaseStrategy (`strategies/base_strategy.py`)
- Implement: `on_start()`, `on_tick()`, `on_stop()`
- Override optionally: `on_fill(result)` — auto-wired, called on every fill
- Override: `params` property — return config dict, stored in TradeLog per trade
- Use: `self.safe_place_order(request, price)` — always use this, not `self.om.place_order()`
- Available: `self.feed`, `self.symbol`, `self.client`, `self.om`, `self.risk_manager`, `self.reconnect`

### DataFeed / IBKRFeed / BarScheduler (`data/feed.py`)
- `IBKRFeed(client)` — subscribes to 5-sec real-time bars via `reqRealTimeBars`
- `feed.subscribe(symbol, callback)` — atomic, deduped, handler stored for clean removal
- `feed.unsubscribe(symbol)` / `feed.unsubscribe_all()`
- `feed.get_latest(symbol)` — returns most recent `Bar` or None
- `BarScheduler(strategy, interval_seconds=60)` — calls `on_tick()` on a timer; stops after 5 consecutive errors

### HistoricalDataLoader (`data/historical.py`)
- `load_yfinance(symbol, start, end, interval="1d")` — free, no API key, returns UTC DataFrame
- `load_ibkr(symbol, duration, bar_size, client)` — enforces 11s rate limit between calls
- `load_csv(filepath, symbol)` — auto-detects date column, validates OHLCV

### BacktestEngine (`backtester/engine.py`)
- `BacktestEngine(strategy_class, data, symbol, initial_capital, commission=1.0, strategy_kwargs={})`
- `engine.run()` → `BacktestResult` with `.fills`, `.equity_curve`, `.metrics`, `.portfolio`
- `result.print_summary()` — prints formatted metrics table
- ⚠️ **Single-symbol only** — `BacktestDataFeed.get_latest()` returns None for any other symbol (TODO in Sprint 4.8)

### TradeLog (`data/trade_log.py`)
- `TradeLog(db_path=None)` — SQLite WAL, auto-creates schema, safe migration on upgrade
- `record(result, strategy_name, strategy_params=None)` — call from `on_fill`
- `get_history(symbol, strategy, since, limit=500)` — returns list of dicts
- `daily_summary(date=None)` — returns `{total_trades, buys, sells, gross_buy, gross_sell, net_flow, realized_pnl}`
- Schema: `id, strategy_name, symbol, action, quantity, fill_price, fill_value, filled_at, order_id, account, cost_basis, realized_pnl, strategy_params`

### Models (`models/order.py`)
- `OrderResult` has `cost_basis: Optional[float]` — set by `BacktestPortfolio` on SELL fills; used by `win_rate()` and `profit_factor()`
- `OrderStatus.PENDING_CANCEL` — legitimate IBKR state during cancellation, not an error
