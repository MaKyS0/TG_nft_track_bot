"""Gifts held by the marketplace's own Telegram account (MRKT keeps deposited gifts on ``@mrktbank``).

Telegram shows that profile's gifts publicly, newest first, through the documented MTProto method
``payments.getSavedStarGifts``. A gift that arrives there was just deposited with MRKT (usually in order to be
sold). This needs no MRKT login and no request to MRKT's servers.

Limits, by nature of the data: the profile shows *which* gifts MRKT holds, not their price or whether a gift
is actually on sale, so such listings are ``UNPRICED`` and the buyer must look at the offer on MRKT itself.
Only the newest pages are read (new arrivals are what a tracker needs).
"""

import asyncio
import logging
import time
from dataclasses import replace

from telethon.tl import functions, types

from app.domain import MRKT, UNPRICED, Collection, Listing, SourceError
from app.matching import normalize
from app.sources.base import GiftSource
from app.sources.telegram_account import TelegramAccount
from app.sources.telethon_source import unique_gift_listing

log = logging.getLogger(__name__)

PAGE_SIZE = 100


def parse_saved_gift(entry: object, users: dict, chats: dict) -> Listing | None:
    """One ``SavedStarGift`` of the market's profile as an unpriced MRKT listing."""
    gift = getattr(entry, "gift", None)
    if not isinstance(gift, types.StarGiftUnique) or not gift.title:
        return None
    listing = unique_gift_listing(gift, users, chats, Collection(id=gift.gift_id or 0, title=gift.title), 0, UNPRICED)
    if listing is None:
        return None
    # The holder is the marketplace itself, not the seller: do not present it as the owner.
    return _anonymous(listing)


def _anonymous(listing: Listing) -> Listing:
    return replace(listing, market=MRKT, owner_id=None, owner_username=None, owner_name=None)


class MrktVaultSource(GiftSource):
    name = "mrkt_vault"
    market = MRKT

    def __init__(self, account: TelegramAccount, username: str = "mrktbank", pages: int = 2, ttl: float = 20.0) -> None:
        self._account = account
        self._username = username
        self._pages = max(1, pages)
        self._ttl = ttl
        self._peer = None
        self._lock = asyncio.Lock()
        self._feed: list[Listing] = []
        self._feed_at = float("-inf")

    async def _newest(self) -> list[Listing]:
        """Newest gifts of the profile, shared by every collection poll of the same cycle."""
        async with self._lock:
            if time.monotonic() - self._feed_at < self._ttl:
                return self._feed
            if self._peer is None:
                self._peer = await self._account.input_peer(self._username)
            listings: list[Listing] = []
            offset = ""
            for _ in range(self._pages):
                page = await self._account.call(
                    functions.payments.GetSavedStarGiftsRequest(peer=self._peer, offset=offset, limit=PAGE_SIZE)
                )
                gifts = getattr(page, "gifts", None)
                if gifts is None:
                    raise SourceError(f"unexpected getSavedStarGifts response: {type(page).__name__}")
                users = {u.id: u for u in page.users if isinstance(u, types.User)}
                chats = {c.id: c for c in page.chats}
                listings.extend(x for x in (parse_saved_gift(e, users, chats) for e in gifts) if x is not None)
                offset = getattr(page, "next_offset", None) or ""
                if not offset or not gifts:
                    break
            self._feed, self._feed_at = listings, time.monotonic()
            log.info("MRKT account: %d newest gifts read", len(listings))
            return listings

    async def list_collections(self) -> list[Collection]:
        seen: dict[str, Collection] = {}
        for item in await self._newest():
            seen.setdefault(normalize(item.collection), Collection(id=item.collection_id, title=item.collection))
        return list(seen.values())

    async def fetch_listings(self, collection: Collection, limit: int) -> list[Listing]:
        wanted = normalize(collection.title)
        return [x for x in await self._newest() if normalize(x.collection) == wanted][:limit]
