"""The operator's service account (MTProto), shared by every source that needs Telegram.

It is used for two things only: reading Telegram's own gift marketplace, and obtaining the signed
``initData`` that third-party mini apps (MRKT) require for login. Nothing is ever sent from this
account. The session string is the single most sensitive secret of the project: whoever holds it
controls the account, so use a dedicated account and keep the string out of logs and git.
"""

import asyncio
import logging
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl import functions, types

from app.domain import SourceError, SourceRateLimited
from app.ratelimit import AsyncRateLimiter

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


def init_data_from_url(url: str) -> str:
    """Extract ``tgWebAppData`` (the signed initData string) from a web-view URL fragment."""
    values = parse_qs(urlparse(url).fragment, keep_blank_values=True).get("tgWebAppData")
    if not values or not values[0]:
        raise SourceError("Telegram returned a web view without initData")
    return values[0]


class TelegramAccount:
    def __init__(self, api_id: int, api_hash: str, session_string: str, request_delay: float = 1.5) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_string = session_string
        self._limiter = AsyncRateLimiter(1.0 / max(request_delay, 0.05))
        self._client: TelegramClient | None = None

    async def start(self) -> None:
        # flood_sleep_threshold=0: never sleep silently inside Telethon, surface FloodWait to the monitor.
        client = TelegramClient(
            StringSession(self._session_string), self._api_id, self._api_hash, flood_sleep_threshold=0
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise SourceError("TG_SESSION_STRING is not authorized; regenerate it with scripts/make_session.py")
        except (OSError, errors.RPCError) as e:
            await client.disconnect()
            raise SourceError(f"cannot connect to Telegram: {e}") from e
        except SourceError:
            await client.disconnect()
            raise
        self._client = client
        log.info("Telegram account connected")

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def _guard(self, awaitable, what: str):
        try:
            return await asyncio.wait_for(awaitable, REQUEST_TIMEOUT)
        except errors.FloodWaitError as e:
            raise SourceRateLimited(float(e.seconds)) from e
        except (errors.RPCError, OSError, asyncio.TimeoutError) as e:
            raise SourceError(f"{what} failed: {e!r}") from e

    async def call(self, request):
        if self._client is None:
            raise SourceError("Telegram account is not started")
        await self._limiter.acquire()
        return await self._guard(self._client(request), type(request).__name__)

    async def input_peer(self, username: str):
        """Resolve a public ``@username`` (read-only lookup)."""
        if self._client is None:
            raise SourceError("Telegram account is not started")
        await self._limiter.acquire()
        return await self._guard(self._client.get_input_entity(username), "resolve " + username)

    async def web_app_init_data(self, bot_username: str, short_name: str = "app") -> str:
        """Signed initData for the mini app ``t.me/<bot_username>/<short_name>`` (what its login expects)."""
        if self._client is None:
            raise SourceError("Telegram account is not started")
        await self._limiter.acquire()
        peer = await self._guard(self._client.get_input_entity(bot_username), "resolve " + bot_username)
        bot = types.InputUser(user_id=peer.user_id, access_hash=peer.access_hash)
        app = types.InputBotAppShortName(bot_id=bot, short_name=short_name)
        result = await self.call(functions.messages.RequestAppWebViewRequest(peer=peer, app=app, platform="android"))
        return init_data_from_url(result.url)
