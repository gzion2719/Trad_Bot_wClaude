# TradeBot — Test Plan
## IBKR Connection, Order Execution & Management

**Scope:** `IBKRClient`, `OrderManager`, `OrderRequest` validation  
**Account:** Paper trading (DUE090987)  
**Environment:** TWS running locally, port 7497  

Legend: `[ ]` not run · `[P]` passed · `[F]` failed · `[S]` skipped

---

## 1. Connection Tests

Goal: verify the bot handles all connection states gracefully — including failure, loss, and recovery.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| C-01 | Connect with TWS running and logged in | Connected, account visible, market data mode = delayed | S1 |
| C-02 | Connect with TWS not running | Clear `ConnectionRefusedError`, not a crash | S1 |
| C-03 | Connect with wrong port (e.g. 9999) | Clear error message, no hang | S1 |
| C-04 | Connect with wrong `clientId` already in use by another process | IBKR error 326, handled gracefully | S2 |
| C-05 | Call `connect()` twice without disconnecting | Warning logged, second call skipped — no duplicate connection | S2 |
| C-06 | Disconnect cleanly and reconnect | Reconnects successfully, order cache re-synced | S1 |
| C-07 | Simulate TWS drop mid-session (close TWS manually) | `on_disconnect` callback fires, warning logged | S1 |
| C-08 | Connect to live port (7496) — should NOT be paper | `is_paper` returns False, market data mode = realtime | S1 |

---

## 2. Market Data Tests

Goal: verify price fetching is reliable, NaN-safe, and handles all market states.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| D-01 | Get price for valid liquid stock (AAPL, MSFT) | Returns a positive float, no NaN | S1 |
| D-02 | Get price for valid stock during market hours | Returns last trade price | S1 |
| D-03 | Get price for valid stock when market is closed | Returns close price (frozen delayed data) | S2 |
| D-04 | Get price for invalid ticker (e.g. "XYZXYZ") | `RuntimeError` from `qualify_contract` — not a crash | S1 |
| D-05 | Get price for an index (e.g. SPX) | Either correct handling or clear error — no silent NaN | S2 |
| D-06 | Get price for low-volume / thinly traded stock | Returns best available price using fallback chain (close → bid/ask midpoint) | S2 |
| D-07 | Call `get_market_price` 10 times in a row | No stale subscriptions left open, no memory leak | S2 |

---

## 3. Order Validation Tests

Goal: verify bad inputs are caught before they reach IBKR.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| V-01 | `OrderRequest` with quantity = 0 | `ValueError` raised immediately | S1 |
| V-02 | `OrderRequest` with quantity = -5 | `ValueError` raised immediately | S1 |
| V-03 | `LIMIT` order with no `limit_price` | `ValueError` raised immediately | S1 |
| V-04 | `STOP` order with no `stop_price` | `ValueError` raised immediately | S1 |
| V-05 | `STOP_LIMIT` order missing both prices | `ValueError` raised immediately | S1 |
| V-06 | Symbol with lowercase letters (e.g. "aapl") | Auto-uppercased to "AAPL" — no error | S2 |
| V-07 | Symbol with leading/trailing spaces (e.g. " AAPL ") | Auto-stripped — no error | S2 |
| V-08 | `LIMIT` order with `limit_price = 0` | `ValueError` or graceful rejection — not sent to IBKR | S1 |
| V-09 | `LIMIT` order with negative `limit_price` | `ValueError` — not sent to IBKR | S1 |

---

## 4. Order Placement Tests

Goal: verify orders reach IBKR correctly under all normal conditions.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| P-01 | Market BUY — valid symbol, market open | Order placed, status PreSubmitted or Filled | S1 |
| P-02 | Market SELL — valid symbol, market open | Order placed, status PreSubmitted or Filled | S1 |
| P-03 | Limit BUY — price far below market (won't fill) | Order placed, sits as PreSubmitted/Submitted | S1 |
| P-04 | Limit SELL — price far above market (won't fill) | Order placed, sits as PreSubmitted/Submitted | S1 |
| P-05 | Market order when market is closed | Order placed with GTC — survives until open | S1 |
| P-06 | Market order when market is closed with TIF=DAY | Order cancelled immediately — handled gracefully, not an unhandled error | S2 |
| P-07 | Order for invalid/unqualifiable symbol | `RuntimeError` from qualification step — not sent to IBKR | S1 |
| P-08 | Order when not connected to TWS | `ConnectionError` raised before any IBKR call | S1 |
| P-09 | Order with quantity larger than buying power | IBKR rejects (code 201) — logged as WARNING, not crash | S1 |
| P-10 | Fractional quantity (e.g. 0.5 shares) | Either placed correctly or clear rejection — no silent failure | S2 |
| P-11 | Very large limit price (e.g. $999,999) | Either placed or rejected by IBKR — handled gracefully | S3 |
| P-12 | `on_fill` callback fires when order fills | Callback called with correct `OrderResult` | S1 |

---

## 5. Duplicate Prevention Tests

Goal: verify the bot never accidentally double-submits.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| DUP-01 | Place same BUY order twice for same symbol | Second call raises `DuplicateOrderError` | S1 |
| DUP-02 | Place BUY then SELL for same symbol | SELL is NOT blocked — different action | S2 |
| DUP-03 | Place order, cancel it, place same order again | Second order goes through — no ghost duplicate blocking it | S1 |
| DUP-04 | Place order, it fills, place same order again | Second order goes through — filled order not treated as open | S1 |
| DUP-05 | `allow_duplicate=True` bypasses check | Order placed without error | S2 |

---

## 6. Order Cancellation Tests

Goal: verify cancellations are reliable in all states.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| X-01 | Cancel an open order by ID | Order cancelled, removed from cache, `on_cancel` fires | S1 |
| X-02 | Cancel an order that was already filled | Warning logged, returns False — no crash | S1 |
| X-03 | Cancel an order that was already cancelled | Warning logged, returns False — no crash | S1 |
| X-04 | Cancel with a non-existent order ID | Warning logged, returns False — no crash | S1 |
| X-05 | `cancel_all()` with no open orders | Returns 0 — no crash | S2 |
| X-06 | `cancel_all("AAPL")` with multiple symbols open | Only AAPL orders cancelled, others untouched | S1 |
| X-07 | Cancel order placed manually in TWS via the bot | Bot finds it via `sync()` and cancels it | S2 |

---

## 7. External Sync Tests

Goal: verify the bot stays in sync when changes happen outside the API.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| S-01 | Place order via bot, cancel it manually in TWS | `on_cancel` callback fires, order removed from cache | S1 |
| S-02 | Place order manually in TWS, call `get_open_orders()` | Bot sees the externally placed order | S1 |
| S-03 | Call `sync()` explicitly — orders refresh | Cache matches TWS state exactly | S1 |
| S-04 | Disconnect and reconnect — check open orders | Orders from previous session visible after `sync()` | S1 |
| S-05 | Two processes connect with different `clientId`s simultaneously | Both operate independently without interfering | S2 |
| S-06 | Order fills while bot is running (market opens) | `on_fill` fires automatically — no polling needed | S1 |

---

## 8. Position Tests

Goal: verify position data is accurate and consistent with IBKR.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| POS-01 | `get_positions()` with no holdings | Returns empty list — no crash | S2 |
| POS-02 | `get_positions()` after a fill | Position appears with correct symbol, quantity, avg cost | S1 |
| POS-03 | Long position shows positive quantity | `is_long == True`, `is_short == False` | S2 |
| POS-04 | Position data matches TWS Portfolio tab | Values are consistent (within rounding) | S1 |

---

## 9. Error Handling & Edge Cases

Goal: find cracks in the design under unexpected conditions.

| ID | Test | Expected Result | Severity if broken |
|----|------|-----------------|-------------------|
| E-01 | IBKR sends error code 202 (order cancelled) | Logged as INFO — not ERROR, no crash | S2 |
| E-02 | IBKR sends error code 201 (order rejected) | Logged as WARNING, `on_error` callback fires | S1 |
| E-03 | IBKR sends unknown error code | Logged as ERROR, `on_error` fires — no crash | S2 |
| E-04 | Place 10 orders in rapid succession | All placed correctly, cache consistent, no race condition | S2 |
| E-05 | Request price and immediately place order | Limit price is never NaN — validation catches it | S1 |
| E-06 | `qualify_contract` times out (IBKR slow) | Timeout handled, clear error message | S2 |
| E-07 | TWS drops mid-order-placement | Order state is recoverable after reconnect via `sync()` | S1 |
| E-08 | Log file directory doesn't exist on first run | `logs/` created automatically — no crash | S3 |

---

## 10. Regression Checklist

Run these after every significant code change:

- [ ] `test_connection.py` passes
- [ ] `test_order_manager.py` passes (place + duplicate block + cancel)
- [ ] `get_market_price()` returns a valid float for AAPL
- [ ] Open orders visible in `get_open_orders()` match TWS Orders tab
- [ ] No leftover open subscriptions or ghost orders after test run

---

## Run History

### Run 1 — 2026-04-10 (pre-market)
- **Result:** 40/40 passed
- **Tests run:** C-01,03,05,06,08 · D-01,04,07 · V-01–09 · P-03,04,05,07,08 · DUP-01–03,05 · X-01,03,04,05,06 · S-03,05 · POS-01 · E-01,04,05,08
- **Skipped (needs market open):** P-01, P-02, P-12, S-06, POS-02 → `tests/run_market_tests.py`
- **Skipped (needs manual TWS action):** C-02, C-04, C-07

---

## Bug Log

| ID | Severity | Test | Description | Status |
|----|----------|------|-------------|--------|
| B-01 | S1 | V-08, V-09 | `limit_price=0` and negative `limit_price` not rejected by `OrderRequest` | Fixed |
| B-02 | S1 | P-08 | `OrderManager.__init__` called `sync()` when not connected — crashed before `place_order` | Fixed |
| B-03 | S2 | X-03 | `cancel_order()` returned `True` for already-cancelled orders | Fixed |

---

## Notes

- Run all S1 tests before any code goes to production
- S2 tests should pass before any live trading
- S3 tests are nice-to-have
- Always clean up test orders (cancel any open ones) after each test run
- Never run these tests against the live account
