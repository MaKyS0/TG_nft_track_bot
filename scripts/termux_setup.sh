#!/usr/bin/env bash
# One-time setup on Android/Termux: system packages, Python environment, .env with your bot token.
# Usage (from the project folder):  bash scripts/termux_setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Системные пакеты (python, компилятор, rust для pydantic-core, cloudflared)"
pkg update -y
pkg install -y python git clang make rust binutils libffi openssl cloudflared tmux

# pydantic-core is built from source on Android and needs to know the API level.
ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || echo 24)"
export ANDROID_API_LEVEL

echo "==> Окружение Python (первый раз сборка pydantic-core может занять 10-20 минут)"
python -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -U pip wheel setuptools
# Pure-Python fallbacks avoid compiling C extensions that often fail on Android.
AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1 FROZENLIST_NO_EXTENSIONS=1 \
    pip install -r requirements-termux.txt

if [ ! -f .env ]; then
    echo "==> Настройка"
    read -r -s -p "Вставьте токен бота от @BotFather (ввод скрыт): " TOKEN
    echo
    if [ -z "$TOKEN" ]; then
        echo "Токен пустой, запустите скрипт снова." >&2
        exit 1
    fi
    mkdir -p data
    umask 077
    {
        echo "BOT_TOKEN=$TOKEN"
        echo "DATABASE_URL=sqlite+aiosqlite:///./data/gifts.db"
        echo "GIFT_SOURCE=mock"
        echo "POLL_INTERVAL_SECONDS=60"
        echo "WEB_HOST=127.0.0.1"
    } > .env
    echo "Файл .env создан (права 600)."
fi

echo
echo "Готово. Запуск: bash scripts/termux_run.sh"
