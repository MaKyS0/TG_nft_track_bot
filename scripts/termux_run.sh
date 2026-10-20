#!/usr/bin/env bash
# Start the bot + Mini App on Termux: opens a free Cloudflare quick tunnel (public HTTPS address),
# passes it to the bot as WEBAPP_URL and runs the bot. The address changes on every start; the bot
# re-points its menu button automatically.
#
# If the tunnel cannot be opened (free quick tunnels are sometimes rate limited), the bot still starts
# WITHOUT the Mini App: notifications and the chat interface do not depend on it.
# Set REQUIRE_TUNNEL=1 to abort instead.
#
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
TUNNEL_WAIT="${TUNNEL_WAIT:-60}"
TUNNEL_PID=""
URL=""

stop_tunnel() {
    if [ -n "$TUNNEL_PID" ]; then
        kill "$TUNNEL_PID" 2>/dev/null || true
        TUNNEL_PID=""
    fi
}
cleanup() {
    stop_tunnel
    command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true
}
trap cleanup EXIT

# Success = an address was issued AND the tunnel actually connected (the address alone proves nothing).
start_tunnel() {
    : >"$LOG"
    cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8080 >"$LOG" 2>&1 &
    TUNNEL_PID=$!
    local i
    for ((i = 0; i < TUNNEL_WAIT; i++)); do
        URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
        if [ -n "$URL" ] && grep -q "Registered tunnel connection" "$LOG"; then
            return 0
        fi
        kill -0 "$TUNNEL_PID" 2>/dev/null || break   # cloudflared exited on its own
        sleep 1
    done
    URL=""
    return 1
}

pkill -x cloudflared 2>/dev/null || true   # a forgotten tunnel from an earlier run would only get in the way
sleep 1

if start_tunnel; then
    :
else
    echo "Туннель не поднялся с первой попытки, пробую ещё раз..."
    stop_tunnel
    sleep 5
    start_tunnel || true
fi

if [ -n "$URL" ]; then
    echo "Мини-приложение: $URL"
    export WEBAPP_URL="$URL"
else
    echo "Не удалось открыть туннель, мини-приложение сейчас недоступно. Последние строки журнала:" >&2
    tail -n 6 "$LOG" 2>/dev/null | cut -c1-160 >&2 || true
    echo "Совет: бесплатный туннель иногда отказывает; подождите несколько минут и запустите снова." >&2
    if [ "${REQUIRE_TUNNEL:-0}" = "1" ]; then
        exit 1
    fi
    stop_tunnel
    unset WEBAPP_URL
    echo "Запускаю бота без мини-приложения (уведомления и чат работают)." >&2
fi
python -m app
