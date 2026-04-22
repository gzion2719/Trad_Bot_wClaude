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

# Detect IB Gateway version from the .desktop file (e.g. "IB Gateway 10.37.desktop" → "1037")
IBGW_VERSION=$(ls "$IBGW_DIR/"*.desktop 2>/dev/null \
    | grep -oP '\d+\.\d+' | head -1 | tr -d '.')
# Fallback: let IBC auto-detect
if [ -z "$IBGW_VERSION" ]; then
    IBGW_VERSION=latest
fi
echo "Starting IB Gateway version $IBGW_VERSION via IBC..."

# IBC constructs gateway path as: <tws-path>/ibgateway/<version>/
# The IBKR installer uses a flat layout (no ibgateway/version subdir).
# Create the expected structure via a symlink:
#   /opt/ibgw/ibgateway/1037  ->  /opt/ibgateway
IBGW_PARENT=/opt/ibgw
mkdir -p "${IBGW_PARENT}/ibgateway"
if [ ! -e "${IBGW_PARENT}/ibgateway/${IBGW_VERSION}" ]; then
    ln -s "$IBGW_DIR" "${IBGW_PARENT}/ibgateway/${IBGW_VERSION}"
fi

# Run IBC — this blocks until IB Gateway exits
# --gateway tells IBC this is IB Gateway (not TWS)
# Trading mode is set in config.ini (TradingMode=paper), not here
exec "${IBC_DIR}/scripts/ibcstart.sh" "$IBGW_VERSION" \
    "--tws-path=${IBGW_PARENT}" \
    "--ibc-path=${IBC_DIR}" \
    "--ibc-ini=${IBC_DIR}/config.ini" \
    "--gateway"
