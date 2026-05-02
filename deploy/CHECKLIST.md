# VPS Deployment Checklist

Follow these steps top to bottom. Each step is one command or one clear action.
VPS IP: 2.24.222.199 | User: root (then tradebot) | OS: Ubuntu 24.04 LTS

---

## Part 1 — First-time server setup (do once)

### Step 1 — SSH into the VPS
On your Windows PC in PowerShell:
```
ssh root@2.24.222.199
```

### Step 2 — Clone the bot repo
```
git clone https://github.com/gzion2719/Trad_Bot_wClaude.git /opt/tradebot
```

### Step 3 — Run the setup script
```
bash /opt/tradebot/deploy/setup.sh
```
This takes 3–5 minutes. It installs Java, Xvfb, IB Gateway, IBC, Python venv,
and all systemd units. Watch for any red errors.

### Step 4 — Fill in your IBKR credentials
```
nano /opt/ibc/config.ini
```
Change these two lines (your IBKR website username and password):
```
IbLoginId=YOUR_IBKR_USERNAME
IbPassword=YOUR_IBKR_PASSWORD
```
Save: Ctrl+X → Y → Enter

### Step 5 — Subscribe to failure alerts (free, takes 30 seconds)
The ntfy topic was generated randomly during `setup.sh` and stored in `/opt/tradebot/.env`.
Read it with:
```
grep NTFY_TOPIC /opt/tradebot/.env
```
On your phone, install the **ntfy** app (iOS or Android), then subscribe to the topic shown above.
Or visit `https://ntfy.sh/<NTFY_TOPIC>` in a browser to see alerts there.
You will only receive a notification when something fails — not on normal trades.

### Step 6 — Start IB Gateway
```
systemctl start ibgateway
```
Wait 30 seconds, then check it started:
```
journalctl -fu ibgateway
```
You should see IBC logging IB Gateway startup. Press Ctrl+C to stop watching.

### Step 7 — Start the bot
```
systemctl start tradebot
```
Check it connected:
```
journalctl -fu tradebot
```
You should see "Connected to IB Gateway" and "SMACrossover started".

### Step 8 — Start the health check timer
```
systemctl start tradebot-health.timer
```

### Step 9 — Verify everything is running
```
systemctl status ibgateway tradebot tradebot-health.timer
```
All three should show **active (running)** or **active (waiting)**.

---

## Part 2 — Deploying updates (any time you push new code)

On the VPS:
```
cd /opt/tradebot
git pull
/opt/tradebot/venv/bin/pip install -q -r requirements.txt
systemctl restart tradebot
```

---

## Part 3 — Daily monitoring

Check today's trades:
```
cd /opt/tradebot
/opt/tradebot/venv/bin/python -c "
from data.trade_log import TradeLog
tl = TradeLog('data/paper_trades.db')
import json; print(json.dumps(tl.daily_summary(), indent=2))
"
```

Check live logs:
```
journalctl -fu tradebot --since today
```

---

## Part 4 — Useful commands

| Task | Command |
|------|---------|
| Stop bot cleanly | `systemctl stop tradebot` |
| Stop IB Gateway | `systemctl stop ibgateway` |
| Restart after crash | `systemctl restart tradebot` |
| See last 100 log lines | `journalctl -u tradebot -n 100 --no-pager` |
| See IB Gateway logs | `journalctl -u ibgateway -n 100 --no-pager` |
| Check disk usage | `df -h /opt/tradebot` |
| Test ntfy alert | `source /opt/tradebot/.env && curl -d "test" https://ntfy.sh/${NTFY_TOPIC}` |

---

## What happens automatically (no action needed)

- **16:10 ET daily**: bot calls `on_tick()`, checks signals, places orders if any
- **11:59 PM ET nightly**: IBC restarts IB Gateway and logs back in automatically
- **During IBKR restart**: ReconnectManager pauses the bot, then resumes (~1 min)
- **Bot crash**: systemd restarts it after 30 seconds, sends you an ntfy alert
- **IB Gateway crash**: systemd restarts it after 60 seconds, sends you an ntfy alert
- **Every 2 hours**: health timer checks that on_tick() ran within the last 26 hours
