#!/usr/bin/env bash
# Start the bot + Mini App on Termux: opens a free Cloudflare quick tunnel (public HTTPS address),
# passes it to the bot as WEBAPP_URL and runs the bot. The address changes on every start; the bot
# re-points its menu button automatically.
# Usage (from the project folder):  bash scripts/termux_run.sh      (stop: Ctrl+C)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ] || [ ! -d .venv ]; then
    echo "Сначала выполните: bash scripts/termux_setup.sh" >&2
    exit 1
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# Keep the CPU awake so Android does not freeze the bot when the screen is off.
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

umask 077   # everything we create (database, logs) stays private to this app
mkdir -p data
chmod 700 data
LOG=data/tunnel.log
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8080 >"$LOG" 2>&1 &
TUNNEL_PID=$!
cleanup() {
    kill "$TUNNEL_PID" 2>/dev/null || true
    command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true
}
trap cleanup EXIT

URL=""
for _ in $(seq 1 60); do
    URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
    [ -n "$URL" ] && break
    sleep 1
done
if [ -z "$URL" ]; then
    echo "Не удалось получить адрес туннеля. Лог: $LOG" >&2
    exit 1
fi

echo "Мини-приложение: $URL"
export WEBAPP_URL="$URL"
python -m app
