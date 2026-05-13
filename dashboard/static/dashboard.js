// Dashboard front-end. CSP-friendly: no inline event handlers, no inline scripts.
// Loaded by index.html as /static/dashboard.js with a strict default-src 'self' CSP.

const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
const fmtMoney = (n) => n == null ? "—" : (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2);
const fmtAge = (s) => {
  if (s == null) return "—";
  if (s < 60) return s.toFixed(0) + "s";
  if (s < 3600) return (s / 60).toFixed(1) + "m";
  return (s / 3600).toFixed(1) + "h";
};
const fmtUSD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

// Cached /api/info account for mismatch detection
let _infoAccount = null;

// True when the IBKR Account tab is the active view.
// Gates fetchEquity (rate-limited 10/min) — NOT fetchAccount, which feeds the
// always-visible KPI strip and is a cheap snapshot-file read.
let _onAcctTab = false;

// Strategies tab state. `_onStratTab` gates polling of /api/strategies/{name}/summary
// while the tab is hidden — the endpoint is NOT rate-limited (it has a 30s
// server-side TTL cache keyed on MAX(id) FROM trades), but polling a hidden
// tab is pure waste. `_stratTabsInited` flips after the lazy first activation
// fetch completes — the tab cannot fetch /api/strategies pre-login (401),
// so building the secondary tablist at DOMContentLoaded would leave it empty
// on cold-load until reload.
let _onStratTab = false;
let _stratList = null;
let _activeStrategy = null;
let _lastStratFetch = 0;
let _stratTabsInited = false;
const _STRAT_POLL_MS = 30000;
const _STRAT_STORAGE_KEY = "tradebot.activeStrategy";

// History table state. One AbortController per panel; ANY state mutation
// (strategy switch, page change, page-size change) aborts the prior in-flight
// fetch and replaces the controller — single rule prevents stale rows
// overwriting newer ones. Server caps `offset` at 10_000 (dashboard/app.py:409).
let _stratHistoryOffset = 0;
let _stratHistoryLimit = 50;
let _stratHistoryTotal = 0;
let _stratHistoryAbort = null;
const _STRAT_OFFSET_CAP = 10_000;
// Display-name mapping. Keep as a module-level constant so a future schema
// rename surfaces here via grep, not as silent column drift. Order defines
// CSV/table column order in S3c too.
const _STRAT_HISTORY_COLS = [
  ["filled_at",       "Time"],
  ["action",          "Side"],
  ["quantity",        "Qty"],
  ["fill_price",      "Price"],
  ["cost_basis",      "Cost basis"],
  ["realized_pnl",    "Realized P&L"],
  ["real_r_multiple", "R-multiple"],
  ["strategy_params", "Params"],
];

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function _fetchJSON(path) {
  const r = await fetch(path, { credentials: "same-origin" });
  if (r.status === 401) {
    showLogin("Session expired — please log in again.");
    return null;
  }
  if (!r.ok) {
    console.warn("fetchJSON", path, "->", r.status);
    return null;
  }
  return r.json();
}

// ── Mission Control polling ───────────────────────────────────────────────────

async function fetchInfo() {
  const info = await _fetchJSON("/api/info");
  if (!info) return;
  _infoAccount = info.account || null;
  document.getElementById("info-account").textContent = info.account;
  document.getElementById("info-host").textContent = info.host;
  document.getElementById("info-port").textContent = info.port;
  document.getElementById("info-started").textContent = info.dashboard_started_at;
}

async function fetchToday() {
  const today = await _fetchJSON("/api/today");
  if (!today) {
    // Reset stale values so they don't persist behind the login overlay.
    const pnlEl = document.getElementById("today-pnl");
    if (pnlEl) { pnlEl.textContent = "—"; pnlEl.className = "big"; }
    return;
  }
  const pnl = today.realized_pnl;
  const pnlEl = document.getElementById("today-pnl");
  pnlEl.textContent = fmtMoney(pnl);
  pnlEl.className = "big " + (pnl == null ? "" : pnl >= 0 ? "ok" : "err");
  document.getElementById("today-trades").textContent = today.total_trades;
  document.getElementById("today-bs").textContent = `${today.buys} / ${today.sells}`;
  document.getElementById("today-net").textContent = fmtMoney(today.net_flow);
}

async function fetchFills() {
  const fills = await _fetchJSON("/api/recent-fills?limit=20");
  if (!fills) {
    // Clear stale rows so they don't persist behind the login overlay.
    const b = document.getElementById("fills-body");
    if (b) b.innerHTML = "";
    return;
  }
  const body = document.getElementById("fills-body");
  if (!fills.length) {
    body.innerHTML = '<tr><td colspan="8" class="muted-center">no fills yet</td></tr>';
  } else {
    body.innerHTML = fills.map(f => `
      <tr>
        <td>${esc((f.filled_at || "").replace("T", " ").slice(0, 19))}</td>
        <td>${f.strategy_name ? esc(f.strategy_name) : '<span class="muted">—</span>'}</td>
        <td>${esc(f.symbol)}</td>
        <td class="${f.action === "BUY" ? "ok" : "warn"}">${esc(f.action)}</td>
        <td class="num">${f.quantity}</td>
        <td class="num">${f.fill_price?.toFixed(4) ?? "—"}</td>
        <td class="num">${fmtMoney(f.fill_value)}</td>
        <td class="num ${f.realized_pnl == null ? "" : f.realized_pnl >= 0 ? "ok" : "err"}">${fmtMoney(f.realized_pnl)}</td>
      </tr>
    `).join("");
  }
}

async function fetchHealth() {
  const health = await _fetchJSON("/api/health");
  if (!health) return;
  const cls = health.status === "ok" ? "ok"
            : health.status === "stale" ? "warn" : "err";
  document.getElementById("health-status").innerHTML =
    `<span class="pulse ${cls === "ok" ? "" : cls}"></span><span class="${cls}">${esc(health.status)}</span>`;
  document.getElementById("health-last").textContent = health.last_tick || "—";
  document.getElementById("health-age").textContent = fmtAge(health.age_seconds);
}

async function fetchSystem() {
  const sys = await _fetchJSON("/api/system");
  if (!sys) return;

  const botStatus = sys.bot_service_status;
  const botOk = botStatus === "active";
  const botStopped = botStatus === "inactive" || botStatus === "failed";
  const botCls = botOk ? "ok" : (botStopped ? "err" : "");
  const botLabel = botOk ? "Active" : (botStatus === "failed" ? "Failed" : botStopped ? "Stopped" : botStatus);
  document.getElementById("sys-bot-status").innerHTML =
    `<span class="pulse ${botOk ? "" : "err"}"></span><span class="${botCls}">${esc(botLabel)}</span>`;

  const gwStatus = sys.gateway_service_status;
  const gwPortOpen = sys.gateway_port_open;
  const gwLoggedIn = gwStatus === "active" && gwPortOpen;
  const gwAwaiting = gwStatus === "active" && !gwPortOpen;
  const gwDown = gwStatus === "inactive" || gwStatus === "failed";
  const gwPulseCls = gwLoggedIn ? "" : (gwAwaiting ? "warn" : "err");
  const gwTextCls  = gwLoggedIn ? "ok" : (gwAwaiting ? "warn" : (gwDown ? "err" : ""));
  const gwLabel    = gwLoggedIn ? "Logged in" : (gwAwaiting ? "Awaiting login" : gwDown ? "Down" : gwStatus);
  document.getElementById("sys-gateway-status").innerHTML =
    `<span class="pulse ${gwPulseCls}"></span><span class="${gwTextCls}">${esc(gwLabel)}</span>`;
  document.getElementById("sys-bot-pid").textContent =
    sys.bot_pid != null ? sys.bot_pid : (botStatus === "unavailable" ? "n/a" : "—");
  document.getElementById("sys-bot-uptime").textContent = fmtAge(sys.bot_uptime_seconds);
  const p4 = sys.gateway_port_open;
  document.getElementById("sys-port4001").innerHTML =
    `<span class="${p4 ? "ok" : "err"}">${p4 ? "open" : "closed"}</span>`;

  const banner = document.getElementById("console-banner");
  if (sys.console_held_by && banner) {
    banner.textContent = `Gateway console held since ${sys.console_held_since || "unknown"}`;
    banner.classList.add("visible");
  } else if (banner) {
    banner.classList.remove("visible");
  }

  const consoleBtn = document.getElementById("btn-console");
  if (consoleBtn) {
    consoleBtn.disabled = !!sys.console_held_by;
  }
}

// ── IBKR Account tab ──────────────────────────────────────────────────────────

function _kpiClass(val) {
  if (val == null) return "kpi-zero";
  if (val > 0) return "kpi-positive";
  if (val < 0) return "kpi-negative";
  return "kpi-zero";
}

function _setKpiStale(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = "—";
  el.className = "kpi-value kpi-stale";
}

function renderKpis(snap) {
  if (!snap || snap.status !== "ok") {
    _setKpiStale("kpi-cash");
    _setKpiStale("kpi-unrealized");
    _setKpiStale("kpi-realized");
    return;
  }
  const s = snap.summary || {};

  // IBKR paper accounts don't populate SettledCash — TotalCashValue ("cash") is
  // the operationally meaningful value and is populated for both paper and live.
  const cash = document.getElementById("kpi-cash");
  if (cash) {
    cash.textContent = s.cash != null ? fmtUSD.format(s.cash) : "—";
    cash.className = "kpi-value kpi-zero";
  }
  const unreal = document.getElementById("kpi-unrealized");
  if (unreal) {
    unreal.textContent = s.unrealized_pnl != null ? fmtUSD.format(s.unrealized_pnl) : "—";
    unreal.className = "kpi-value " + _kpiClass(s.unrealized_pnl);
  }
  const real = document.getElementById("kpi-realized");
  if (real) {
    real.textContent = s.realized_pnl != null ? fmtUSD.format(s.realized_pnl) : "—";
    real.className = "kpi-value " + _kpiClass(s.realized_pnl);
  }
}

const _BALANCE_FIELDS = [
  ["net_liquidation",       "Net Liq"],
  ["equity_with_loan",      "EWL"],
  ["previous_day_ewl",      "Previous Day EWL"],
  ["regulation_t_ewl",      "Regulation T EWL"],
  ["sma",                   "SMA"],
  ["buying_power",          "Buying Power"],
  ["gross_position_value",  "Gross Position Value"],
  ["cash",                  "Cash"],
  ["settled_cash",          "Settled Cash"],
  ["available_funds",       "Available Funds"],
  ["initial_margin",        "Initial Margin"],
  ["maintenance_margin",    "Maintenance Margin"],
  ["excess_liquidity",      "Excess Liquidity"],
];

function renderBalances(snap) {
  const dl = document.getElementById("balances-list");
  if (!dl) return;
  if (!snap || snap.status !== "ok") {
    dl.innerHTML = "";
    return;
  }
  const s = snap.summary || {};
  dl.innerHTML = _BALANCE_FIELDS.map(([key, label]) => {
    const val = s[key];
    const display = val != null ? fmtUSD.format(val) : "—";
    return `<dt>${esc(label)}</dt><dd>${esc(display)}</dd>`;
  }).join("");
}

function renderPositions(snap) {
  const tbody = document.getElementById("positions-body");
  const emptyEl = document.getElementById("positions-empty");
  const table = document.getElementById("positions-table");
  if (!tbody) return;

  const positions = (snap && snap.status === "ok" && snap.positions) ? snap.positions : [];

  if (positions.length === 0) {
    tbody.innerHTML = "";
    if (emptyEl) emptyEl.removeAttribute("hidden");
    if (table) table.setAttribute("hidden", "");
    return;
  }

  if (emptyEl) emptyEl.setAttribute("hidden", "");
  if (table) table.removeAttribute("hidden");

  const pnlCls = (v) => v == null ? "" : v > 0 ? "kpi-positive" : v < 0 ? "kpi-negative" : "kpi-zero";

  tbody.innerHTML = positions.map(p => `
    <tr>
      <td>${esc(p.symbol)}<br><small>${esc(p.name || "")}</small></td>
      <td class="num">${p.position}</td>
      <td class="num">${p.market_value != null ? fmtUSD.format(p.market_value) : "—"}</td>
      <td class="num">${p.avg_cost != null ? fmtUSD.format(p.avg_cost) : "—"}</td>
      <td class="num ${pnlCls(p.unrealized_pnl)}">${p.unrealized_pnl != null ? fmtUSD.format(p.unrealized_pnl) : "—"}</td>
    </tr>
  `).join("");
}

function renderFreshness(snap) {
  const pill = document.getElementById("acct-freshness");
  if (!pill) return;
  const age = snap && snap.age_seconds;
  if (age == null) {
    pill.setAttribute("hidden", "");
    return;
  }
  pill.className = "freshness-pill";
  if (age > 300) {
    pill.removeAttribute("hidden");
    pill.classList.add("fresh-red");
    pill.textContent = "snapshot 5m+ old";
  } else if (age > 60) {
    pill.removeAttribute("hidden");
    pill.classList.add("fresh-orange");
    pill.textContent = "snapshot " + Math.round(age) + "s old";
  } else {
    pill.setAttribute("hidden", "");
  }
}

function checkAccountMismatch(snap) {
  const banner = document.getElementById("acct-mismatch");
  if (!banner) return;
  const snapAcct = snap && snap.account;
  if (
    snapAcct && _infoAccount &&
    snapAcct !== "unknown" && _infoAccount !== "unknown" &&
    snapAcct !== _infoAccount
  ) {
    // textContent assigns plain text — no XSS risk, esc() not needed here.
    banner.textContent =
      "Snapshot account " + snapAcct +
      " does not match dashboard account " + _infoAccount +
      ". Investigate immediately.";
    banner.removeAttribute("hidden");
  } else {
    banner.setAttribute("hidden", "");
  }
}

// Net liquidation value in the account dash header
function renderNetliq(snap) {
  const el = document.getElementById("acct-netliq");
  if (!el) return;
  const val = snap && snap.status === "ok" && snap.summary
    ? snap.summary.net_liquidation
    : null;
  el.textContent = val != null ? fmtUSD.format(val) : "—";
}

async function fetchAccount() {
  const snap = await _fetchJSON("/api/account");
  if (!snap) return;
  _lastAccountFetch = Date.now();
  renderKpis(snap);
  renderBalances(snap);
  renderPositions(snap);
  renderFreshness(snap);
  renderNetliq(snap);
  checkAccountMismatch(snap);
}

// ── Equity chart ──────────────────────────────────────────────────────────────

let _currentRange = "7";
let _lastEquityFetch = 0;
let _lastAccountFetch = 0;

function daysFor(range) {
  if (range === "7") return 7;
  if (range === "30") return 30;
  if (range === "mtd") {
    const now = new Date();
    return now.getUTCDate() - 1 || 1;
  }
  if (range === "ytd") {
    const now = new Date();
    const jan1 = new Date(Date.UTC(now.getUTCFullYear(), 0, 1));
    return Math.max(1, Math.floor((now - jan1) / 86400000));
  }
  // "all" or "365"
  return 365;
}

function renderChart(points) {
  const svg = document.getElementById("equity-chart");
  const line = document.getElementById("equity-line");
  const emptyEl = document.getElementById("chart-empty");
  if (!svg || !line) return;

  // Remove any previous no-data text element
  const oldText = svg.querySelector("text.chart-no-data");
  if (oldText) oldText.remove();

  if (!points || points.length < 2) {
    line.setAttribute("points", "");
    if (emptyEl) emptyEl.removeAttribute("hidden");
    return;
  }

  if (emptyEl) emptyEl.setAttribute("hidden", "");

  const vals = points.map(p => p.net_liq);
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const range = maxV - minV || 1;
  const n = points.length;

  const ptStr = points.map((p, i) => {
    const x = (i / (n - 1)) * 800;
    const y = 240 - ((p.net_liq - minV) / range) * 240;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");

  line.setAttribute("points", ptStr);
}

async function fetchEquity(days) {
  const r = await fetch(`/api/equity-history?days=${days}`, { credentials: "same-origin" });
  if (r.status === 401) {
    // Login overlay is triggered by fetchAccount already; just skip here
    return;
  }
  if (r.status === 429) {
    console.warn("equity-history: rate limited, skipping");
    return;
  }
  if (!r.ok) {
    console.warn("equity-history:", r.status);
    return;
  }
  const json = await r.json();
  _lastEquityFetch = Date.now();
  renderChart(json.points);
}

// ── Range chips ───────────────────────────────────────────────────────────────

function _activateChip(range) {
  _currentRange = range;
  const chips = document.querySelectorAll("[data-days]");
  chips.forEach(btn => {
    if (btn.getAttribute("data-days") === range) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });
}

function _initRangeChips() {
  const chips = document.querySelectorAll("[data-days]");
  chips.forEach(btn => {
    btn.addEventListener("click", () => {
      const range = btn.getAttribute("data-days");
      _activateChip(range);
      fetchEquity(daysFor(range));
    });
  });
}

// ── Strategies tab ────────────────────────────────────────────────────────────
//
// Field names below are pinned against `data/trade_log.py:lifetime_summary()`
// (verified at impl time, not retyped from memory). The /summary endpoint
// returns every key from `lifetime_summary` plus `realized_pnl_today`,
// `symbol`, and `schedule`.

function _fmtSchedule(sch) {
  if (!sch || !sch.kind) return "—";
  if (sch.kind === "DailyAt") {
    const hh = String(sch.hour).padStart(2, "0");
    const mm = String(sch.minute).padStart(2, "0");
    const tz = sch.tz === "America/New_York" ? "ET" : (sch.tz || "");
    return `Daily @ ${hh}:${mm} ${tz}`.trim();
  }
  if (sch.kind === "Interval") return `Every ${sch.seconds}s`;
  return "—";
}

function _fmtWinRate(v) {
  return v == null ? "—" : (v * 100).toFixed(1) + "%";
}

function _fmtRMultiple(v) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return sign + v.toFixed(2) + "R";
}

function _fmtProfitFactor(v) {
  if (v == null) return "—";
  // The server emits the string sentinel "Infinity" (or "-Infinity") when the
  // raw value is non-finite — FastAPI's default JSON encoder converts
  // float('inf') to null on the wire, so the helper at
  // data/trade_log.py:_round_profit_factor swaps to a string before return.
  // The Number Infinity literal is handled too in case a non-default encoder
  // ever lands. Either way → "∞".
  if (v === Infinity || v === "Infinity") return "∞";
  if (typeof v === "number" && !isFinite(v)) return "∞";
  if (typeof v === "number") return v.toFixed(2);
  return "—";
}

function _fmtRelTimeFromIso(iso) {
  if (!iso) return "never";
  // ISO timestamps from SQLite may be naive (no TZ suffix) — append Z to
  // force UTC parsing and avoid silent cross-browser inconsistency.
  let s = String(iso);
  if (!/[Zz]$|[+-]\d{2}:?\d{2}$/.test(s)) s = s + "Z";
  const t = Date.parse(s);
  if (Number.isNaN(t)) return "—";
  const ageSec = Math.max(0, (Date.now() - t) / 1000);
  return fmtAge(ageSec) + " ago";
}

function _kpiClassForPnl(v) {
  if (v == null) return "kpi-zero";
  if (v > 0) return "kpi-positive";
  if (v < 0) return "kpi-negative";
  return "kpi-zero";
}

function _setStratError(msg) {
  const pill = document.getElementById("strat-error");
  if (!pill) return;
  if (msg) {
    pill.textContent = msg;
    pill.removeAttribute("hidden");
  } else {
    pill.textContent = "";
    pill.setAttribute("hidden", "");
  }
}

function _setStratKpisStale() {
  const ids = [
    "strat-kpi-total-fills", "strat-kpi-closed", "strat-kpi-win-rate",
    "strat-kpi-pnl-life", "strat-kpi-pnl-today", "strat-kpi-pf",
    "strat-kpi-avg-r", "strat-kpi-last-fill",
  ];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = "—";
    el.className = "kpi-value kpi-stale";
  });
  const warn = document.getElementById("strat-legacy-warn");
  if (warn) { warn.setAttribute("hidden", ""); warn.textContent = ""; }
}

function renderStrategySummary(payload) {
  // Sub-header
  const title = document.getElementById("strat-title");
  const meta = document.getElementById("strat-meta");
  if (title) title.textContent = payload.strategy_name || _activeStrategy || "—";
  if (meta) {
    const sym = payload.symbol ? esc(payload.symbol) : "—";
    const sch = _fmtSchedule(payload.schedule);
    meta.textContent = `${sym} • ${sch}`;
  }

  // KPIs
  const setKpi = (id, text, cls) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.className = "kpi-value " + (cls || "");
  };

  setKpi("strat-kpi-total-fills", String(payload.total_fills ?? 0));
  setKpi("strat-kpi-closed", String(payload.sells_with_basis ?? 0));
  setKpi("strat-kpi-win-rate", _fmtWinRate(payload.win_rate),
         payload.win_rate == null ? "kpi-zero" : "");
  setKpi("strat-kpi-pnl-life",
         payload.realized_pnl_lifetime != null ? fmtUSD.format(payload.realized_pnl_lifetime) : "—",
         _kpiClassForPnl(payload.realized_pnl_lifetime));
  setKpi("strat-kpi-pnl-today",
         payload.realized_pnl_today != null ? fmtUSD.format(payload.realized_pnl_today) : "—",
         _kpiClassForPnl(payload.realized_pnl_today));
  setKpi("strat-kpi-pf", _fmtProfitFactor(payload.profit_factor),
         payload.profit_factor == null ? "kpi-zero" : "");
  setKpi("strat-kpi-avg-r", _fmtRMultiple(payload.avg_r_multiple),
         payload.avg_r_multiple == null ? "kpi-zero" : "");
  setKpi("strat-kpi-last-fill", _fmtRelTimeFromIso(payload.last_fill_at), "kpi-zero");

  // Legacy NULL-basis warning row — only shown when > 0.
  const warn = document.getElementById("strat-legacy-warn");
  if (warn) {
    const n = payload.legacy_null_basis_sells || 0;
    if (n > 0) {
      warn.textContent = `⚠ ${n} legacy fill(s) without cost basis — excluded from P&L aggregates`;
      warn.removeAttribute("hidden");
    } else {
      warn.setAttribute("hidden", "");
      warn.textContent = "";
    }
  }
}

async function fetchStrategies() {
  // Pinned to dashboard/app.py @app.get("/api/strategies")
  const list = await _fetchJSON("/api/strategies");
  return list;  // may be null on 401/non-2xx (login overlay fires in _fetchJSON)
}

async function fetchStrategySummary(name) {
  if (!name) return null;
  // Pinned to dashboard/app.py @app.get("/api/strategies/{name}/summary")
  const enc = encodeURIComponent(name);
  const payload = await _fetchJSON(`/api/strategies/${enc}/summary`);
  _lastStratFetch = Date.now();
  if (!payload) {
    _setStratKpisStale();
    _setStratError("Could not load summary for " + name);
    return null;
  }
  _setStratError("");
  renderStrategySummary(payload);
  return payload;
}

// ── Per-strategy history table ──────────────────────────────────────────────
//
// Fully decoupled from the 30s summary poll: only user actions trigger fetches.
// AbortController is replaced (not reused) on each call so a late response
// from a superseded fetch cannot render over a newer one.

function _setHistoryRowsPlaceholder(text) {
  const body = document.getElementById("strat-history-body");
  // colspan derived from the column constant so it can't drift on reorders
  // or insertions. The static initial row in index.html keeps the literal
  // matching value, locked by test_ds69.
  const span = _STRAT_HISTORY_COLS.length;
  if (body) body.innerHTML = `<tr><td colspan="${span}" class="muted-center">${esc(text)}</td></tr>`;
}

function _fmtHistoryCell(key, value) {
  if (value === null || value === undefined) return "—";
  if (key === "filled_at") return String(value).replace("T", " ").slice(0, 19);
  if (key === "action") return String(value);
  if (key === "quantity") return Number(value).toLocaleString();
  if (key === "fill_price" || key === "cost_basis") return fmtUSD.format(Number(value));
  if (key === "realized_pnl") {
    const n = Number(value);
    return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2);
  }
  if (key === "real_r_multiple") return Number(value).toFixed(2) + "R";
  if (key === "strategy_params") {
    // Server parses JSON server-side; may arrive as object or string.
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }
  return String(value);
}

function _renderHistoryRows(fills) {
  const body = document.getElementById("strat-history-body");
  if (!body) return;
  if (!fills.length) {
    // "No fills yet" only when total is also zero. If total > 0 but this
    // page returned [], the user paginated past the last filled page —
    // e.g. a deletion race or hitting the offset cap on a partial last page.
    const text = _stratHistoryTotal > 0 ? "No fills on this page" : "No fills yet";
    _setHistoryRowsPlaceholder(text);
    return;
  }
  const rows = fills.map(f => {
    const cells = _STRAT_HISTORY_COLS.map(([k]) => {
      const text = esc(_fmtHistoryCell(k, f[k]));
      const num = (k === "quantity" || k === "fill_price" || k === "cost_basis" ||
                   k === "realized_pnl" || k === "real_r_multiple");
      const cls = k === "strategy_params" ? "params" : (num ? "num" : "");
      // Params column gets a title so the truncated JSON is visible on hover.
      const title = k === "strategy_params" ? ` title="${text}"` : "";
      return `<td${cls ? ` class="${cls}"` : ""}${title}>${text}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  });
  body.innerHTML = rows.join("");
}

function _renderHistoryPager() {
  const total = _stratHistoryTotal;
  const offset = _stratHistoryOffset;
  const limit = _stratHistoryLimit;
  const status = document.getElementById("strat-history-status");
  const pageEl = document.getElementById("strat-history-page");
  const prev = document.getElementById("strat-history-prev");
  const next = document.getElementById("strat-history-next");

  if (status) {
    if (total === 0) {
      status.textContent = "0 fills";
    } else {
      const from = offset + 1;
      const to = Math.min(offset + limit, total);
      status.textContent = `Showing ${from}-${to} of ${total}`;
    }
  }
  if (pageEl) {
    const pageNum = Math.floor(offset / limit) + 1;
    const pageCount = Math.max(1, Math.ceil(total / limit));
    pageEl.textContent = `Page ${pageNum} of ${pageCount}`;
  }
  if (prev) prev.disabled = offset <= 0;
  if (next) {
    // Next is disabled at the server's offset cap even if more rows exist.
    // Single rule: disabled iff no more rows OR we've already reached the cap.
    // Matches the click-handler's `candidate > _STRAT_OFFSET_CAP` guard so the
    // two gates can't disagree and strand the user one page before the cap.
    const moreRowsExist = offset + limit < total;
    next.disabled = !moreRowsExist || offset >= _STRAT_OFFSET_CAP;
  }
}

async function fetchStrategyFills(name) {
  if (!name) return null;
  // Abort any in-flight fetch and replace the controller — covers every
  // mutation path (strategy switch, page change, page-size change). The
  // replacement must happen BEFORE the fetch so a late prior response sees
  // the OLD aborted signal and is dropped silently.
  if (_stratHistoryAbort) {
    try { _stratHistoryAbort.abort(); } catch (_) { /* ignore */ }
  }
  _stratHistoryAbort = new AbortController();
  const myAbort = _stratHistoryAbort;

  const table = document.getElementById("strat-history");
  if (table) table.setAttribute("aria-busy", "true");

  const enc = encodeURIComponent(name);
  const offset = _stratHistoryOffset;
  const limit = _stratHistoryLimit;
  // Pinned to dashboard/app.py @app.get("/api/strategies/{name}/fills")
  const url = `/api/strategies/${enc}/fills?limit=${limit}&offset=${offset}`;
  let payload = null;
  try {
    const r = await fetch(url, { credentials: "same-origin", signal: myAbort.signal });
    if (r.status === 401) {
      showLogin("Session expired — please log in again.");
      return null;
    }
    if (!r.ok) {
      console.warn("fetchStrategyFills", url, "->", r.status);
      _setHistoryRowsPlaceholder("Failed to load fills");
      return null;
    }
    payload = await r.json();
  } catch (e) {
    if (e && e.name === "AbortError") return null;  // superseded; silent drop
    console.warn("fetchStrategyFills", e);
    _setHistoryRowsPlaceholder("Failed to load fills");
    return null;
  } finally {
    // Only clear busy state if we're still the current request.
    if (myAbort === _stratHistoryAbort && table) {
      table.setAttribute("aria-busy", "false");
    }
  }

  // Drop late responses (a newer fetch may have superseded us).
  if (myAbort !== _stratHistoryAbort) return null;

  _stratHistoryTotal = Number(payload.total ?? 0);
  _renderHistoryRows(payload.fills || []);
  _renderHistoryPager();
  return payload;
}

function _wireHistoryControls() {
  const prev = document.getElementById("strat-history-prev");
  const next = document.getElementById("strat-history-next");
  const size = document.getElementById("strat-history-pagesize");
  if (prev) prev.addEventListener("click", () => {
    if (!_activeStrategy) return;
    _stratHistoryOffset = Math.max(0, _stratHistoryOffset - _stratHistoryLimit);
    fetchStrategyFills(_activeStrategy);
  });
  if (next) next.addEventListener("click", () => {
    if (!_activeStrategy) return;
    const candidate = _stratHistoryOffset + _stratHistoryLimit;
    if (candidate > _STRAT_OFFSET_CAP) return;  // honour server cap
    _stratHistoryOffset = candidate;
    fetchStrategyFills(_activeStrategy);
  });
  if (size) size.addEventListener("change", () => {
    if (!_activeStrategy) return;
    const v = parseInt(size.value, 10);
    if (!Number.isFinite(v) || v <= 0) return;
    _stratHistoryLimit = v;
    _stratHistoryOffset = 0;  // page-size change resets pagination
    fetchStrategyFills(_activeStrategy);
  });
}

function _setActiveStrategy(name) {
  // Validate against the cached list — a stale sessionStorage key from a
  // renamed/deleted strategy must NOT silently activate.
  const valid = (_stratList || []).some(s => s.name === name);
  if (!valid) {
    if (_stratList && _stratList.length) {
      name = _stratList[0].name;
    } else {
      _activeStrategy = null;
      return;
    }
  }
  _activeStrategy = name;
  try { sessionStorage.setItem(_STRAT_STORAGE_KEY, name); } catch (_) { /* private mode */ }

  // Update aria-selected on the secondary tab strip
  document.querySelectorAll("#strat-tablist .strat-tab").forEach(btn => {
    const isMe = btn.getAttribute("data-strat") === name;
    btn.setAttribute("aria-selected", isMe ? "true" : "false");
    btn.setAttribute("tabindex", isMe ? "0" : "-1");
  });

  // Fetch summary for this strategy immediately
  fetchStrategySummary(name);
  // History pagination resets on strategy switch; history fetch is fully
  // decoupled from the summary poll so a 30s summary refresh does NOT
  // re-render the table out from under the user.
  _stratHistoryOffset = 0;
  fetchStrategyFills(name);
}

function _renderStratTabs(list) {
  const tablist = document.getElementById("strat-tablist");
  const empty = document.getElementById("strat-empty");
  if (!tablist) return;

  // Clear any prior buttons but keep the empty-state placeholder element.
  Array.from(tablist.querySelectorAll(".strat-tab")).forEach(b => b.remove());

  if (!list || list.length === 0) {
    if (empty) empty.removeAttribute("hidden");
    _setStratKpisStale();
    return;
  }
  if (empty) empty.setAttribute("hidden", "");

  list.forEach((s, idx) => {
    const btn = document.createElement("button");
    btn.className = "strat-tab";
    btn.setAttribute("role", "tab");
    btn.setAttribute("data-strat", s.name);
    btn.setAttribute("aria-selected", "false");
    btn.setAttribute("tabindex", "-1");
    btn.textContent = s.name;
    btn.addEventListener("click", () => _setActiveStrategy(s.name));
    btn.addEventListener("keydown", e => {
      // Circular left/right + Home/End across the secondary strip
      const buttons = Array.from(tablist.querySelectorAll(".strat-tab"));
      const i = buttons.indexOf(btn);
      let target = -1;
      if (e.key === "ArrowRight") target = (i + 1) % buttons.length;
      else if (e.key === "ArrowLeft") target = (i - 1 + buttons.length) % buttons.length;
      else if (e.key === "Home") target = 0;
      else if (e.key === "End") target = buttons.length - 1;
      else if (e.key === "Enter" || e.key === " ") { btn.click(); e.preventDefault(); return; }
      if (target >= 0) {
        buttons[target].focus();
        _setActiveStrategy(buttons[target].getAttribute("data-strat"));
        e.preventDefault();
      }
    });
    tablist.appendChild(btn);
    void idx;  // index reserved for future ordering work
  });
}

async function _initStrategyTabs() {
  // Must run AFTER login (the session cookie gate). Called lazily on first
  // activation of #tab-strats, NOT at DOMContentLoaded — pre-login the
  // /api/strategies endpoint 401s.
  const list = await fetchStrategies();
  if (!list) {
    // 401 path: _fetchJSON already triggered the login overlay. Mark
    // un-inited so the next activation retries.
    _stratTabsInited = false;
    return;
  }
  _stratList = list;
  _renderStratTabs(list);

  if (list.length === 0) {
    _activeStrategy = null;
    _stratTabsInited = true;
    return;
  }

  // Restore previously-active strategy if it still exists; otherwise first.
  let initial = null;
  try { initial = sessionStorage.getItem(_STRAT_STORAGE_KEY); } catch (_) { /* private mode */ }
  if (!initial || !list.some(s => s.name === initial)) initial = list[0].name;
  _setActiveStrategy(initial);
  _stratTabsInited = true;
}

// ── Tab handler ───────────────────────────────────────────────────────────────
//
// N-tab pattern: each entry is { id, panel, onActivate, onDeactivate }.
// `body.dataset.tab` mirrors the active tab so CSS rules
// (e.g. `body[data-tab="strats"] > .kpi-strip { display: none }`) can react.

const _TABS = [
  {
    id: "tab-mc", panel: "panel-mc", key: "mc",
    onActivate: () => { _onAcctTab = false; _onStratTab = false; },
  },
  {
    id: "tab-acct", panel: "panel-acct", key: "acct",
    onActivate: () => {
      _onAcctTab = true; _onStratTab = false;
      // Fetch immediately so the tab feels live, not blank for up to 30s.
      fetchAccount();
      fetchEquity(daysFor(_currentRange));
    },
  },
  {
    id: "tab-strats", panel: "panel-strats", key: "strats",
    onActivate: () => {
      _onAcctTab = false; _onStratTab = true;
      if (!_stratTabsInited) {
        _initStrategyTabs();
      } else if (_activeStrategy) {
        // Returning to the tab — refresh immediately (cache-friendly).
        fetchStrategySummary(_activeStrategy);
      }
    },
  },
];

function _selectTab(tabId) {
  const tabs = _TABS.map(t => ({
    ...t,
    btn: document.getElementById(t.id),
    panelEl: document.getElementById(t.panel),
  }));
  if (tabs.some(t => !t.btn || !t.panelEl)) return;

  let activated = null;
  tabs.forEach(t => {
    const isActive = t.id === tabId;
    t.btn.setAttribute("aria-selected", isActive ? "true" : "false");
    t.btn.setAttribute("tabindex", isActive ? "0" : "-1");
    if (isActive) {
      t.panelEl.removeAttribute("hidden");
      activated = t;
    } else {
      t.panelEl.setAttribute("hidden", "");
    }
  });
  if (!activated) return;

  document.body.dataset.tab = activated.key;
  activated.btn.focus();
  try { activated.onActivate && activated.onActivate(); } catch (e) { console.warn("tab activate", e); }
}

function _initTabs() {
  const buttons = _TABS.map(t => document.getElementById(t.id)).filter(Boolean);
  if (buttons.length < 2) return;

  buttons.forEach(btn => {
    btn.addEventListener("click", () => _selectTab(btn.id));
    btn.addEventListener("keydown", e => {
      const i = buttons.indexOf(btn);
      let target = -1;
      if (e.key === "ArrowRight") target = (i + 1) % buttons.length;
      else if (e.key === "ArrowLeft") target = (i - 1 + buttons.length) % buttons.length;
      else if (e.key === "Home") target = 0;
      else if (e.key === "End") target = buttons.length - 1;
      else if (e.key === "Enter" || e.key === " ") { btn.click(); e.preventDefault(); return; }
      if (target >= 0) {
        _selectTab(buttons[target].id);
        e.preventDefault();
      }
    });
  });
}

// ── Main polling loop ─────────────────────────────────────────────────────────

async function refresh() {
  try {
    // Account snapshot: poll every 30s regardless of tab. The bot writes the
    // file every 30s, so faster polling is wasted; the KPI strip is always
    // visible (Mission Control + IBKR Account tab both show it). On the IBKR
    // tab _selectTab triggers an immediate fetch so the tab feels live.
    const accountDue = Date.now() - _lastAccountFetch >= 30000;

    await Promise.all([
      fetchHealth(),
      fetchToday(),
      fetchFills(),
      fetchSystem(),
      ...(accountDue ? [fetchAccount()] : []),
    ]);

    // Equity: only when tab is active AND 30s have elapsed since last fetch
    if (_onAcctTab && Date.now() - _lastEquityFetch >= 30000) {
      fetchEquity(daysFor(_currentRange));
    }

    // Strategies summary: only when tab is active AND visible AND 30s have
    // elapsed. The endpoint has a 30s server-side TTL cache, so polling
    // faster is wasted; the visibility gate avoids burning RAM on a
    // background-tab dashboard left open overnight.
    if (
      _onStratTab &&
      _activeStrategy &&
      document.visibilityState === "visible" &&
      Date.now() - _lastStratFetch >= _STRAT_POLL_MS
    ) {
      fetchStrategySummary(_activeStrategy);
    }

    document.getElementById("footer").textContent =
      "last refresh: " + new Date().toISOString().replace("T", " ").slice(0, 19) + "Z";
  } catch (e) {
    document.getElementById("footer").textContent = "refresh error: " + e.message;
  }
}

// ── Control plane (Phase 3) — session-cookie auth (CR-10) ────────────────────

const msgEl   = document.getElementById("ctrl-msg");
const overlay  = document.getElementById("login-overlay");
const loginInput = document.getElementById("login-input");
const loginErr   = document.getElementById("login-err");

function setMsg(text, cls) {
  msgEl.textContent = text;
  msgEl.className = "ctrl-msg" + (cls ? " " + cls : "");
}
function showLogin(hint) {
  loginErr.textContent = hint || "";
  loginInput.value = "";
  overlay.classList.add("visible");
  loginInput.focus();
}
function hideLogin() { overlay.classList.remove("visible"); }

async function postAction(path, label) {
  if (!confirm(`${label} the live bot?`)) { setMsg("cancelled", ""); return; }
  setMsg(`${label}…`, "");
  try {
    const r = await fetch(path, { method: "POST", credentials: "same-origin" });
    const body = await r.json().catch(() => ({}));
    if (r.status === 401 || r.status === 403) { showLogin("Session expired — please log in again."); return; }
    if (!r.ok) { setMsg(`${esc(label)} failed: ${esc(body.detail || String(r.status))}`, "err"); return; }
    setMsg(`${label} ok`, "ok");
  } catch (e) {
    setMsg(`${label} error: ${esc(e.message)}`, "err");
  }
}

document.getElementById("btn-restart").addEventListener("click",
  () => postAction("/api/bot/restart", "Restart"));
document.getElementById("btn-stop").addEventListener("click",
  () => postAction("/api/bot/stop", "Stop"));
document.getElementById("btn-logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  showLogin("Logged out.");
});

const consoleBtn = document.getElementById("btn-console");
if (consoleBtn) {
  consoleBtn.addEventListener("click", () => {
    // Open in a sized OS window — keeps the dashboard out of the noVNC blast
    // radius (separate top-level window, no iframe / sandbox / CSP gymnastics).
    // The console page handles step-up auth, lock acquire, and unload-time
    // release on its own — closing the window fires beforeunload/pagehide
    // which sendBeacon's the lock release back to the server.
    // Note: noopener/noreferrer would force window.open() to return null,
    // breaking the popup-blocked detection below. The console page is
    // same-origin trusted code, so dropping noopener is acceptable here.
    // Using "_blank" (not a fixed name) means each click opens a fresh
    // window instead of focusing a stale one with possibly-wrong URL.
    const features = "popup=yes,width=960,height=680,resizable=yes,scrollbars=no";
    const w = window.open("/console.html", "_blank", features);
    if (!w) {
      setMsg("popup blocked — allow popups for this site", "err");
    } else {
      // Best-effort: detach the popup's reference back to us so console code
      // can't reach into the dashboard via window.opener. Same-origin still
      // permits cookie+fetch access, but window.opener-based DOM access goes
      // away. We retain `w` locally for the popup-blocked check above.
      try { w.opener = null; } catch (_) { /* cross-origin or already null */ }
      setMsg("", "");
    }
  });
}

const btnLogin = document.getElementById("btn-login");
if (btnLogin) {
  btnLogin.addEventListener("click", async () => {
    // Logout first so the prior session cookie + step-up tokens are revoked
    // server-side before the new login overlay shows. Mirrors btn-logout flow.
    // If logout fails we MUST NOT show the overlay — the user could submit
    // a new token while the old session is still valid server-side.
    try {
      const r = await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
      if (!r.ok) {
        setMsg(`logout failed (${r.status}) — reload before re-logging in`, "err");
        return;
      }
    } catch (e) {
      setMsg(`logout error: ${esc(e.message)} — reload before re-logging in`, "err");
      return;
    }
    showLogin("");
  });
}

document.getElementById("login-btn").addEventListener("click", async () => {
  const token = loginInput.value.trim();
  if (!token) { loginErr.textContent = "enter token"; return; }
  const r = await fetch("/api/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (r.ok) { hideLogin(); setMsg("logged in", "ok"); }
  else { loginErr.textContent = "invalid token"; loginInput.select(); }
});
loginInput.addEventListener("keydown", e => { if (e.key === "Enter") document.getElementById("login-btn").click(); });

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  _initTabs();
  _initRangeChips();
  _wireHistoryControls();
  _activateChip("7");
  // Fetch info first so _infoAccount is populated before fetchAccount runs
  fetchInfo().then(() => {
    refresh();
    setInterval(refresh, 5000);
  });
});
