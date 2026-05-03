#!/usr/bin/env bash
# deploy/setup.sh — Run once as root on a fresh Ubuntu 24.04 VPS
# Usage: bash /opt/tradebot/deploy/setup.sh
set -euo pipefail

TRADEBOT_USER=tradebot
BOT_DIR=/opt/tradebot
IBC_DIR=/opt/ibc
IBGW_DIR=/opt/ibgateway

echo "=== [1/9] System packages ==="
apt-get update -qq
apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    xvfb \
    x11vnc \
    unzip \
    wget \
    curl \
    git \
    python3.12 \
    python3.12-venv \
    python3-pip

echo "=== [2/9] Create tradebot user ==="
id -u $TRADEBOT_USER &>/dev/null \
    || useradd -r -m -d /home/$TRADEBOT_USER -s /bin/bash $TRADEBOT_USER

echo "=== [3/9] Install IB Gateway (stable, headless) ==="
mkdir -p "$IBGW_DIR"
IBGW_INSTALLER=/tmp/ibgateway-installer.sh
wget -q -O "$IBGW_INSTALLER" \
    "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh"
chmod +x "$IBGW_INSTALLER"
# -q = quiet, -dir = install location, -java = use bundled JRE
"$IBGW_INSTALLER" -q -dir "$IBGW_DIR" -java
rm "$IBGW_INSTALLER"

echo "=== [4/9] Install IBC ==="
mkdir -p "$IBC_DIR"
IBC_VERSION=$(curl -s https://api.github.com/repos/IbcAlpha/IBC/releases/latest \
    | grep '"tag_name"' | cut -d'"' -f4)
echo "IBC version: $IBC_VERSION"
wget -q -O /tmp/ibc.zip \
    "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip"
unzip -q /tmp/ibc.zip -d "$IBC_DIR"
chmod +x "$IBC_DIR"/scripts/*.sh
rm /tmp/ibc.zip

echo "=== [5/9] Copy IBC config and start script ==="
cp "$BOT_DIR/deploy/ibc/config.ini" "$IBC_DIR/config.ini"
# Lock down IBC config — contains IBKR username/password in plaintext.
# Owner is set later in step [7/9] (chown -R root:root "$IBC_DIR").
chmod 600 "$IBC_DIR/config.ini"
cp "$BOT_DIR/deploy/ibc/start_ibgateway.sh" /usr/local/bin/start_ibgateway.sh
chmod +x /usr/local/bin/start_ibgateway.sh

echo "=== [6/9] Python venv and dependencies ==="
python3.12 -m venv "$BOT_DIR/venv"
"$BOT_DIR/venv/bin/pip" install -q --upgrade pip
"$BOT_DIR/venv/bin/pip" install -q -r "$BOT_DIR/requirements.txt"

echo "=== [7/9] Directories and permissions ==="
mkdir -p "$BOT_DIR/data" "$BOT_DIR/logs"
chown -R $TRADEBOT_USER:$TRADEBOT_USER "$BOT_DIR"
# IB Gateway writes to /root/Jts by default -- leave as root
# IBC needs access to /opt/ibc
chown -R root:root "$IBC_DIR"

echo "=== [8/9] Create .env (edit this with real values) ==="
if [ ! -f "$BOT_DIR/.env" ]; then
    # Generate a random 32-char suffix for the ntfy topic so it is unguessable
    NTFY_SUFFIX=$(head -c 24 /dev/urandom | base64 | tr -dc 'a-z0-9' | head -c 24)
    cat > "$BOT_DIR/.env" <<EOF
IB_HOST=127.0.0.1
IB_PORT=4001
IB_CLIENT_ID=1
# Your IBKR account ID (e.g. DUE... for paper, U... for live) — used for log context only
IBKR_ACCOUNT_ID=CHANGEME
# ntfy.sh topic — keep this secret; generated randomly on first setup
NTFY_TOPIC=tradebot-${NTFY_SUFFIX}
# Dashboard control-plane auth token (generate with: openssl rand -hex 32)
DASHBOARD_TOKEN=CHANGEME
EOF
    chown $TRADEBOT_USER:$TRADEBOT_USER "$BOT_DIR/.env"
    chmod 600 "$BOT_DIR/.env"
    echo "Created $BOT_DIR/.env — fill in IBKR_ACCOUNT_ID and DASHBOARD_TOKEN before starting."
else
    echo "$BOT_DIR/.env already exists — skipping. Ensure NTFY_TOPIC and IBKR_ACCOUNT_ID are set."
fi

echo "=== [8.5/9] Generate x11vnc password (defense-in-depth on localhost VNC) ==="
# x11vnc is bound to 127.0.0.1 only, but a password is still required so that
# any process that gains localhost access (compromised dep, sidecar bug) cannot
# silently drive the IB Gateway. Password file is root-owned, mode 0400.
if [ ! -f /etc/x11vnc.pass ]; then
    X11VNC_PASS=$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)
    /usr/bin/x11vnc -storepasswd "$X11VNC_PASS" /etc/x11vnc.pass
    chown root:root /etc/x11vnc.pass
    chmod 0400 /etc/x11vnc.pass
    echo "Wrote /etc/x11vnc.pass (random 24-char password). Used by x11vnc.service and websockify."
    unset X11VNC_PASS
else
    echo "/etc/x11vnc.pass already exists — leaving in place."
fi

echo "=== [9/9] Install and enable systemd units ==="
cp "$BOT_DIR/deploy/systemd/"*.service /etc/systemd/system/
cp "$BOT_DIR/deploy/systemd/"*.timer  /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload
systemctl enable ibgateway.service
systemctl enable x11vnc.service
systemctl enable tradebot.service
systemctl enable tradebot-health.timer

echo ""
echo "=========================================="
echo "  Setup complete. Three steps remain:"
echo "=========================================="
echo ""
echo "  1. Fill in IBKR credentials:"
echo "     nano /opt/ibc/config.ini"
echo "     (set IbLoginId and IbPassword)"
echo ""
echo "  2. Subscribe to failure alerts on your phone:"
echo "     Read NTFY_TOPIC from /opt/tradebot/.env, then:"
echo "     Open https://ntfy.sh/<NTFY_TOPIC> or subscribe via the ntfy app"
echo ""
echo "  3. Start services:"
echo "     systemctl start ibgateway"
echo "     systemctl start tradebot"
echo "     systemctl start tradebot-health.timer"
echo ""
echo "  Monitor with:"
echo "     journalctl -fu tradebot"
echo "     journalctl -fu ibgateway"
echo ""
