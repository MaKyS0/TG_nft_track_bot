import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.factory import build_dispatcher
from app.bot.profile import configure_profile
from app.config import Settings, get_settings
from app.db.base import create_engine, create_session_factory, init_db
from app.logging_setup import setup_logging
from app.services.monitor import Monitor, MonitorConfig
from app.services.notifier import TelegramNotifier
from app.sources.base import GiftSource
from app.web.server import create_web_app, start_web

log = logging.getLogger("app")


def build_source(settings: Settings) -> GiftSource:
    if settings.gift_source == "telethon":
        from app.sources.telethon_source import TelethonGiftSource

        assert settings.tg_api_id and settings.tg_api_hash and settings.tg_session_string  # validated in Settings
        return TelethonGiftSource(
            api_id=settings.tg_api_id,
            api_hash=settings.tg_api_hash.get_secret_value(),
            session_string=settings.tg_session_string.get_secret_value(),
            request_delay=settings.source_request_delay,
        )
    from app.sources.mock import MockGiftSource

    log.warning("GIFT_SOURCE=mock: using FAKE demo data, not the real marketplace")
    return MockGiftSource()


def build_storage(settings: Settings) -> BaseStorage:
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url)
    return MemoryStorage()


async def amain() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, secrets=[settings.bot_token.get_secret_value()])

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    await init_db(engine)

    source = build_source(settings)
    await source.start()

    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = build_storage(settings)
    dp = build_dispatcher(storage, session_factory, settings)

    monitor = Monitor(
        source,
        session_factory,
        TelegramNotifier(bot, settings.notify_rate_per_second),
        MonitorConfig(
            poll_interval=settings.poll_interval_seconds,
            listings_per_collection=settings.listings_per_collection,
            catalog_ttl=settings.catalog_ttl_seconds,
        ),
    )
    monitor_task = asyncio.create_task(monitor.run(), name="monitor")
    web_runner = None

    try:
        if settings.webapp_url:
            web_runner = await start_web(
                create_web_app(session_factory, settings), settings.web_host, settings.web_port
            )
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            log.critical("BOT_TOKEN was rejected by Telegram: check the token from @BotFather")
            raise SystemExit(1) from None
        except TelegramNetworkError as e:
            log.critical("Cannot reach api.telegram.org: %s", e)
            raise SystemExit(1) from None
        log.info("Bot @%s started", me.username)
        await configure_profile(bot, settings.bot_name, settings.webapp_url)
        await dp.start_polling(bot)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
        monitor.stop()
        await asyncio.wait([monitor_task], timeout=10)
        monitor_task.cancel()
        await source.stop()
        await storage.close()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
