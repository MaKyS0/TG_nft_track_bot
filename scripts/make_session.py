"""Generate TG_SESSION_STRING for the *operator's* service account (run locally, once).

    TG_API_ID=123 TG_API_HASH=abc python scripts/make_session.py

Telethon asks for the phone number, the login code and (if enabled) the 2FA password directly in
this terminal; nothing is stored except the resulting session string, which is printed once.
Treat that string like a password: anyone holding it controls the account. Use a dedicated
account, put the string only into your server's .env, and never commit it.
"""

import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    try:
        api_id = int(os.environ["TG_API_ID"])
        api_hash = os.environ["TG_API_HASH"]
    except (KeyError, ValueError):
        sys.exit("Set TG_API_ID and TG_API_HASH (from https://my.telegram.org) in the environment first.")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        print("\nTG_SESSION_STRING=" + client.session.save())  # type: ignore[union-attr]


if __name__ == "__main__":
    asyncio.run(main())
