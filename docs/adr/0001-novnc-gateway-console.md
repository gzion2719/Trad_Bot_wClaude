# ADR-0001 — Embed noVNC in the dashboard for IB Gateway 2FA login

- **Status:** Proposed
- **Date:** 2026-05-03
- **Deciders:** Project owner
- **Supersedes:** —

## Context

The bot relies on IB Gateway being logged in. IBKR invalidates all 2FA tokens
once per week (Sunday ~01:00 ET); IBC restarts the gateway and presents a
login dialog that requires a fresh 6-digit code from the operator's IBKR Mobile
app (Interactive IL Key — Israeli code generator). The current recovery path
requires the operator to:

1. SSH to the VPS over Tailscale
2. Open a second SSH tunnel `ssh -L 5900:localhost:5900 chappy-vps`
3. Connect TightVNC to `localhost:5900`
4. Enter the code in the gateway's login dialog

This works but has friction: requires TightVNC installed locally, two terminal
windows, and one minute of manual ceremony every week. It also makes backup
operator handoff harder (CR-03 — see `docs/runbook-2fa-recovery.md`).

Goal: **let the operator complete the weekly 2FA login from a browser tab on
any Tailnet device, without VNC client software.**

## Considered options

### Option A — xdotool keystroke injection from a dashboard endpoint

`POST /api/gateway/2fa` receives the 6-digit code, dashboard backend shells out
to `xdotool type "$code"` against display `:99`.

Rejected because:
- Backend handles the 2FA code in plaintext — leak surface in logs, error
  paths, subprocess args
- Display `:99` is owned by `root` (IB Gateway runs `User=root`), dashboard
  runs as `tradebot` — requires solving xauth or running xdotool via sudo
- Blind keystroke injection without explicit window targeting could send
  digits to the wrong window if dialog state is unexpected
- Brittle to any future gateway dialog change (cert prompt, version update
  nag, error toast)
- Custom code: ~80 LoC of security-critical glue

### Option B — Embed noVNC in the dashboard, reverse-proxy WebSocket through FastAPI to websockify→x11vnc

**Recommended.** Browser ⇄ dashboard `/ws/console` (WebSocket, cookie+step-up
auth) ⇄ websockify (127.0.0.1:6080) ⇄ x11vnc (127.0.0.1:5900) ⇄ Xvfb :99 ⇄
IB Gateway. Operator enters the 2FA code in a real gateway dialog rendered in
the browser via noVNC's HTML5 canvas client.

### Option C — Wait for IBKR push-notification IB Key

Pending support inquiry (5.16). If approved, IBKR Mobile push answers 2FA with
zero UI. Inquiry has been **drafted but not sent**; response time historically
weeks. Not a near-term solution; sending the inquiry in parallel is no-cost.

### Option D — Status quo (SSH+VNC tunnel)

Acceptable today; CR-03 runbook covers it. Doesn't solve the friction goal but
remains the fallback regardless of which option ships.

## Decision

Adopt **Option B**, gated on the verification checklist below.
Keep Option D fully working as the documented fallback (`runbook-2fa-recovery.md`
stays valid; this work adds a primary path, does not replace the escape hatch).
Send the Option C inquiry in parallel.

## Architecture

```
Browser (Tailscale device, https-or-http)
   │
   │  WSS/WS  /ws/console
   ▼
Dashboard FastAPI (tradebot user, 100.113.140.69:8080)
   │  - Cookie auth (CR-10)               — required
   │  - Step-up password (NEW)            — required, 5-min token, per-session
   │  - Single-session lock (NEW)         — only one operator at a time
   │  - Origin check (CR-11)              — required on WS upgrade
   │  - Rate limit (CR-05)                — applied to upgrade attempts
   │  - Audit log (NEW)                   — connect/disconnect/idle/who/source
   │
   │  bytes-only proxy
   ▼
websockify (tradebot user, 127.0.0.1:6080)  — NEW systemd unit
   │
   ▼
x11vnc (TBD owner, 127.0.0.1:5900, password REQUIRED, -nosetclipboard)
   │
   ▼
Xvfb :99  → IB Gateway (root)
```

## Threat model and changes

The dashboard's auth boundary today gates "restart the bot" (limited blast
radius). After this change it gates **the entire IBKR account** — full
trading control via the gateway UI. Three concrete risk increases:

| Threat                            | Today                | After noVNC                           |
|-----------------------------------|----------------------|---------------------------------------|
| Dashboard XSS                     | Press Restart Bot    | Drive trades / change account settings|
| Stolen session cookie             | Restart-only         | Full trading control                  |
| Logged-in shared device           | Restart-only         | Full trading control                  |

Mitigations are non-negotiable for production:

1. **Step-up auth** — cookie alone is insufficient. Re-prompt for password to
   open the console; issue a 5-min console-token bound to session ID.
2. **CSP** — add `default-src 'self'; frame-ancestors 'none'`,
   `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: no-referrer`. Move inline `<style>` and `<script>` in
   `index.html` to `/static/dashboard.css` and `/static/dashboard.js` so CSP
   does not need `'unsafe-inline'`.
3. **Single-session lock** — main UI shows "Console held by session X since
   HH:MM"; second operator gets 409 until first releases or times out.
4. **Idle disconnect** at 5 min no input. VNC sessions left open are
   drive-by-trade risk on shared devices.
5. **Disable clipboard sync** in x11vnc (`-nosetclipboard`). Paste-from-browser
   is a real exploit vector.
6. **VNC password** on x11vnc — defense-in-depth even though localhost-only.
7. **Audit log** to `/var/log/tradebot-console.log` (append-only, journalctl
   mirror). Records connect/disconnect/idle-timeout/who/source IP/duration.
   Never logs the 2FA code or password (they live in the WS bytestream, never
   touch FastAPI process memory).
8. **Vendor noVNC** at a pinned tag (`v1.5.0` or current at implementation)
   under `dashboard/static/vendor/novnc/<tag>/` with checksum recorded in
   `requirements.txt` or a `THIRD_PARTY.md`. No CDN.
9. **Pin websockify** in `requirements.txt` with hash; check CVE history at
   pin time.
10. **Single ingress** — websockify binds 127.0.0.1 only; FastAPI is the only
    network-reachable surface. websockify is never directly reachable from
    Tailnet.

## Verified pre-conditions (read-only audit, this session)

| Item                                                              | Status   |
|-------------------------------------------------------------------|----------|
| `_check_token` accepts cookie OR Bearer (CR-10)                   | ✅ ready |
| `_enforce_rate_limit` + lockout state machine (CR-05)             | ✅ reusable |
| `_check_origin` CSRF check (CR-11)                                | ✅ reusable |
| HttpOnly SameSite=Strict session cookie (CR-10)                   | ✅ reusable |
| `_client_ip` with TRUSTED_PROXIES support                         | ✅ reusable |
| `_check_session` style helper exists                              | ✅ reusable |
| Dashboard binds Tailscale IP only (CR-04)                         | ✅ in unit file |
| systemd hardening pattern (CR-15)                                 | ✅ reuse for websockify unit |
| Bot Python venv at `/opt/tradebot/venv`                           | ✅ websockify pip-installable |
| ibgateway runs as `User=root`, Xvfb :99 owned by root             | ✅ confirms websockify→x11vnc boundary |

## Gaps that block merge (must fix before noVNC ships)

| Gap                                                                | Fix                                                |
|--------------------------------------------------------------------|----------------------------------------------------|
| **Dashboard has no CSP / security headers**                        | Add FastAPI middleware; refactor inline JS/CSS out |
| **`x11vnc` runs with `-nopw`** (no VNC password)                   | Add `-rfbauth /opt/tradebot/.x11vnc.pass`          |
| **CLAUDE.md drift**: claims `xvfb.service` + `x11vnc.service` exist as units; repo has neither | Verify on VPS; reconcile docs with reality first |
| **No `x11vnc.service` checked into repo**                          | If it exists on VPS, capture and commit it; else create one |
| **Step-up auth helper does not exist**                             | Build `_check_console_token` + `/api/console/login` |
| **Single-session lock does not exist**                             | New module + lock visible in `/api/system` |
| **Audit log file/sink does not exist**                             | Add structured logger writing to `/var/log/tradebot-console.log` |
| **IBKR push-2FA inquiry not sent**                                 | Send in parallel (5.16 — no blocker)               |

## Live VPS verification pass (run before opening the feature PR)

```bash
ssh chappy-vps
sudo -i
# 1. Confirm what's actually running
ps -ef | grep -E 'x11vnc|Xvfb|ibgateway|websockify' | grep -v grep
ss -tlnp | grep -E ':(4001|5900|6080|8080)'
systemctl list-units --all 'xvfb*' 'x11vnc*' 'ibgateway*' 'tradebot*'

# 2. Capture exact x11vnc invocation
ps -fC x11vnc

# 3. Confirm no public listener on VNC ports
ss -tln src '*:5900' src '*:6080' || true

# 4. Identify Xvfb display owner
ls -la /tmp/.X11-unix/

# 5. Inquire whether x11vnc.service file exists despite not being in repo
ls -la /etc/systemd/system/ | grep -E 'xvfb|x11vnc'
```

Reconcile findings into CLAUDE.md and add any missing systemd unit files to
`deploy/systemd/` in a small docs-and-config PR **before** the noVNC PR opens.

## Implementation plan (after gaps closed)

Single PR, scoped tightly. Branch `feature/dashboard-novnc-console` cut from
`develop`. Estimated 250–350 LoC + vendored noVNC bundle.

1. **Pre-noVNC hardening PR** (separate, ships first):
   - Add CSP + security-header FastAPI middleware
   - Move inline `<style>`/`<script>` from `index.html` to `/static/`
   - Add `-rfbauth` password to x11vnc deployment
   - Reconcile CLAUDE.md with VPS reality; commit any missing systemd units
   - Add the 5.16 inquiry (send + record date)
2. **noVNC PR**:
   - Vendor noVNC under `dashboard/static/vendor/novnc/<tag>/`
   - Pin websockify in `requirements.txt`
   - New systemd unit `deploy/systemd/websockify.service` (User=tradebot,
     bind 127.0.0.1:6080, full hardening per CR-15)
   - New `dashboard/console.py` module: `_check_console_token`,
     `_console_session_lock`, `/api/console/login` (step-up), `/ws/console`
     (cookie+step-up auth, origin check, rate limit, audit, proxy to
     127.0.0.1:6080)
   - New `dashboard/static/console.html` + `console.js` (noVNC canvas page)
   - Add console-state field to `/api/system` so main dashboard shows lock
   - Tests:
     - `/ws/console` rejects without cookie → 401
     - `/ws/console` rejects with cookie but no step-up token → 403
     - `/ws/console` rejects with bad origin → 403
     - `/ws/console` rate-limits per IP
     - Single-session lock returns 409 to second connector
     - Audit log written on every connect/disconnect
     - Idle-timeout disconnects after threshold
   - Mid-week rehearsal: `systemctl restart ibgateway` to surface a fresh
     login dialog without waiting for Sunday; full end-to-end test

## Schedule and risk

- Sunday 2FA rehearsal is **2026-05-10 (7 days out)**.
- Pre-noVNC hardening PR: 1 session, low risk.
- noVNC PR: 1–2 sessions plus mid-week rehearsal.
- **If verification slips:** ship pre-noVNC hardening this week, fall back to
  VNC tunnel for 2026-05-10 rehearsal, ship noVNC for the **2026-05-17**
  rehearsal. Do not ship untested step-up auth under deadline pressure.

## Consequences

- **Positive:** weekly 2FA recovery becomes a 30-second browser flow on any
  Tailnet device. Backup operator setup simplifies (no TightVNC install).
  CSP / security headers harden the dashboard against XSS regardless of this
  feature. Future gateway popups (cert prompts, errors) are no longer blocked
  on VNC tunneling.
- **Negative:** dashboard auth boundary now equals trading authority — step-up
  auth is mandatory, not optional. ~250–350 LoC of new security-critical code.
  websockify becomes a pinned third-party dependency to track CVE-wise.
- **Reversible:** all new code lives under `dashboard/console.py` +
  `dashboard/static/vendor/novnc/`; the websockify unit can be disabled to
  fully revert to VNC tunnel without affecting bot or main dashboard.

## Open questions

1. Should the step-up password be the same as `DASHBOARD_TOKEN` or a separate
   `DASHBOARD_CONSOLE_PASSWORD` env var? **Recommendation: separate.** Reduces
   blast radius if the dashboard token is leaked via journalctl or .env.
2. Should noVNC be available on a paper-only build flag, locked off in live?
   **Recommendation: no, but document that pre-go-live we revisit.** Customer-
   agreement language about access methods may apply when real money is in
   play (Phase 7 review item).
3. Idle timeout: 5 min default? **Recommendation: 5 min, configurable via env.**
