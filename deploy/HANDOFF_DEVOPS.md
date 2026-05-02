# DevOps Handoff — TradeBot VPS Deployment
# Continue from this point in a new Claude session

---

## Context

This is a Python algorithmic trading bot that connects to Interactive Brokers
(IBKR) via IB Gateway. We are deploying it to a Hostinger VPS for 24/7
unattended operation. The bot itself is complete and backtested — we are purely
doing infrastructure work in this handoff.

GitHub: https://github.com/gzion2719/Trad_Bot_wClaude

---

## VPS Details

| Setting | Value |
|---|---|
| Provider | Hostinger KVM 1 |
| OS | Ubuntu 24.04 LTS |
| IP | 2.24.222.199 |
| SSH | `ssh root@2.24.222.199` |
| Bot directory | `/opt/tradebot` |
| IB Gateway install | `/opt/ibgateway` |
| IBC install | `/opt/ibc` |
| Symlink for IBC | `/opt/ibgw/ibgateway/1037` → `/opt/ibgateway` |
| IBC config | `/opt/ibc/config.ini` |
| Start script | `/usr/local/bin/start_ibgateway.sh` |

---

## What Is Already Done (Do Not Redo)

- [x] Ubuntu 24.04 provisioned, SSH key set up
- [x] Bot repo cloned to `/opt/tradebot`
- [x] Python venv at `/opt/tradebot/venv` with all dependencies installed
- [x] IB Gateway 10.37 installed at `/opt/ibgateway`
- [x] IBC 3.23.0 installed at `/opt/ibc`
- [x] All required system libraries installed (GTK, X11, ALSA, etc.)
- [x] Xvfb working (virtual display :99)
- [x] IBC path/symlink issues resolved — IBC finds jars and vmoptions correctly
- [x] IBC successfully reads `config.ini`, starts IB Gateway, detects login dialog,
      types username and password automatically
- [x] All systemd unit files created in `/opt/tradebot/deploy/systemd/`
  - `ibgateway.service`
  - `tradebot.service`
  - `tradebot-notify@.service`
  - `tradebot-health.service`
  - `tradebot-health.timer`
- [x] `.env` file at `/opt/tradebot/.env` with `IB_PORT=4001`

---

## Current Status — BLOCKED ON LOGIN

IBC starts IB Gateway, types credentials, but the login does not complete.

**Root cause:** IBKR is presenting a 2FA challenge (the login is from an
unknown IP/device). IBC cannot answer interactive 2FA challenges automatically.

**What we know:**
- The IBKR credentials in `/opt/ibc/config.ini` are for the **paper trading
  account** (username: `ibkpaperacc`, account stored in `IBKR_ACCOUNT_ID` in `.env`)
- Paper accounts are a sub-account of the main IBKR account
- TWS was open on the user's PC during earlier attempts — that may have caused
  session conflicts. TWS is now closed.
- IBKR's "IP Restrictions" page only does full IP lockout (blocks all other IPs)
  — NOT what we want. Do not add an IP restriction.

**The IBC log stops here each time:**
```
IBC: Setting user name
IBC: Setting password
IBC: detected frame entitled: IBKR Gateway; event=Activated
IBC: detected frame entitled: IBKR Gateway; event=Focused
[... hangs waiting for login to complete ...]
```

---

## What Needs To Be Done Next

### Priority 1 — Get IB Gateway to log in

Options to try (in order of preference):

**Option A — Try login with TWS fully closed (simplest)**
With TWS closed on the user's PC, retry:
```bash
pkill -f ibcalpha 2>/dev/null; pkill Xvfb 2>/dev/null; rm -f /tmp/.X99-lock
bash /usr/local/bin/start_ibgateway.sh
```
Watch for `Login complete` or `main window` in the logs.
Paper accounts sometimes don't require 2FA if there's no session conflict.

**Option B — Use VNC to see the IB Gateway GUI**
IB Gateway is running in Xvfb (virtual display :99). Use x11vnc to expose the
display so we can SEE what dialog is showing and interact with it manually:
```bash
apt-get install -y x11vnc
x11vnc -display :99 -nopw -listen localhost -xkb &
ssh -L 5900:localhost:5900 root@2.24.222.199
```
Then connect a VNC viewer on the PC to `localhost:5900` to see the login screen.

**Option C — Configure IBC to handle 2FA challenge codes**
IBC 3.x supports some 2FA methods. Check the IBC config options:
- `TwoFactorLoginDialogueDetectedAction` 
- Look in `/opt/ibc/config.ini` — IBC may support entering a challenge response

**Option D — Disable 2FA for the paper account**
Log into ibkr.com with the main account → Settings → Secure Login System →
check if paper sub-account can have 2FA disabled independently.

### Priority 2 — Once logged in, wire up systemd

After IB Gateway logs in successfully manually, do:
```bash
systemctl daemon-reload
systemctl start ibgateway
systemctl start tradebot
systemctl start tradebot-health.timer
systemctl status ibgateway tradebot tradebot-health.timer
```

### Priority 3 — Persist the symlink fix in setup.sh

The symlink `/opt/ibgw/ibgateway/1037 → /opt/ibgateway` was created manually
and is NOT in `deploy/setup.sh`. Add it so future deploys work automatically.
In `deploy/setup.sh`, after the IBC install section, add:
```bash
mkdir -p /opt/ibgw/ibgateway
ln -sf /opt/ibgateway /opt/ibgw/ibgateway/$(ls /opt/ibgateway/*.desktop 2>/dev/null | grep -oP '\d+\.\d+' | head -1 | tr -d '.')
```

### Priority 4 — Push VPS-side fixes back to git

The start script was patched directly on the VPS with `sed`. The local file
`deploy/ibc/start_ibgateway.sh` on the dev PC has the correct version.
After login works, do `git pull` on VPS to sync.

---

## Key Files

| File | Purpose |
|---|---|
| `/opt/ibc/config.ini` | IBC credentials and settings — **edit here for credentials** |
| `/usr/local/bin/start_ibgateway.sh` | Script that starts Xvfb + IBC + IB Gateway |
| `/opt/tradebot/.env` | Bot config: `IB_HOST=127.0.0.1`, `IB_PORT=4001`, `IB_CLIENT_ID=1` |
| `/opt/tradebot/deploy/systemd/` | All systemd unit files |
| `/root/Jts/jts.ini` | IB Gateway settings written by IBC (auto-generated) |

---

## Useful Commands

```bash
# Start IB Gateway manually (for debugging)
pkill -f ibcalpha 2>/dev/null; pkill Xvfb 2>/dev/null; rm -f /tmp/.X99-lock
bash /usr/local/bin/start_ibgateway.sh

# Check if IB Gateway Java process is running
ps aux | grep -i ibcalpha | grep -v grep

# See IB Gateway GUI via VNC (install x11vnc first)
x11vnc -display :99 -nopw -listen localhost -xkb

# Check systemd service status
systemctl status ibgateway tradebot

# Watch live logs
journalctl -fu ibgateway
journalctl -fu tradebot

# Test bot connection to IB Gateway (once IB Gateway is running)
cd /opt/tradebot
venv/bin/python -c "
from broker.ibkr_client import IBKRClient
c = IBKRClient('127.0.0.1', 4001, 1)
c.connect()
print('Connected:', c.is_alive())
"
```

---

## IBKR Account

| Setting | Value |
|---|---|
| Paper account | See `IBKR_ACCOUNT_ID` in `/opt/tradebot/.env` |
| IBC username | ibkpaperacc (paper account login) |
| IB Gateway port | 4001 (paper) |

---

## Notification System (already configured)

Failure alerts go to the ntfy.sh topic stored in `NTFY_TOPIC` in `/opt/tradebot/.env`.
Read it with: `grep NTFY_TOPIC /opt/tradebot/.env`
User should subscribe via the ntfy app or visit `https://ntfy.sh/<NTFY_TOPIC>`.
Test: `source /opt/tradebot/.env && curl -d "test" https://ntfy.sh/${NTFY_TOPIC}`

---

## Do NOT Do

- Do NOT add an IP Restriction in IBKR — it locks out all other IPs including
  the user's PC
- Do NOT modify the Python bot code (except health.txt line already added)
- Do NOT use Docker
- Do NOT expose IB Gateway port externally (must stay on localhost only)
- Do NOT store IBKR credentials in `.env` — they live in `/opt/ibc/config.ini`
