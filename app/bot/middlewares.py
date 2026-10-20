import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import repo


class DbSessionMiddleware(BaseMiddleware):
    """One DB session/transaction per update; also registers/refreshes the sender in ``users``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._factory() as session:
            data["session"] = session
            user = data.get("event_from_user")
            if user is not None and not user.is_bot:
                await repo.upsert_user(session, user.id, user.username, user.first_name)
            result = await handler(event, data)
            await session.commit()
            return result


class ThrottleMiddleware(BaseMiddleware):
    """Drops updates from a user who sends faster than ``min_interval`` seconds apart (in-memory)."""

    def __init__(self, min_interval: float = 0.4) -> None:
        self._min_interval = min_interval
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        if now - self._last.get(user.id, 0.0) < self._min_interval:
            if isinstance(event, CallbackQuery):
                await event.answer("Не так быстро 🙂")
            return None
        self._last[user.id] = now
        if len(self._last) > 10_000:  # keep memory bounded
            cutoff = now - 60
            self._last = {k: v for k, v in self._last.items() if v > cutoff}
        return await handler(event, data)
