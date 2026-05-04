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

async function refresh() {
  try {
    const [health, today, fills, info, sys] = await Promise.all([
      fetch("/api/health").then(r => r.json()),
      fetch("/api/today").then(r => r.json()),
      fetch("/api/recent-fills?limit=20").then(r => r.json()),
      fetch("/api/info").then(r => r.json()),
      fetch("/api/system").then(r => r.json()),
    ]);

    // Health
    const cls = health.status === "ok" ? "ok"
              : health.status === "stale" ? "warn" : "err";
    document.getElementById("health-status").innerHTML =
      `<span class="pulse ${cls === 'ok' ? '' : cls}"></span><span class="${cls}">${esc(health.status)}</span>`;
    document.getElementById("health-last").textContent = health.last_tick || "—";
    document.getElementById("health-age").textContent = fmtAge(health.age_seconds);

    // Today
    const pnl = today.realized_pnl;
    const pnlEl = document.getElementById("today-pnl");
    pnlEl.textContent = fmtMoney(pnl);
    pnlEl.className = "big " + (pnl == null ? "" : pnl >= 0 ? "ok" : "err");
    document.getElementById("today-trades").textContent = today.total_trades;
    document.getElementById("today-bs").textContent = `${today.buys} / ${today.sells}`;
    document.getElementById("today-net").textContent = fmtMoney(today.net_flow);

    // Info
    document.getElementById("info-account").textContent = info.account;
    document.getElementById("info-host").textContent = info.host;
    document.getElementById("info-port").textContent = info.port;
    document.getElementById("info-started").textContent = info.dashboard_started_at;

    // Fills
    const body = document.getElementById("fills-body");
    if (!fills.length) {
      body.innerHTML = '<tr><td colspan="7" class="muted-center">no fills yet</td></tr>';
    } else {
      body.innerHTML = fills.map(f => `
        <tr>
          <td>${esc((f.filled_at || "").replace("T", " ").slice(0, 19))}</td>
          <td>${esc(f.symbol)}</td>
          <td class="${f.action === 'BUY' ? 'ok' : 'warn'}">${esc(f.action)}</td>
          <td class="num">${f.quantity}</td>
          <td class="num">${f.fill_price?.toFixed(4) ?? "—"}</td>
          <td class="num">${fmtMoney(f.fill_value)}</td>
          <td class="num ${f.realized_pnl == null ? '' : f.realized_pnl >= 0 ? 'ok' : 'err'}">${fmtMoney(f.realized_pnl)}</td>
        </tr>
      `).join("");
    }

    // System — bot status
    const botStatus = sys.bot_service_status;
    const botOk = botStatus === "active";
    const botStopped = botStatus === "inactive" || botStatus === "failed";
    const botCls = botOk ? "ok" : (botStopped ? "err" : "");
    const botLabel = botOk ? "Active" : (botStatus === "failed" ? "Failed" : botStopped ? "Stopped" : botStatus);
    document.getElementById("sys-bot-status").innerHTML =
      `<span class="pulse ${botOk ? '' : 'err'}"></span><span class="${botCls}">${esc(botLabel)}</span>`;

    // System — gateway status (active + port open = logged in; active + port closed = awaiting 2FA)
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
      `<span class="${p4 ? 'ok' : 'err'}">${p4 ? "open" : "closed"}</span>`;

    // Console session banner — visible to all dashboard sessions when one operator
    // holds the gateway console (single-session lock).
    const banner = document.getElementById("console-banner");
    if (sys.console_held_by && banner) {
      banner.textContent = `Gateway console held since ${sys.console_held_since || "unknown"}`;
      banner.classList.add("visible");
    } else if (banner) {
      banner.classList.remove("visible");
    }

    // "Open Gateway Console" button is always visible (gateway state is shown
    // in the System card; no need to hide the entry point). Disabled only when
    // another operator already holds the single-session console lock.
    const consoleBtn = document.getElementById("btn-console");
    if (consoleBtn) {
      consoleBtn.disabled = !!sys.console_held_by;
    }

    document.getElementById("footer").textContent =
      "last refresh: " + new Date().toISOString().replace("T", " ").slice(0, 19) + "Z";
  } catch (e) {
    document.getElementById("footer").textContent = "refresh error: " + e.message;
  }
}

refresh();
setInterval(refresh, 5000);

// ---- Control plane (Phase 3) — session-cookie auth (CR-10) -------------
const msgEl  = document.getElementById("ctrl-msg");
const overlay = document.getElementById("login-overlay");
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
    const features = "popup=yes,width=960,height=680,resizable=yes,scrollbars=no,noopener,noreferrer";
    const w = window.open("/console.html", "tradebot-console", features);
    if (!w) setMsg("popup blocked — allow popups for this site", "err");
  });
}

const btnLogin = document.getElementById("btn-login");
if (btnLogin) {
  btnLogin.addEventListener("click", async () => {
    // Logout first so the prior session cookie + step-up tokens are revoked
    // server-side before the new login overlay shows. Mirrors btn-logout flow.
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
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
