#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
CLOUDFLARED="${CLOUDFLARED_BIN:-$HOME/bin/cloudflared}"
LOCAL_URL="http://localhost:8000"
SERVER_LOG="/tmp/kora-server.log"
TUNNEL_LOG="/tmp/kora-cloudflared.log"
STATE_FILE="${KORA_TUNNEL_URL_FILE:-/tmp/kora-tunnel-url}"
ENV_FILE="$BACKEND/.env"

cd "$BACKEND"

TUNNEL_STARTED=0
if ! pgrep -f "cloudflared tunnel --url $LOCAL_URL" >/dev/null 2>&1; then
  echo "Starting Cloudflare quick tunnel..."
  nohup "$CLOUDFLARED" tunnel --url "$LOCAL_URL" --no-autoupdate >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_STARTED=1
else
  echo "Cloudflare tunnel already running."
fi

PUBLIC_URL=""
if [ "$TUNNEL_STARTED" = "1" ]; then
  for _ in $(seq 1 30); do
    PUBLIC_URL="$(grep -Eo 'https://[A-Za-z0-9.-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -n 1 || true)"
    if [ -n "$PUBLIC_URL" ]; then
      break
    fi
    sleep 1
  done
fi

if [ -z "$PUBLIC_URL" ] && [ -f "$STATE_FILE" ]; then
  PUBLIC_URL="$(cat "$STATE_FILE")"
fi
if [ -z "$PUBLIC_URL" ]; then
  for _ in $(seq 1 10); do
    PUBLIC_URL="$(grep -Eo 'https://[A-Za-z0-9.-]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -n 1 || true)"
    if [ -n "$PUBLIC_URL" ]; then
      break
    fi
    sleep 1
  done
fi
if [ -n "$PUBLIC_URL" ]; then
  printf '%s\n' "$PUBLIC_URL" >"$STATE_FILE"
fi

ENV_CHANGED=0
if [ -n "$PUBLIC_URL" ]; then
  CURRENT_FRONTEND="$(grep -E '^FRONTEND_BASE_URL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  if [ "$CURRENT_FRONTEND" != "$PUBLIC_URL" ]; then
    echo "Updating FRONTEND_BASE_URL to $PUBLIC_URL"
    if grep -q '^FRONTEND_BASE_URL=' "$ENV_FILE"; then
      sed -i '' "s|^FRONTEND_BASE_URL=.*|FRONTEND_BASE_URL=$PUBLIC_URL|" "$ENV_FILE"
    else
      printf '\nFRONTEND_BASE_URL=%s\n' "$PUBLIC_URL" >>"$ENV_FILE"
    fi
    ENV_CHANGED=1
  fi
fi

BACKEND_RUNNING=0
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  BACKEND_RUNNING=1
fi
if [ "$BACKEND_RUNNING" = "1" ] && [ "$ENV_CHANGED" = "1" ]; then
  echo "Public URL changed; restarting backend to pick up FRONTEND_BASE_URL..."
  pkill -f "uvicorn app.main:app" || true
  sleep 1
  BACKEND_RUNNING=0
fi
if [ "$BACKEND_RUNNING" = "0" ]; then
  echo "Starting KORA EATS backend on port 8000..."
  env -u DEBUG nohup venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >"$SERVER_LOG" 2>&1 &
fi

healthy=0
for _ in $(seq 1 30); do
  if curl -fsS "$LOCAL_URL/api/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
if [ "$healthy" -ne 1 ]; then
  echo "Backend did not become healthy. Check $SERVER_LOG" >&2
  exit 1
fi

echo
echo "KORA EATS is running."
echo "Local:  $LOCAL_URL"
if [ -n "$PUBLIC_URL" ]; then
  echo "Public: $PUBLIC_URL"
else
  echo "Public tunnel URL not detected yet; check $TUNNEL_LOG" >&2
fi
