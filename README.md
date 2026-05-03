# TradeBot

Algorithmic trading bot for Interactive Brokers (IBKR), built in Python.

- Connects to IBKR via the TWS API (`ib_insync`)
- Supports paper trading and live trading
- Designed for multiple pluggable strategies
- Includes a backtesting engine (in progress)

> **Dependency note:** This project uses [`ib_insync==0.9.86`](https://github.com/erdewit/ib_insync),
> which is **archived and no longer maintained** as of 2023. It works against current TWS API versions
> but has no upstream patch path if IBKR makes a breaking API change. Migration to
> [`ib_async`](https://github.com/ib-api-reloaded/ib_async) (the community fork) is tracked in the
> backlog (CR-07). Re-evaluate quarterly.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | [python.org/downloads](https://python.org/downloads) |
| TWS or IB Gateway | Download from IBKR. TWS recommended for development. |
| IBKR paper trading account | Free to open at interactivebrokers.com |

### TWS API setup
1. Open TWS and go to **Edit → Global Configuration → API → Settings**
2. Check **Enable ActiveX and Socket Clients**
3. Set **Socket port** to `7497` (paper) or `7496` (live)
4. Uncheck **Read-Only API**
5. Click **Apply → OK** and restart TWS

---

## Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd TradeBot

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
# Edit .env with your settings
```

---

## Configuration

All settings live in `.env` (never commit this file):

```env
IB_HOST=127.0.0.1
IB_PORT=7497        # 7497 = paper | 7496 = live
IB_CLIENT_ID=1
```

---

## Running the bot

Make sure TWS is open and logged in, then:

```bash
python main.py
```

---

## Project Structure

```
TradeBot/
│
├── main.py                   # Entry point — starts the bot and event loop
│
├── broker/
│   ├── ibkr_client.py        # IBKR connection, market data, contract qualification
│   └── order_manager.py      # Order placement, cancellation, sync, event callbacks
│
├── strategies/
│   ├── base_strategy.py      # Abstract base class all strategies must implement
│   └── (strategy files)      # One file per strategy
│
├── backtester/
│   ├── engine.py             # Backtest runner (replays data through strategy)
│   └── data_loader.py        # Loads historical OHLCV data (CSV, yfinance)
│
├── models/
│   └── order.py              # Data models: OrderRequest, OrderResult, Position
│
├── config/
│   ├── settings.py           # Loads settings from .env
│   └── logging_config.py     # Rotating file + console logging setup
│
├── logs/                     # Auto-created. Rotating log files written here.
│
├── .env                      # Your local secrets — never commit
├── .env.example              # Template for .env — safe to commit
├── .gitignore
└── requirements.txt
```

---

## Architecture

```
main.py
  └── IBKRClient          connects to TWS, fetches prices, qualifies contracts
        └── OrderManager  places/cancels orders, syncs state, fires callbacks
              └── Strategy  implements trading logic via on_start / on_tick / on_stop
```

**Key design decisions:**

- **IBKRClient** is the only layer that talks to TWS directly. Everything else goes through it.
- **OrderManager** maintains an internal order cache that stays in sync with TWS in real time — even when orders are modified manually or by another client.
- **Strategies** are stateless regarding connection — they receive a client and order manager and focus purely on logic.
- **Market data mode** is set automatically: delayed (free) for paper accounts, realtime for live.

---

## Adding a Strategy

1. Create `strategies/my_strategy.py`
2. Inherit from `BaseStrategy` and implement `on_start`, `on_tick`, `on_stop`
3. Register it in `main.py`

```python
from strategies.my_strategy import MyStrategy
strategy = MyStrategy(client, om)
strategy.on_start()
```

---

## Logging

Logs are written to both the console and `logs/tradebot.log`.  
The file rotates at 5 MB and keeps 5 backups.

---

## Deployment

| Environment | Setup |
|---|---|
| Local (dev) | Run TWS, then `python main.py` |
| Hostinger VPS | Install IB Gateway (headless), use a process manager like `systemd` or `supervisor` |

---

## Dependencies

| Package | Purpose |
|---|---|
| `ib_insync` | Async Python wrapper for the IBKR TWS API |
| `python-dotenv` | Loads `.env` configuration |
