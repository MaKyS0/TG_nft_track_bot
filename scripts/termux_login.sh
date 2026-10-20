#!/usr/bin/env bash
# Switch the bot from demo data to REAL markets (Termux). Asks for your Telegram API credentials,
# logs the service account in and stores everything in .env. Secrets are typed hidden and never printed.
# Usage (from the project folder):  bash scripts/termux_login.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ] || [ ! -d .venv ]; then
    echo "Сначала выполните: bash scripts/termux_setup.sh" >&2
    exit 1
fi
# shellcheck disable=SC1091
. .venv/bin/activate
umask 077

set_env() { # set_env KEY VALUE : replace or append a line in .env, keeping it private
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    grep -v "^${key}=" .env >"$tmp" || true
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
    mv "$tmp" .env
    chmod 600 .env
}

cat <<'TEXT'
Подключение реальных рынков.

ВАЖНО: используйте ОТДЕЛЬНЫЙ аккаунт Telegram (например, на запасной SIM) без ценных подарков и
денег, и включите на нём двухфакторную защиту. Строка сессии даёт полный доступ к аккаунту.

Нужны api_id и api_hash: https://my.telegram.org -> API development tools.
TEXT

read -r -p "api_id (число): " API_ID
if ! [[ "$API_ID" =~ ^[0-9]+$ ]]; then
    echo "api_id должен быть числом." >&2
    exit 1
fi
read -r -s -p "api_hash (ввод скрыт): " API_HASH
echo
API_HASH="$(printf '%s' "$API_HASH" | tr -d '[:space:]')"
if ! [[ "$API_HASH" =~ ^[0-9a-fA-F]{32}$ ]]; then
    echo "api_hash — 32 символа (цифры и буквы a-f)." >&2
    exit 1
fi

echo
echo "Какие рынки включить?"
echo "  1) только официальный рынок Telegram (рекомендуется)"
echo "  2) Telegram + MRKT (экспериментально: MRKT сейчас отвечает 429 на запросы лотов, см. README)"
read -r -p "Выбор [1]: " CHOICE
case "${CHOICE:-1}" in
    2) SOURCES="telegram,mrkt" ;;
    *) SOURCES="telegram" ;;
esac

set_env TG_API_ID "$API_ID"
set_env TG_API_HASH "$API_HASH"

echo
echo "Сейчас Telegram пришлёт код входа для служебного аккаунта (телефон, код, пароль 2FA вводятся здесь)."
python scripts/make_session.py

set_env GIFT_SOURCE "$SOURCES"
echo
echo "Готово: включено «$SOURCES». Запуск: bash scripts/termux_run.sh"
echo "Совет: очистите историю буфера обмена клавиатуры (там мог остаться api_hash)."
