from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings

SETTINGS = web.AppKey("settings", Settings)
SESSION_FACTORY = web.AppKey("session_factory", async_sessionmaker[AsyncSession])
RATE_LIMITER = web.AppKey("rate_limiter", object)

DB_SESSION = web.RequestKey("session", AsyncSession)
USER_ID = web.RequestKey("user_id", int)
