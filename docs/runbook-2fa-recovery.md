# Runbook: Weekly 2FA Recovery

**Who this is for:** Any authorised team member who needs to re-authenticate IB Gateway on Sunday morning when the owner is unavailable.

**Time required:** 5–10 minutes, including setup.

**When this is needed:** Every Sunday ~01:00 ET (08:00 IL), IBKR invalidates all cached tokens. The IB Gateway login dialog appears and waits for a fresh 2FA code. Until it is entered, the trading bot is paused.

---

## Prerequisites

You need all three of these before Sunday morning — set them up in advance:

### 1. SSH access
- Install an SSH client (Windows: PowerShell built-in, or PuTTY)
- Get the private key file `chappy_v3` from the owner and save it to `~/.ssh/chappy_v3`
- Add this to your `~/.ssh/config`:
  ```
  Host chappy-vps
      HostName 100.113.140.69
      User chappy
      IdentityFile ~/.ssh/chappy_v3
  ```
- **Important:** the VPS public IP `2.24.222.199` has port 22 blocked by firewall. You must connect via Tailscale IP `100.113.140.69`.

### 2. Tailscale
- Install Tailscale on your PC: https://tailscale.com/download
- Log in with the same account the owner added you to (ask owner for the invite link)
- Confirm it works: `ping 100.113.140.69` should get replies

### 3. TightVNC Viewer
- Download and install TightVNC Viewer (Windows): https://www.tightvnc.com/download.php
- You only need the Viewer — not the server component

---

## Step-by-step recovery (Sunday morning)

### Step 1 — Open two terminal windows

**Terminal A** (SSH session for commands):
```bash
ssh chappy-vps
```
When prompted for the sudo password, ask the owner.

**Terminal B** (VNC tunnel — keep this open the whole time):
```bash
ssh -L 5900:localhost:5900 chappy-vps
```
This tunnel stays open silently. Leave it running.

### Step 2 — Check gateway status

In **Terminal A**:
```bash
sudo systemctl status ibgateway
```

You will see one of these:
- **"active (running)"** — gateway is up but stuck at the 2FA prompt. Continue to Step 3.
- **"activating"** or **"auto-restart"** — gateway is still starting up. Wait 30 seconds and re-run. If it stays in this state for more than 2 minutes, skip to [Troubleshooting](#troubleshooting).
- **"inactive (dead)"** — gateway stopped. Run `sudo systemctl start ibgateway`, wait 30 seconds, then continue.

### Step 3 — Open TightVNC

1. Open TightVNC Viewer on your PC
2. In the "Remote Host" field enter: `localhost:5900`
3. Click Connect
4. If prompted for a VNC password: leave blank and press OK (VNC is localhost-only, no password set)

You should see the IB Gateway login window on the virtual desktop.

### Step 4 — Generate a fresh 2FA code

On your phone:
1. Open the **IBKR Mobile** app
2. Tap **More** → **Security** → **Generate Code** (or go to the Interactive IL Key section)
3. A 6-digit code appears. It is valid for ~30 seconds — have the next step ready.

### Step 5 — Enter the code in IB Gateway

In the VNC window:
1. You should see the IB Gateway login dialog with a "Security Code" or "2FA Code" field
2. Type the 6-digit code
3. Click **OK** or press Enter

The gateway will log in. The VNC screen will show the normal IB Gateway dashboard (not a login dialog) within 10–15 seconds.

### Step 6 — Verify the bot reconnected

In **Terminal A**:
```bash
sudo journalctl -fu tradebot
```

Within 60 seconds you should see a line like:
```
Connected | account=<account-id> | port=4001
```
or
```
SMACrossover started
```

Also confirm the port is listening:
```bash
ss -tlnp | grep 4001
```
Expected output: a line showing `LISTEN` on port 4001.

Press `Ctrl+C` to stop watching logs.

### Step 7 — Done

Close the VNC window. You can close Terminal B (the tunnel). Leave Terminal A open if you want to keep watching.

Notify the owner that the recovery completed and note the time.

---

## What success looks like

| Check | Command | Expected output |
|-------|---------|-----------------|
| Gateway running | `sudo systemctl is-active ibgateway` | `active` |
| Port 4001 open | `ss -tlnp \| grep 4001` | Line with `LISTEN` |
| Bot connected | `sudo journalctl -u tradebot -n 20 --no-pager` | `Connected \| account=` |
| Dashboard healthy | Open `http://100.113.140.69:8080` in browser | Liveness shows `ok`, Gateway shows green |

---

## Troubleshooting

### VNC connects but screen is black
The virtual display may have restarted. Run in Terminal A:
```bash
sudo systemctl restart xvfb x11vnc
```
Wait 10 seconds, then reconnect TightVNC.

### IB Gateway login dialog asks for username/password (not just 2FA)
IBC has lost the saved credentials. This is rare — contact the owner. Do not enter credentials yourself unless explicitly authorised.

### Bot still shows "disconnected" after 2 minutes
```bash
sudo systemctl restart tradebot
sudo journalctl -fu tradebot
```
Watch for `Connected` within 30 seconds. If it keeps failing, check the gateway is actually running: `sudo systemctl status ibgateway`.

### Cannot connect via SSH (Tailscale not reachable)
1. Confirm Tailscale is running on your PC: check the Tailscale tray icon
2. If your PC shows connected but ping times out, ask the owner to check Tailscale on the VPS
3. Last resort: log in to https://hpanel.hostinger.com → KVM console (browser-based terminal, no SSH needed)

### Gateway shows "active (running)" but port 4001 is not listening
Gateway is up but not yet accepting connections — it may still be completing its startup sequence. Wait 30–60 seconds and re-check. If it persists after 2 minutes:
```bash
sudo systemctl restart ibgateway
```
Then wait and repeat Step 6.

---

## Background: why this is needed every Sunday

IBKR's security model:
- **Mon–Sat at 23:59 UTC**: IBC automatically restarts IB Gateway using the cached token. No 2FA needed — fully automated.
- **Sunday ~01:00 ET**: IBKR servers invalidate all cached tokens. The next restart requires a fresh code.

This is a regulatory floor — IBKR has removed all opt-out paths for trading accounts. There is no API key or IP-whitelist bypass.

The bot is otherwise fully self-healing (systemd supervises all processes, ReconnectManager handles TWS restarts). The one weekly human step is entering this code.

---

## Emergency contacts

- **Owner:** <add contact info>
- **Hostinger KVM console:** https://hpanel.hostinger.com (browser terminal — last resort if SSH fails)
- **ntfy alerts:** subscribe to the topic in `/opt/tradebot/.env` for automatic failure notifications
