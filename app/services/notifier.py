import asyncio
import logging
from enum import Enum
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from app.domain import Listing
from app.formatting import DEAL_NOTE, format_listing
from app.ratelimit import AsyncRateLimiter

log = logging.getLogger(__name__)


class NotifyResult(Enum):
    SENT = "sent"
    BLOCKED = "blocked"  # user blocked the bot / chat is gone: stop messaging them
    FAILED = "failed"


class Notifier(Protocol):
    async def notify(self, user_id: int, listing: Listing, filter_name: str) -> NotifyResult: ...


class TelegramNotifier:
    MAX_RETRIES = 2

    def __init__(self, bot: Bot, rate_per_second: float = 20) -> None:
        self._bot = bot
        self._limiter = AsyncRateLimiter(rate_per_second)

    async def notify(self, user_id: int, listing: Listing, filter_name: str) -> NotifyResult:
        text = f"🔔 <b>Новое предложение по вашему фильтру</b>\n\n{format_listing(listing, filter_name=filter_name)}\n\n{DEAL_NOTE}"
        buttons = [[InlineKeyboardButton(text="🔗 Открыть подарок", url=listing.url)]]
        if listing.owner_url:
            buttons.append([InlineKeyboardButton(text="👤 Профиль владельца", url=listing.owner_url)])
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)

        for attempt in range(self.MAX_RETRIES + 1):
            await self._limiter.acquire()
            try:
                await self._bot.send_message(
                    user_id,
                    text,
                    reply_markup=markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return NotifyResult.SENT
            except TelegramRetryAfter as e:
                log.warning("Telegram flood control, sleeping %ss (attempt %d)", e.retry_after, attempt + 1)
                await asyncio.sleep(e.retry_after)
            except TelegramForbiddenError:
                return NotifyResult.BLOCKED
            except TelegramAPIError as e:
                if "chat not found" in str(e).lower() or "user is deactivated" in str(e).lower():
                    return NotifyResult.BLOCKED
                log.error("Failed to notify user %s: %s", user_id, e)
                return NotifyResult.FAILED
        log.error("Gave up notifying user %s after repeated flood control", user_id)
        return NotifyResult.FAILED
