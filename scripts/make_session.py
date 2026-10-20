"""Log the operator's *service* Telegram account in and store TG_SESSION_STRING (run locally, once).

    python scripts/make_session.py            # reads TG_API_ID / TG_API_HASH from .env or the environment

Telegram asks for the phone number, the login code and (if enabled) the 2FA password directly in
this terminal. The resulting session string is written straight into ``.env`` (chmod 600) and is
NEVER printed: it gives full control of the account, so it must not end up in scrollback,
screenshots or chat. Use a dedicated account that owns nothing valuable, and enable 2FA on it.
"""

import asyncio
import os
import re
import stat
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

ENV_PATH = Path(".env")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                values[key.strip()] = value.strip()
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    """Set KEY=value in the env file (replacing an existing line), keeping permissions private."""
    lines = path.read_text().splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    lines = [line for line in lines if not pattern.match(line)] + [f"{key}={value}"]
    previous_umask = os.umask(0o077)
    try:
        path.write_text("\n".join(lines) + "\n")
    finally:
        os.umask(previous_umask)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


async def main() -> None:
    env = {**read_env(ENV_PATH), **os.environ}
    try:
        api_id = int(env["TG_API_ID"])
        api_hash = env["TG_API_HASH"]
    except (KeyError, ValueError):
        sys.exit("Set TG_API_ID and TG_API_HASH (from https://my.telegram.org) in .env or the environment first.")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        session = client.session.save()  # type: ignore[union-attr]
    write_env_value(ENV_PATH, "TG_SESSION_STRING", session)
    print(f"Done: TG_SESSION_STRING saved to {ENV_PATH} (not shown on screen).")


if __name__ == "__main__":
    asyncio.run(main())
