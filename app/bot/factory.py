from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import filters, help, notifications, search, start, stats
from app.bot.middlewares import DbSessionMiddleware, ThrottleMiddleware
from app.config import Settings


def build_dispatcher(
    storage: BaseStorage,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    throttle_interval: float = 0.4,
) -> Dispatcher:
    dp = Dispatcher(storage=storage, settings=settings)

    # Update-level middleware runs after aiogram's own user-context middleware, so event_from_user is set.
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    throttle = ThrottleMiddleware(throttle_interval)
    dp.message.outer_middleware(throttle)
    dp.callback_query.outer_middleware(throttle)

    # Order matters: commands first, then menu buttons, then FSM text input.
    dp.include_routers(start.router, help.router, stats.router, notifications.router, search.router, filters.router)
    return dp
