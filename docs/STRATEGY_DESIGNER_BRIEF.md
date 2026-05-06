# TradeBot — Strategy Designer Brief

**Who this is for:** The person designing a new trading strategy to run in the TradeBot system.
You do NOT need to write Python code. You need to answer the questions in this document.
Claude will turn your answers into production code.

---

## What the bot already enforces (non-negotiable)

These rules are hard-wired into the infrastructure and apply to **every** strategy automatically:

| Rule | Value | Where it lives |
|---|---|---|
| Max risk per trade | 2% of account equity | `RiskManager.plan_trade()` |
| Minimum reward/risk ratio | 1:3 | `RiskManager.plan_trade()` |
| Max daily loss ceiling | −$2,000 | `RiskManager` — bot halts all trading |
| Max single order value | $120,000 | `RiskManager` |
| Max single position value | $100,000 | `RiskManager` |
| Protective stop order | Placed immediately after BUY fills | `BaseStrategy` pattern |
| GTC orders only | No DAY orders | Avoids cancellation when market is closed |
| Broker: Interactive Brokers | Paper account, TWS API | Hard-wired |

**The designer cannot override these.** If you need different limits for your strategy, tell Claude — we will discuss raising/lowering the caps in `main.py` for your specific strategy.

---

## What the designer must decide

### 1. Strategy name
A short, descriptive name. Example: `MomentumBreakout`, `MeanReversionRSI`, `VIXHedge`.

### 2. Symbol(s)
What ticker(s) does this strategy trade?
- Current bot trades: `QQQ` (SMA Crossover)
- Supported: any liquid US equity or ETF tradeable on IBKR (SMART routing)
- Example: `SPY`, `AAPL`, `GLD`

### 3. Timeframe / bar interval
How often does the strategy make a decision?

| Option | Description | When to use |
|---|---|---|
| Daily (1 bar/day) | Fires once at 16:10 ET | Swing trading, trend following |
| Intraday | Fires on a timer (e.g. every 5 min) | Mean reversion, momentum |

**Current SMA Crossover uses: Daily** (fires once per day at close).

> Note: intraday strategies require live IBKR market data (~$10–25/month). Daily strategies work fine with free delayed data.

### 4. Direction
- **Long only** (BUY then SELL) — simplest, what the current bot does
- **Short only** (SELL SHORT then BUY TO COVER) — supported by infrastructure
- **Both long and short** — supported, but requires careful position-state tracking

### 5. Entry signal
Describe in plain English what conditions must be true for the strategy to enter a trade.

Examples:
- "Price closes above the 20-day high after a pullback to the 50-day moving average"
- "RSI crosses above 30 after being below 30 for at least 3 days"
- "Price breaks out above yesterday's high with volume > 1.5x the 20-day average"

**What we need from you:**
- The indicator(s) used (SMA, EMA, RSI, MACD, Bollinger Bands, Volume, etc.)
- The parameters for each indicator (e.g. RSI period = 14)
- The exact crossover or threshold that triggers entry
- Any filters (time of day, day of week, VIX level, etc.)

### 6. Stop loss
Where does the strategy exit if the trade goes wrong?

Examples:
- "Lowest close of the last 5 days minus 0.5%"
- "3% below entry"
- "Below the prior swing low on the daily chart"
- "ATR-based: entry minus 2× 14-day ATR"

**This is required.** The stop price is used by `RiskManager.plan_trade()` to size the position. A strategy without a stop cannot be wired.

### 7. Take profit / target
Where does the strategy exit if the trade goes well?

Examples:
- "3× the distance from entry to stop" (this is what SMA Crossover does — minimum required by the 1:3 rule)
- "Next resistance level"
- "Exit on reversal signal (e.g. RSI crosses back below 70)"
- "Time-based: exit after N days regardless"

> Note: the 1:3 R/R rule means the target must be at least 3× the stop distance from entry. If your target is tighter, the trade will be rejected by `RiskManager`. A common workaround is to set the "risk manager target" to 3× and manage the actual exit differently in code.

### 8. Exit signal (if not purely stop/target)
Describe any additional conditions that trigger an exit before the stop or target is hit.

Examples:
- "Fast SMA crosses back below slow SMA (like SMA Crossover)"
- "RSI crosses above 70 (overbought)"
- "Price closes below the 20-day EMA"

### 9. How many bars of history are needed?
How far back does the strategy need to look to compute its signals?

Example: "I need 50 days of daily closes to compute the 50-day SMA."

This determines the warmup period — the strategy will not trade until this many bars have loaded.

### 10. Position sizing preference
The bot uses risk-based sizing by default (2% of equity risked per trade, calculated from entry–stop distance). Do you want anything different?

- **Default (recommended):** 2% risk-based — `RiskManager.plan_trade()` calculates shares automatically
- **Fixed shares:** always trade exactly N shares
- **Fixed % of equity:** always deploy X% of account value

> If you choose fixed shares or fixed %, tell Claude the specific number/percentage. The 2% rule still caps you — you cannot risk more than 2% per trade regardless of sizing method.

### 11. Frequency / how often can a new trade open?
- After exiting a trade, can the strategy immediately re-enter on the next signal?
- Or is there a cooldown (e.g. "no new entries for 5 days after a stop-out")?

### 12. Any special conditions or filters?
Anything else the strategy should check before entering?

Examples:
- "Only trade when the S&P 500 is above its 200-day MA"
- "No new entries in the last 30 minutes before market close"
- "Skip earnings week (within 3 days of earnings date)"
- "Only trade on Monday, Wednesday, Friday"

---

## How a strategy is tested before going live

1. **Backtest first** — Claude will run the strategy against 5+ years of historical data using the built-in backtester. We expect to see: positive returns, max drawdown, win rate, profit factor.
2. **Validate on bear regimes** — 2008, 2020 crash, 2022 bear market. A strategy that only works in bull markets is not usable.
3. **Paper trade** — run on IBKR paper account alongside the existing SMA Crossover bot (separate instances).
4. **Review fills** — compare paper fills to backtest fills for 2–4 weeks before live.

---

## What is NOT needed from the designer

- Python code (Claude writes it)
- Knowledge of the IBKR API or bot internals
- Unit tests (Claude writes those too)
- Deployment instructions (handled by the existing systemd + git workflow)

---

## Multi-strategy: can this strategy run alongside SMA Crossover?

**Yes.** The infrastructure supports multiple strategies running simultaneously — each gets its own `BarScheduler`, its own position state, and its own `TradeLog` entries.

**Decision B is already resolved (2026-05-06):** each strategy is fully independent with its own 2% risk cap. A second strategy trading at the same time as SMA Crossover could expose up to 4% of equity total — that is intentional and accepted.

`main.py` currently only wires one strategy. Once the new strategy design is ready, Claude will wire both into `main.py` in a single session (ROADMAP 4.8).

---

## Summary: what to send Claude

Fill in this block and paste it into the chat:

```
Strategy name: 
Symbol(s): 
Timeframe: [daily / intraday every X min]
Direction: [long only / short only / both]

Entry signal:
  Indicator(s): 
  Parameters: 
  Trigger condition: 
  Filters (if any): 

Stop loss: 

Take profit / target: 

Additional exit conditions: 

History needed (bars): 

Position sizing: [default 2% risk-based / fixed N shares / X% of equity]

Re-entry cooldown: 

Special filters: 
```
