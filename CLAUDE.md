# CLAUDE.md — Session Handoff Document

Read this file at the start of every new Claude session before touching any code.
It gives you full context so you can continue work without re-explaining everything.

---

## What this project is

A Python algorithmic trading bot that connects to Interactive Brokers (IBKR) via the TWS API.
Built for the user (Afikim team) to run multiple trading strategies on paper and live accounts.

**GitHub:** https://github.com/gzion2719/Trad_Bot_wClaude

---

## User profile

- Business owner, not a software engineer — explain things clearly but do not over-explain
- Expects expert-level code and architecture decisions
- Uses Claude Code on Windows 11 (local machine: `C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot`)
- Has a team that will read the code — keep everything clean and well-documented
- Plans to host on Hostinger VPS once the bot is stable

---

## Current state (update this section each session)

**Last session completed:**
- IBKR connection working (paper account DUE090987, port 7497)
- `IBKRClient` — connection, market data (delayed mode auto-set for paper), contract qualification, heartbeat, retry with backoff
- `OrderManager` — place/cancel orders, duplicate prevention, real-time TWS sync via events, thread-safe order cache
- Data models — `OrderRequest`, `OrderResult`, `Position`
- Logging — rotating file + console
- Project structure — README, .gitignore, .env.example, main.py, BaseStrategy
- Git repo initialized and pushed to GitHub: https://github.com/gzion2719/Trad_Bot_wClaude
- QA audit round 1 (25 issues) + round 2 (13 issues) — all fixed
- Test suite: 40/40 passing (`tests/run_tests.py`) + 5/5 market-hours tests (`tests/run_market_tests.py`)
- Architect reviewed Sprint 2 & 3 — full implementation plan logged in TODO.md
- No strategies implemented yet

**Next tasks (check TODO.md for full list):**
1. **START HERE → Sprint 2.1:** `ReconnectManager` in `broker/reconnect.py`
2. Sprint 2.2: `RiskManager` in `risk/risk_manager.py`
3. Sprint 2.3: `PositionSizer` in `risk/position_sizer.py`
4. Sprint 2.5: `validate_config()` in `config/validator.py`
5. Sprint 2.6: venv setup docs
6. Then Sprint 3: DataFeed, HistoricalDataLoader, BacktestEngine

**Owner decisions needed before Sprint 4 (not urgent):**
- Decision A: Pay for IBKR live data (~$10–25/mo) or use free delayed data?
- Decision B: Multi-strategy positions — independent or combined?

---

## Python environment

- Python: 3.12 (`C:\Users\galzi\AppData\Local\Programs\Python\Python312\python.exe`)
- Run scripts with full path or after adding Python to PATH
- No virtual environment yet (Task 2.6)

```bash
# How to run anything in this project:
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
/c/Users/galzi/AppData/Local/Programs/Python/Python312/python.exe <script.py>
```

---

## Architecture

```
main.py
  └── IBKRClient          broker/ibkr_client.py
        └── OrderManager  broker/order_manager.py
              └── Strategy  strategies/base_strategy.py (abstract)
```

### IBKRClient (`broker/ibkr_client.py`)
- Wraps `ib_insync.IB`
- `connect()` — connects and auto-sets market data mode (delayed for paper, realtime for live)
- `get_market_price(symbol)` — fetches best available price with NaN-safe fallback chain
- `qualify_contract(contract)` — resolves full contract details from IBKR
- Fires `on_disconnect` callback when TWS drops

### OrderManager (`broker/order_manager.py`)
- `place_order(OrderRequest)` — validates, deduplicates, submits
- `cancel_order(order_id)` / `cancel_all(symbol)` — cancel by ID or symbol
- `get_open_orders()` / `get_positions()` — current state
- `sync()` — pulls all open orders from TWS across all sessions (`reqAllOpenOrders`)
- Internal cache stays in sync via `openOrderEvent`, `newOrderEvent`, `cancelOrderEvent`
- `on_fill`, `on_cancel`, `on_error` — register callbacks for order events
- Error code 202 (cancelled) treated as INFO, not ERROR

### Models (`models/order.py`)
- `OrderRequest` — validated input (raises on bad data in `__post_init__`)
- `OrderResult` — immutable snapshot of order state
- `Position` — current holding
- Enums: `OrderAction`, `OrderType`, `TimeInForce`, `OrderStatus`

### Config (`config/`)
- `settings.py` — loads from `.env` (IB_HOST, IB_PORT, IB_CLIENT_ID)
- `logging_config.py` — call `setup_logging()` once at startup

---

## IBKR connection details

| Setting | Value |
|---|---|
| Account | DUE090987 (paper) |
| Host | 127.0.0.1 |
| Port | 7497 (paper) / 7496 (live) |
| Client ID | 1 |
| Market data | Delayed (auto-set for paper accounts) |

TWS must be running and logged in before starting the bot.
TWS API must have "Enable ActiveX and Socket Clients" checked.

---

## Key conventions

- All currency: USD unless specified
- Default exchange: SMART (IBKR's smart routing)
- Default TIF: GTC (Good Till Cancelled) — avoids DAY order cancellation when market is closed
- `setup_logging()` must be called before any module that uses `logging`
- Never import from `.env` directly — always go through `config/settings.py`
- Always qualify contracts before placing orders (`client.qualify_contract(...)`)

---

## Files to always read before editing

| File | Why |
|---|---|
| `TODO.md` | Understand current task priorities |
| `broker/order_manager.py` | Core trading logic |
| `broker/ibkr_client.py` | Connection and data layer |
| `models/order.py` | Data contracts used everywhere |

---

## How to run tests

```bash
python test_connection.py      # verify IBKR connection + account summary
python test_order_manager.py   # place/cancel test order, test duplicate prevention
```

---

## Known limitations / watch out for

- Paper accounts get delayed data only (15-min lag) — `get_market_price()` returns delayed prices
- `openTrades()` only sees orders from the current session — always use `sync()` or `reqAllOpenOrders()` to see all orders
- TWS disconnects daily around 11:45 PM EST — auto-reconnect not yet implemented (Task 2.1)
- No risk manager yet — the bot will place whatever it is told (Task 2.2)
- No virtual environment set up yet (Task 2.6)
