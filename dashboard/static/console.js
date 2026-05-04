// Gateway console front-end. CSP-strict: no inline handlers, no remote origins.
// Loaded as a module so we can `import` from the vendored noVNC bundle.
//
// Flow:
//   1. User arrives at /console.html with a valid dashboard session cookie.
//   2. Step-up password challenge → POST /api/console/login → console_token cookie.
//   3. POST /api/console/acquire → take the single-session lock.
//   4. Open WebSocket to /ws/console (subprotocol "binary") → noVNC takes over.
//   5. On disconnect or page unload → POST /api/console/release.

import RFB from "/static/vendor/novnc/core/rfb.js";

const stepUpCard = document.getElementById("step-up-card");
const canvasWrap = document.getElementById("canvas-wrap");
const stateEl    = document.getElementById("console-state");
const msgEl      = document.getElementById("console-msg");
const stepUpErr  = document.getElementById("step-up-err");
const passwordInput = document.getElementById("console-password");

let rfb = null;
let lockHeld = false;

function setState(text) { stateEl.textContent = text; }
function setMsg(text)   { msgEl.textContent = text; }
function setErr(text)   { stepUpErr.textContent = text; }

async function stepUp() {
  setErr("");
  const password = passwordInput.value;
  if (!password) { setErr("enter password"); return; }
  try {
    const r = await fetch("/api/console/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      setErr(body.detail || `step-up failed (${r.status})`);
      return;
    }
    passwordInput.value = ""; // clear from DOM memory
    await acquireAndConnect();
  } catch (e) {
    setErr("network error: " + e.message);
  }
}

async function acquireAndConnect() {
  // 1. Take the lock
  const acq = await fetch("/api/console/acquire", {
    method: "POST",
    credentials: "same-origin",
  });
  if (acq.status === 409) {
    const body = await acq.json().catch(() => ({}));
    const detail = body.detail || {};
    setErr(`Console held by another session since ${detail.held_since || "earlier"}. Try again later.`);
    return;
  }
  if (!acq.ok) {
    setErr(`acquire failed (${acq.status})`);
    return;
  }
  lockHeld = true;

  // 2. Switch UI to canvas mode
  stepUpCard.classList.add("hidden");
  canvasWrap.classList.add("visible");
  setState("connecting…");

  // 3. Open the WebSocket (cookies travel automatically; subprotocol must
  //    match the server's accept(subprotocol="binary"))
  const wsScheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${wsScheme}//${window.location.host}/ws/console`;

  rfb = new RFB(document.getElementById("novnc-canvas"), wsUrl, {
    credentials: { password: "" },  // VNC password injected by websockify side
    wsProtocols: ["binary"],
  });

  // x11vnc requires a password; we don't know it on the client. The server-side
  // websockify could inject it, but the simpler design is to let x11vnc accept
  // the password in the protocol exchange — we leave the credentials.password
  // empty here and let noVNC prompt the user via its own dialog if needed.
  // Operator workflow: enter the same console password (also stored in
  // /etc/x11vnc.pass on the VPS) when noVNC asks.

  rfb.viewOnly = false;
  rfb.scaleViewport = true;
  rfb.resizeSession = false;
  rfb.clipViewport = false;

  rfb.addEventListener("connect", () => {
    setState("connected");
    setMsg("Use IB Gateway as if you were on a local screen. Idle for 5 min disconnects.");
    // noVNC computes its scale once during construction. If the container
    // hasn't completed flex layout by then, scaleViewport collapses the
    // canvas to 0x0. Toggle scaleViewport so noVNC recomputes after layout.
    requestAnimationFrame(() => {
      if (!rfb) return;
      rfb.scaleViewport = false;
      rfb.scaleViewport = true;
    });
  });

  // Keep canvas scaled correctly when the browser viewport changes.
  const wrap = document.getElementById("novnc-canvas");
  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      if (!rfb) return;
      rfb.scaleViewport = false;
      rfb.scaleViewport = true;
    }).observe(wrap);
  }
  rfb.addEventListener("disconnect", (e) => {
    setState("disconnected");
    const reason = e.detail && e.detail.clean ? "clean" : "error";
    setMsg(`disconnected (${reason}). Click "Back to dashboard" or refresh to reconnect.`);
    releaseLock();
  });
  rfb.addEventListener("credentialsrequired", () => {
    const pw = prompt("VNC password (see /etc/x11vnc.pass on the VPS):");
    if (pw !== null) rfb.sendCredentials({ password: pw });
  });
  rfb.addEventListener("securityfailure", (e) => {
    setMsg(`security failure: ${e.detail?.reason || "unknown"}`);
  });
}

async function releaseLock() {
  if (!lockHeld) return;
  lockHeld = false;
  try {
    await fetch("/api/console/release", {
      method: "POST",
      credentials: "same-origin",
    });
  } catch (_) { /* best effort */ }
}

document.getElementById("btn-step-up").addEventListener("click", stepUp);
passwordInput.addEventListener("keydown", e => { if (e.key === "Enter") stepUp(); });
function closeOrNavigate() {
  // Popup case (most common — opened by dashboard.js): close ourselves so the
  // user isn't left with two dashboard windows. Direct-load case: fall back
  // to navigation so /console.html still has a way back to the dashboard.
  if (window.opener && !window.opener.closed) {
    window.close();
  } else {
    window.location.href = "/";
  }
}
document.getElementById("btn-disconnect").addEventListener("click", () => {
  if (rfb) rfb.disconnect();
  releaseLock();
  closeOrNavigate();
});
const backLink = document.querySelector('.console-actions a[href="/"]');
if (backLink) {
  backLink.addEventListener("click", e => { e.preventDefault(); closeOrNavigate(); });
}

function releaseBeacon() {
  // Best-effort lock release on tab/window close — sendBeacon survives unload.
  // Listening on both beforeunload and pagehide because beforeunload is
  // skipped on bfcache restores and on some mobile-Safari close paths.
  if (lockHeld) {
    navigator.sendBeacon("/api/console/release");
  }
}
window.addEventListener("beforeunload", releaseBeacon);
window.addEventListener("pagehide", releaseBeacon);

setState("awaiting step-up");
