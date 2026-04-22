#!/usr/bin/env bash
# Starts a virtual display (Xvfb), then launches IB Gateway via IBC.
# Systemd keeps this process alive; it exits only when IB Gateway exits.
set -euo pipefail

DISPLAY_NUM=99
export DISPLAY=":${DISPLAY_NUM}"
IBGW_DIR=/opt/ibgateway
IBC_DIR=/opt/ibc

# Start virtual framebuffer (IB Gateway is a Java Swing app, needs a display)
Xvfb ":${DISPLAY_NUM}" -screen 0 1024x768x24 -nolisten tcp &
XVFB_PID=$!
trap 'kill $XVFB_PID 2>/dev/null; exit' EXIT TERM INT
sleep 2

# Detect installed IB Gateway version (directory name is the version number)
IBGW_VERSION=$(ls "$IBGW_DIR/" | grep -E '^[0-9]+$' | sort -rn | head -1)
if [ -z "$IBGW_VERSION" ]; then
    echo "ERROR: No IB Gateway version found in $IBGW_DIR/" >&2
    exit 1
fi
echo "Starting IB Gateway $IBGW_VERSION via IBC..."

# Run IBC — this blocks until IB Gateway exits
exec "${IBC_DIR}/scripts/ibcstart.sh" "$IBGW_VERSION" \
    "--tws-path=${IBGW_DIR}" \
    "--ibc-path=${IBC_DIR}" \
    "--ibc-ini=${IBC_DIR}/config.ini" \
    "--trading-mode=paper" \
    "--log-components=never"
