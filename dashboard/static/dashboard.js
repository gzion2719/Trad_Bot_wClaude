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

// ── Tab handler ───────────────────────────────────────────────────────────────

function _selectTab(tabId) {
  const mc   = document.getElementById("tab-mc");
  const acct = document.getElementById("tab-acct");
  const panelMc   = document.getElementById("panel-mc");
  const panelAcct = document.getElementById("panel-acct");
  if (!mc || !acct || !panelMc || !panelAcct) return;

  const isAcct = tabId === "tab-acct";

  mc.setAttribute("aria-selected", isAcct ? "false" : "true");
  mc.setAttribute("tabindex", isAcct ? "-1" : "0");
  acct.setAttribute("aria-selected", isAcct ? "true" : "false");
  acct.setAttribute("tabindex", isAcct ? "0" : "-1");

  if (isAcct) {
    _onAcctTab = true;
    panelMc.setAttribute("hidden", "");
    panelAcct.removeAttribute("hidden");
    acct.focus();
    // Fetch immediately so the tab feels live, not blank for up to 30s.
    fetchAccount();
    fetchEquity(daysFor(_currentRange));
  } else {
    _onAcctTab = false;
    panelAcct.setAttribute("hidden", "");
    panelMc.removeAttribute("hidden");
    mc.focus();
  }
}

function _initTabs() {
  const mc   = document.getElementById("tab-mc");
  const acct = document.getElementById("tab-acct");
  if (!mc || !acct) return;

  mc.addEventListener("click", () => _selectTab("tab-mc"));
  acct.addEventListener("click", () => _selectTab("tab-acct"));

  // Arrow key navigation between tabs
  [mc, acct].forEach(btn => {
    btn.addEventListener("keydown", e => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const target = btn === mc ? "tab-acct" : "tab-mc";
        _selectTab(target);
        e.preventDefault();
      }
      if (e.key === "Enter" || e.key === " ") {
        btn.click();
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
  _activateChip("7");
  // Fetch info first so _infoAccount is populated before fetchAccount runs
  fetchInfo().then(() => {
    refresh();
    setInterval(refresh, 5000);
  });
});
