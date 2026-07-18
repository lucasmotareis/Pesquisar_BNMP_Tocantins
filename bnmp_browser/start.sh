#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1366}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-768}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
CDP_PORT="${CDP_PORT:-9222}"
BNMP_PORTAL_URL="${BNMP_PORTAL_URL:-https://portalbnmp.pdpj.jus.br/#/pesquisa-peca}"
BNMP_VNC_PASSWORD="${BNMP_VNC_PASSWORD:-}"

mkdir -p /app/data /browser-profile
rm -f "/tmp/.X${DISPLAY#:}-lock"

if [[ -z "$BNMP_VNC_PASSWORD" ]]; then
  echo "BNMP_VNC_PASSWORD nao configurado. Defina uma senha para o noVNC." >&2
  exit 1
fi

Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24" -nolisten tcp &
XVFB_PID=$!

sleep 1

fluxbox >/tmp/fluxbox.log 2>&1 &
FLUXBOX_PID=$!

x11vnc -storepasswd "$BNMP_VNC_PASSWORD" /tmp/x11vnc.pass >/tmp/x11vnc-pass.log 2>&1
chmod 600 /tmp/x11vnc.pass

x11vnc -display "$DISPLAY" -forever -shared -rfbport 5900 -rfbauth /tmp/x11vnc.pass -noxdamage -quiet >/tmp/x11vnc.log 2>&1 &
X11VNC_PID=$!

websockify --web=/usr/share/novnc/ "0.0.0.0:${NOVNC_PORT}" localhost:5900 >/tmp/novnc.log 2>&1 &
NOVNC_PID=$!

chromium \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --disable-software-rasterizer \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="${CDP_PORT}" \
  --remote-allow-origins=* \
  --user-data-dir=/browser-profile \
  --window-size="${SCREEN_WIDTH},${SCREEN_HEIGHT}" \
  "${BNMP_PORTAL_URL}" >/tmp/chromium.log 2>&1 &
CHROMIUM_PID=$!

cleanup() {
  kill "$CHROMIUM_PID" "$NOVNC_PID" "$X11VNC_PID" "$FLUXBOX_PID" "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python /app/session_exporter.py
