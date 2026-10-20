"""Real marketplace data via MTProto (Telethon).

The Bot API has no method for browsing resale offers, so this source logs in with a *service user
account owned by the operator* and calls the documented MTProto methods:

* ``payments.getStarGifts``        - gift collections (to learn which ones have resale offers)
* ``payments.getResaleStarGifts``  - offers of one collection, newest first

User accounts of bot users are never involved and no codes/passwords are handled here: the operator
logs in once with ``scripts/make_session.py`` and supplies the resulting session string as a secret.
"""

import asyncio
import logging

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl import functions, types

from app.domain import STARS, TON, Collection, Listing, SourceError, SourceRateLimited
from app.ratelimit import AsyncRateLimiter
from app.sources.base import GiftSource

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


def _rarity(attribute: types.TypeStarGiftAttribute) -> int | None:
    # Newer layers use StarGiftAttributeRarity(permille) plus tier-only variants without a number.
    return getattr(getattr(attribute, "rarity", None), "permille", None)


def _price(gift: types.StarGiftUnique) -> tuple[int, str] | None:
    amounts = gift.resell_amount or []
    for amount in amounts:
        if isinstance(amount, types.StarsAmount):
            return int(amount.amount), STARS
    for amount in amounts:
        if isinstance(amount, types.StarsTonAmount):
            return int(amount.amount), TON  # nanoTON
    return None


def _owner(
    gift: types.StarGiftUnique,
    users: dict[int, types.User],
    chats: dict[int, types.TypeChat],
) -> tuple[int | None, str | None, str | None]:
    """(id, public @username, display name) using only what the API itself returned."""
    peer = gift.owner_id
    owner_id: int | None = None
    username: str | None = None
    name: str | None = gift.owner_name or None
    if isinstance(peer, types.PeerUser):
        owner_id = peer.user_id
        user = users.get(peer.user_id)
        if user is not None:
            username = getattr(user, "username", None)
            full_name = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p)
            name = full_name or name
    elif isinstance(peer, types.PeerChannel):
        owner_id = peer.channel_id
        chat = chats.get(peer.channel_id)
        if chat is not None:
            username = getattr(chat, "username", None)
            name = getattr(chat, "title", None) or name
    return owner_id, username or None, name


def parse_resale_gift(
    gift: types.TypeStarGift,
    users: dict[int, types.User],
    chats: dict[int, types.TypeChat],
    fallback_collection: Collection,
) -> Listing | None:
    """Convert a ``StarGiftUnique`` from ``payments.ResaleStarGifts`` into a ``Listing``.

    Returns None for entries that are not upgraded gifts or have no price (not actually for sale).
    """
    if not isinstance(gift, types.StarGiftUnique):
        return None
    price = _price(gift)
    if price is None:
        return None

    model = symbol = backdrop = None
    model_rarity = symbol_rarity = backdrop_rarity = None
    backdrop_center = backdrop_edge = None
    for attribute in gift.attributes:
        if isinstance(attribute, types.StarGiftAttributeModel):
            model, model_rarity = attribute.name, _rarity(attribute)
        elif isinstance(attribute, types.StarGiftAttributePattern):
            symbol, symbol_rarity = attribute.name, _rarity(attribute)
        elif isinstance(attribute, types.StarGiftAttributeBackdrop):
            backdrop, backdrop_rarity = attribute.name, _rarity(attribute)
            backdrop_center = attribute.center_color & 0xFFFFFF
            backdrop_edge = attribute.edge_color & 0xFFFFFF

    owner_id, owner_username, owner_name = _owner(gift, users, chats)
    return Listing(
        slug=gift.slug,
        collection_id=gift.gift_id or fallback_collection.id,
        collection=gift.title or fallback_collection.title,
        number=gift.num,
        price=price[0],
        currency=price[1],
        model=model,
        model_rarity=model_rarity,
        backdrop=backdrop,
        backdrop_rarity=backdrop_rarity,
        backdrop_center=backdrop_center,
        backdrop_edge=backdrop_edge,
        symbol=symbol,
        symbol_rarity=symbol_rarity,
        owner_id=owner_id,
        owner_username=owner_username,
        owner_name=owner_name,
    )


class TelethonGiftSource(GiftSource):
    name = "telethon"

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
        self._client = client
        log.info("Telethon source connected")

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def _call(self, request):
        if self._client is None:
            raise SourceError("source is not started")
        await self._limiter.acquire()
        try:
            return await asyncio.wait_for(self._client(request), REQUEST_TIMEOUT)
        except errors.FloodWaitError as e:
            raise SourceRateLimited(float(e.seconds)) from e
        except (errors.RPCError, OSError, asyncio.TimeoutError) as e:
            raise SourceError(f"{type(request).__name__} failed: {e!r}") from e

    async def list_collections(self) -> list[Collection]:
        result = await self._call(functions.payments.GetStarGiftsRequest(hash=0))
        gifts = getattr(result, "gifts", None)
        if gifts is None:  # StarGiftsNotModified cannot happen with hash=0, but never trust the wire
            raise SourceError(f"unexpected getStarGifts response: {type(result).__name__}")
        collections = []
        for gift in gifts:
            if isinstance(gift, types.StarGift) and gift.title and gift.availability_resale:
                collections.append(
                    Collection(
                        id=gift.id,
                        title=gift.title,
                        resale_count=gift.availability_resale,
                        floor_price=gift.resell_min_stars,
                    )
                )
        return collections

    async def fetch_listings(self, collection: Collection, limit: int) -> list[Listing]:
        # No sort flag set = default ordering by listing date, newest first.
        result = await self._call(
            functions.payments.GetResaleStarGiftsRequest(gift_id=collection.id, offset="", limit=limit)
        )
        users = {u.id: u for u in result.users if isinstance(u, types.User)}
        chats = {c.id: c for c in result.chats}
        listings = []
        for gift in result.gifts:
            listing = parse_resale_gift(gift, users, chats, collection)
            if listing is not None:
                listings.append(listing)
        return listings
