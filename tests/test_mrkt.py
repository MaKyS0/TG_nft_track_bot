import logging

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.domain import Collection, SourceError, SourceRateLimited
from app.sources.mrkt import MrktGiftSource, clean_text, parse_gift

PEPE = Collection(id=1, title="Plush Pepe")
TOKEN = "tok.en-123_abc=="
INIT_DATA = "query_id=AAH&user=%7B%22id%22%3A1%7D&auth_date=1&hash=deadbeef"


def gift(**overrides):
    """Shaped like the Gift model of the open-source MRKT clients (camelCase wire names)."""
    item = {
        "id": "g1",
        "number": 123,
        "collectionName": "plush_pepe",
        "collectionTitle": "Plush Pepe",
        "modelName": "Classic",
        "modelRarityPerMille": 15,
        "backdropName": "Satin Gold",
        "backdropRarityPerMille": 20,
        "backdropColorsCenterColor": 0xE3BE62,
        "backdropColorsEdgeColor": 0x9A7421,
        "symbolName": "Stars",
        "symbolRarityPerMille": 30,
        "salePrice": 12_500_000_000,
        "isOnSale": True,
    }
    item.update(overrides)
    return item


# ── parsing ────────────────────────────────────────────────────────────────


def test_parse_gift_maps_fields():
    listing = parse_gift(gift(), PEPE)
    assert (listing.market, listing.currency, listing.price) == ("mrkt", "TON", 12_500_000_000)
    assert (listing.slug, listing.number, listing.collection) == ("PlushPepe-123", 123, "Plush Pepe")
    assert (listing.model, listing.model_rarity, listing.backdrop, listing.symbol) == ("Classic", 15, "Satin Gold", "Stars")
    assert (listing.backdrop_center, listing.backdrop_edge) == (0xE3BE62, 0x9A7421)
    assert listing.owner_url is None and listing.url == "https://t.me/nft/PlushPepe-123"
    assert listing.market_title == "MRKT" and listing.market_url == "https://t.me/mrkt/app"
    assert listing.key == "mrkt:PlushPepe-123|12500000000|TON"


def test_apostrophes_are_dropped_from_slug_like_telegram_does():
    listing = parse_gift(gift(), Collection(id=2, title="Durov's Cap"))
    assert listing.slug == "DurovsCap-123"


@pytest.mark.parametrize(
    "bad",
    [
        "not a dict",
        None,
        gift(isOnSale=False),
        gift(salePrice=0),
        gift(salePrice=-5),
        gift(salePrice=True),
        gift(salePrice="12"),
        gift(salePrice=10**30),
        gift(number=0),
        gift(number="7"),
        gift(number=None),
    ],
)
def test_invalid_offers_are_dropped(bad):
    assert parse_gift(bad, PEPE) is None


def test_collection_without_ascii_letters_cannot_produce_a_slug():
    assert parse_gift(gift(), Collection(id=3, title="Пепе")) is None


def test_untrusted_text_is_sanitised():
    listing = parse_gift(gift(modelName="Evil‮\x00<script>" + "x" * 500), PEPE)
    assert "‮" not in listing.model and "\x00" not in listing.model and len(listing.model) <= 64
    assert clean_text(123) is None and clean_text("   ") is None


def test_out_of_range_numbers_become_unknown():
    listing = parse_gift(gift(modelRarityPerMille=99999, backdropColorsCenterColor="red"), PEPE)
    assert listing.model_rarity is None and listing.backdrop_center is None


# ── HTTP behaviour against a fake MRKT ─────────────────────────────────────


class FakeMrkt:
    def __init__(self):
        self.auth_calls = []
        self.saling_calls = []
        self.saling_status = [200]  # consumed one per request, last one repeats
        self.pages = [[gift()]]
        self.auth_body = {"token": TOKEN}
        self.raw_body = None
        self.headers = {}

    async def auth(self, request):
        self.auth_calls.append(await request.json())
        return web.json_response(self.auth_body)

    async def saling(self, request):
        body = await request.json()
        self.saling_calls.append((body, dict(request.headers)))
        status = self.saling_status.pop(0) if len(self.saling_status) > 1 else self.saling_status[0]
        if status != 200:
            return web.Response(status=status, text="blocked", headers=self.headers)
        if self.raw_body is not None:
            return web.Response(body=self.raw_body)
        index = int(body.get("cursor") or 0)
        gifts = self.pages[index] if index < len(self.pages) else []
        cursor = str(index + 1) if index + 1 < len(self.pages) else None
        return web.json_response({"gifts": gifts, "total": 99, "cursor": cursor})


@pytest.fixture
async def mrkt():
    fake = FakeMrkt()
    app = web.Application()
    app.router.add_post("/api/v1/auth", fake.auth)
    app.router.add_post("/api/v1/gifts/saling", fake.saling)
    async with TestServer(app) as server:
        async with aiohttp.ClientSession() as http:
            calls = []

            async def init_data():
                calls.append(1)
                return INIT_DATA

            source = MrktGiftSource(
                init_data, request_delay=0.2, page_size=2, max_pages=3,
                base_url=str(server.make_url("/api/v1")), http=http,
            )
            fake.source, fake.init_calls = source, calls
            yield fake


async def test_login_then_listing_with_token_headers(mrkt):
    listings = await mrkt.source.fetch_listings(PEPE, 50)
    assert [item.slug for item in listings] == ["PlushPepe-123"]
    assert mrkt.auth_calls == [{"data": INIT_DATA}]
    body, headers = mrkt.saling_calls[0]
    assert body == {"count": 2, "collectionNames": ["Plush Pepe"]}
    assert headers["Authorization"] == TOKEN and headers["Cookie"] == f"access_token={TOKEN}"

    await mrkt.source.fetch_listings(PEPE, 50)
    assert len(mrkt.auth_calls) == 1  # token is reused


async def test_pagination_follows_cursor_up_to_limits(mrkt):
    mrkt.pages = [[gift(number=n)] for n in (1, 2, 3, 4)]
    listings = await mrkt.source.fetch_listings(PEPE, 50)
    assert [item.number for item in listings] == [1, 2, 3]  # max_pages=3
    assert [call[0].get("cursor") for call in mrkt.saling_calls] == [None, "1", "2"]

    mrkt.saling_calls.clear()
    assert [item.number for item in await mrkt.source.fetch_listings(PEPE, 2)] == [1, 2]  # limit reached


async def test_expired_token_triggers_exactly_one_relogin(mrkt):
    await mrkt.source.fetch_listings(PEPE, 50)
    mrkt.saling_status = [401, 200]
    await mrkt.source.fetch_listings(PEPE, 50)
    assert len(mrkt.auth_calls) == 2

    mrkt.saling_status = [401]  # still unauthorised after re-login: give up
    with pytest.raises(SourceError, match="401"):
        await mrkt.source.fetch_listings(PEPE, 50)


async def test_rate_limit_is_reported_with_clamped_wait(mrkt):
    mrkt.saling_status = [429]
    mrkt.headers = {"Retry-After": "7"}
    with pytest.raises(SourceRateLimited) as info:
        await mrkt.source.fetch_listings(PEPE, 50)
    assert info.value.retry_after == 7

    mrkt.headers = {"Retry-After": "999999"}
    with pytest.raises(SourceRateLimited) as info:
        await mrkt.source.fetch_listings(PEPE, 50)
    assert info.value.retry_after == 900

    mrkt.headers = {}
    with pytest.raises(SourceRateLimited) as info:
        await mrkt.source.fetch_listings(PEPE, 50)
    assert info.value.retry_after == 60


async def test_block_is_reported_not_bypassed(mrkt):
    mrkt.saling_status = [403]
    with pytest.raises(SourceError, match="not bypassing"):
        await mrkt.source.fetch_listings(PEPE, 50)
    assert len(mrkt.saling_calls) == 1  # no retry with other fingerprints


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        pytest.param(b"<html>nope</html>", "not JSON", id="html"),
        pytest.param(b'{"items": []}', "shape", id="no-gifts-key"),
        pytest.param(b"[]", "shape", id="list"),
        pytest.param(b"x" * 2_100_000, "large", id="oversized"),
    ],
)
async def test_garbage_responses_are_source_errors(mrkt, raw, message):
    mrkt.raw_body = raw
    with pytest.raises(SourceError, match=message):
        await mrkt.source.fetch_listings(PEPE, 50)


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": 5}, {"token": "bad token\r\nX: y"}, [1]])
async def test_unusable_login_response(mrkt, body):
    mrkt.auth_body = body
    with pytest.raises(SourceError, match="token"):
        await mrkt.source.fetch_listings(PEPE, 50)


async def test_secrets_never_reach_the_logs(mrkt, caplog):
    caplog.set_level(logging.DEBUG)
    mrkt.saling_status = [401, 403]
    with pytest.raises(SourceError):
        await mrkt.source.fetch_listings(PEPE, 50)
    assert TOKEN not in caplog.text and INIT_DATA not in caplog.text and "deadbeef" not in caplog.text


async def test_network_failure_is_a_source_error():
    async with aiohttp.ClientSession() as http:
        source = MrktGiftSource(lambda: _init(), request_delay=0.2, base_url="http://127.0.0.1:1/api/v1", http=http)
        with pytest.raises(SourceError, match="failed"):
            await source.fetch_listings(PEPE, 10)


async def _init():
    return INIT_DATA


async def test_configured_collections_are_listed_with_stable_negative_ids():
    source = MrktGiftSource(_init, collections=["Plush Pepe", "Heart Locket"])
    first = await source.list_collections()
    assert [c.title for c in first] == ["Plush Pepe", "Heart Locket"]
    assert all(c.id < 0 for c in first)
    assert first == await MrktGiftSource(_init, collections=["Plush Pepe", "Heart Locket"]).list_collections()
