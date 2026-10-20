"""MRKT marketplace (https://t.me/mrkt): offers priced in TON.

IMPORTANT - read before relying on this:

* MRKT publishes no API documentation. The protocol below (login with Telegram initData, then
  ``POST /gifts/saling``) and the response fields were taken from the open-source clients ``amrkt`` and
  ``mrkts``. It could NOT be verified against the live service while writing this (login needs a real
  Telegram account), so treat it as experimental and expect to adjust field names if MRKT changes.
* We poll politely: honest User-Agent, a pause between requests, and we obey 429/403. We do NOT
  imitate a browser or rotate fingerprints to get around their bot protection; if MRKT blocks us,
  the source reports it and backs off.
* Everything MRKT returns is untrusted input: strings are sanitised and length-limited, numbers are
  type-checked, links are built only from our own constants, and the token never reaches the logs.
"""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence

import aiohttp

from app.domain import MRKT, TON, Collection, Listing, SourceError, SourceRateLimited, is_safe_slug
from app.ratelimit import AsyncRateLimiter
from app.sources.base import GiftSource

log = logging.getLogger(__name__)

API_URL = "https://api.tgmrkt.io/api/v1"
USER_AGENT = "nft-tracker/1.0 (personal price tracker; polite polling)"
MAX_BODY = 2_000_000
MAX_PRICE_NANO = 10**18
_CONTROL = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u200b-\\u200f\\u2028-\\u202e\\u2066-\\u2069]")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{10,4096}")


def clean_text(value: object, limit: int = 64) -> str | None:
    if not isinstance(value, str):
        return None
    return _CONTROL.sub("", value).strip()[:limit] or None


def clean_int(value: object, low: int, high: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def synthetic_collection_id(title: str) -> int:
    """Stable negative id for collections known only by name (never collides with Telegram's ids)."""
    return -int(hashlib.sha1(title.casefold().encode(), usedforsecurity=False).hexdigest()[:10], 16)


def parse_gift(item: object, collection: Collection) -> Listing | None:
    """Turn one entry of ``/gifts/saling`` into a Listing; None if it is not a valid active offer."""
    if not isinstance(item, dict) or item.get("isOnSale") is False:
        return None
    price = clean_int(item.get("salePrice"), 1, MAX_PRICE_NANO)  # nanoTON, what the buyer pays
    number = clean_int(item.get("number"), 1, 10**9)
    stem = re.sub(r"[^A-Za-z0-9]", "", collection.title)
    if price is None or number is None or not stem:
        return None
    slug = f"{stem}-{number}"  # Telegram's own slug convention, so the t.me/nft/ link opens the gift
    if not is_safe_slug(slug):
        return None

    def mask(value: object) -> int | None:
        color = clean_int(value, -(2**31), 2**32)
        return None if color is None else color & 0xFFFFFF

    return Listing(
        slug=slug,
        collection_id=collection.id,
        collection=collection.title,
        number=number,
        price=price,
        currency=TON,
        market=MRKT,
        model=clean_text(item.get("modelTitle")) or clean_text(item.get("modelName")),
        model_rarity=clean_int(item.get("modelRarityPerMille"), 0, 1000),
        backdrop=clean_text(item.get("backdropName")),
        backdrop_rarity=clean_int(item.get("backdropRarityPerMille"), 0, 1000),
        backdrop_center=mask(item.get("backdropColorsCenterColor")),
        backdrop_edge=mask(item.get("backdropColorsEdgeColor")),
        symbol=clean_text(item.get("symbolName")),
        symbol_rarity=clean_int(item.get("symbolRarityPerMille"), 0, 1000),
        # MRKT does not expose the seller; the gift is held by the marketplace.
    )


class MrktGiftSource(GiftSource):
    name = "mrkt"
    market = MRKT
    follows_catalog = True

    def __init__(
        self,
        get_init_data: Callable[[], Awaitable[str]],
        *,
        collections: Sequence[str] = (),
        request_delay: float = 2.0,
        page_size: int = 20,
        max_pages: int = 3,
        base_url: str = API_URL,
        http: aiohttp.ClientSession | None = None,
    ) -> None:
        self._get_init_data = get_init_data
        self._collections = [Collection(id=synthetic_collection_id(t), title=t) for t in collections]
        self._limiter = AsyncRateLimiter(1.0 / max(request_delay, 0.2))
        self._page_size = page_size
        self._max_pages = max_pages
        self._base = base_url.rstrip("/")
        self._http = http
        self._owns_http = http is None
        self._token: str | None = None

    async def start(self) -> None:
        if self._http is None:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": USER_AGENT}, trust_env=True
            )

    async def stop(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.close()
        self._http = None
        self._token = None

    async def _login(self) -> None:
        init_data = await self._get_init_data()
        data = await self._request("/auth", {"data": init_data}, auth=False)
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise SourceError("MRKT login returned no usable token")
        self._token = token
        log.info("Logged in to MRKT")

    async def _request(self, path: str, payload: dict, *, auth: bool = True, _retried: bool = False) -> object:
        if self._http is None:
            raise SourceError("MRKT source is not started")
        headers = {"Content-Type": "application/json"}
        if auth:
            if self._token is None:
                await self._login()
            headers["Authorization"] = self._token or ""
            headers["Cookie"] = f"access_token={self._token}"

        await self._limiter.acquire()
        try:
            async with self._http.post(self._base + path, json=payload, headers=headers) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):  # read(n) may return a partial body
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise SourceError("MRKT response is unexpectedly large")
                    chunks.append(chunk)
                body = b"".join(chunks)
                status = response.status
                retry_after = response.headers.get("Retry-After", "")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise SourceError(f"MRKT request failed: {type(e).__name__}") from e

        if status == 429:
            wait = float(retry_after) if retry_after.isdigit() else 60.0
            raise SourceRateLimited(min(max(wait, 5.0), 900.0))
        if status == 401 and auth and not _retried:
            self._token = None  # expired: log in again once
            return await self._request(path, payload, auth=auth, _retried=True)
        if status == 403:
            raise SourceError("MRKT refused the request (403: anti-bot protection or revoked access); not bypassing")
        if status != 200:
            raise SourceError(f"MRKT answered HTTP {status}")
        try:
            return json.loads(body)
        except ValueError as e:
            raise SourceError("MRKT answered with something that is not JSON") from e

    async def list_collections(self) -> list[Collection]:
        # No documented endpoint lists MRKT collections; they come from MRKT_COLLECTIONS or, when the
        # official Telegram source is enabled too, from Telegram's catalogue (merged by title).
        return list(self._collections)

    async def fetch_listings(self, collection: Collection, limit: int) -> list[Listing]:
        listings: list[Listing] = []
        cursor = ""
        for _ in range(self._max_pages):
            payload: dict = {"count": self._page_size, "collectionNames": [collection.title]}
            if cursor:
                payload["cursor"] = cursor
            data = await self._request("/gifts/saling", payload)
            gifts = data.get("gifts") if isinstance(data, dict) else None
            if not isinstance(gifts, list):
                raise SourceError("unexpected MRKT response shape (no 'gifts' list)")
            for item in gifts:
                listing = parse_gift(item, collection)
                if listing is not None:
                    listings.append(listing)
            cursor = data.get("cursor") if isinstance(data.get("cursor"), str) else ""
            if not cursor or not gifts or len(listings) >= limit:
                break
        return listings[:limit]
