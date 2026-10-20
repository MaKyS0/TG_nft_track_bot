import asyncio
import logging
from enum import Enum
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from app.domain import Listing
from app.formatting import DEAL_NOTE, format_listing
from app.ratelimit import AsyncRateLimiter

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024  # Telegram's limit for a photo caption


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

    @staticmethod
    def _markup(listing: Listing) -> InlineKeyboardMarkup:
        buttons = [[InlineKeyboardButton(text="🔗 Открыть подарок", url=listing.url)]]
        if listing.market_url:
            buttons.insert(0, [InlineKeyboardButton(text=f"🛒 Купить на {listing.market_title}", url=listing.market_url)])
        if listing.owner_url:
            buttons.append([InlineKeyboardButton(text="👤 Профиль владельца", url=listing.owner_url)])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def notify(self, user_id: int, listing: Listing, filter_name: str) -> NotifyResult:
        text = f"🔔 <b>Новое предложение по вашему фильтру</b>\n\n{format_listing(listing, filter_name=filter_name)}\n\n{DEAL_NOTE}"
        markup = self._markup(listing)

        # With a picture when possible (Telegram fetches the URL itself); plain text if that fails.
        if listing.image_url and len(text) <= CAPTION_LIMIT:
            result = await self._deliver(user_id, markup, text, photo=listing.image_url)
            if result is not NotifyResult.FAILED:
                return result
            log.info("Picture for %s could not be sent, falling back to text", listing.slug)
        return await self._deliver(user_id, markup, text)

    async def _deliver(
        self, user_id: int, markup: InlineKeyboardMarkup, text: str, *, photo: str | None = None
    ) -> NotifyResult:
        for attempt in range(self.MAX_RETRIES + 1):
            await self._limiter.acquire()
            try:
                if photo:
                    await self._bot.send_photo(user_id, photo, caption=text, reply_markup=markup)
                else:
                    await self._bot.send_message(
                        user_id, text, reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True)
                    )
                return NotifyResult.SENT
            except TelegramRetryAfter as e:
                log.warning("Telegram flood control, sleeping %ss (attempt %d)", e.retry_after, attempt + 1)
                await asyncio.sleep(e.retry_after)
            except TelegramForbiddenError:
                return NotifyResult.BLOCKED
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower() or "user is deactivated" in str(e).lower():
                    return NotifyResult.BLOCKED
                log.warning("Telegram rejected the message for user %s: %s", user_id, e)
                return NotifyResult.FAILED
            except TelegramAPIError as e:
                log.error("Failed to notify user %s: %s", user_id, e)
                return NotifyResult.FAILED
        log.error("Gave up notifying user %s after repeated flood control", user_id)
        return NotifyResult.FAILED
