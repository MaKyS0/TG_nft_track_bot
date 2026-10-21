from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.tl import types

from app.config import Settings
from app.domain import UNPRICED, Collection, SourceError
from app.formatting import format_listing
from app.matching import matches
from app.sources.mrkt_vault import MrktVaultSource, parse_saved_gift
from tests.test_telethon_parse import unique_gift

TOKEN = "123456:TEST-TOKEN"


def saved(**overrides):
    return types.SavedStarGift(date=datetime(2026, 10, 21, tzinfo=UTC), gift=unique_gift(**overrides))


def page(entries, next_offset=""):
    return SimpleNamespace(gifts=entries, users=[types.User(id=77, username="mrktbank", first_name="MRKT")], chats=[], next_offset=next_offset)


class FakeAccount:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []
        self.resolved = []

    async def input_peer(self, username):
        self.resolved.append(username)
        return "peer"

    async def call(self, request):
        self.calls.append(request.offset)
        return self.pages.pop(0)


def test_saved_gift_becomes_an_unpriced_mrkt_listing_without_owner():
    listing = parse_saved_gift(saved(), {}, {})
    assert (listing.market, listing.currency, listing.price) == ("mrkt", UNPRICED, 0)
    assert not listing.has_price
    assert (listing.owner_id, listing.owner_username, listing.owner_name) == (None, None, None)  # the holder is MRKT
    assert listing.key == "mrkt:PlushPepe-42|0|NONE"
    assert (listing.model, listing.model_rarity) == ("Classic", 15)


@pytest.mark.parametrize("bad", [types.SavedStarGift(date=datetime(2026, 1, 1, tzinfo=UTC), gift=types.StarGift(id=1, sticker=None, stars=1, convert_stars=1)), object()])
def test_non_upgraded_entries_are_skipped(bad):
    assert parse_saved_gift(bad, {}, {}) is None


def test_unsafe_slug_is_skipped():
    assert parse_saved_gift(saved(slug="Bad/../Slug"), {}, {}) is None


def test_unpriced_listing_matches_only_filters_without_price_bounds(make_listing):
    listing = parse_saved_gift(saved(), {}, {})
    base = dict(collection="Plush Pepe", model=None, backdrop=None, symbol=None, max_rarity=None,
                min_price=None, max_price=None, min_ton=None, max_ton=None)
    assert matches(listing, SimpleNamespace(**base))
    assert not matches(listing, SimpleNamespace(**{**base, "max_price": 500}))
    assert not matches(listing, SimpleNamespace(**{**base, "max_ton": 10**9}))
    assert matches(listing, SimpleNamespace(**{**base, "max_rarity": 20}))


def test_message_says_to_look_up_the_price_on_the_market():
    text = format_listing(parse_saved_gift(saved(), {}, {}))
    assert "Цена: смотрите на <b>MRKT</b>" in text and "💎" not in text and "⭐" not in text


async def test_feed_is_shared_between_collections_and_filtered_by_title():
    account = FakeAccount([page([saved(), saved(title="Heart Locket", slug="HeartLocket-7", num=7, gift_id=9)])])
    source = MrktVaultSource(account, "mrktbank")
    titles = sorted(c.title for c in await source.list_collections())
    assert titles == ["Heart Locket", "Plush Pepe"]
    pepe = await source.fetch_listings(Collection(id=1, title="plush  pepe"), 10)
    assert [x.slug for x in pepe] == ["PlushPepe-42"]
    assert len(account.calls) == 1 and account.resolved == ["mrktbank"]  # one read serves every poll of the cycle


async def test_reads_a_second_page_only_when_telegram_offers_one():
    account = FakeAccount([page([saved()], next_offset="n1"), page([saved(slug="PlushPepe-43", num=43)])])
    source = MrktVaultSource(account, pages=3)
    found = await source.fetch_listings(Collection(id=1, title="Plush Pepe"), 10)
    assert [x.number for x in found] == [42, 43] and account.calls == ["", "n1"]


async def test_unexpected_response_is_a_source_error():
    source = MrktVaultSource(FakeAccount([SimpleNamespace(users=[], chats=[])]))
    with pytest.raises(SourceError):
        await source.list_collections()


def test_config_accepts_the_source_and_validates_the_username():
    creds = dict(bot_token=TOKEN, database_url="sqlite+aiosqlite://", tg_api_id=1, tg_api_hash="a" * 32, tg_session_string="s")
    settings = Settings(gift_source="telegram,mrkt_vault", mrkt_vault_username="@mrktbank", **creds)
    assert settings.sources == ["telegram", "mrkt_vault"] and settings.mrkt_vault_username == "mrktbank"
    with pytest.raises(ValueError):
        Settings(gift_source="mrkt_vault", **{k: v for k, v in creds.items() if k != "tg_session_string"})
    with pytest.raises(ValueError):
        Settings(gift_source="mrkt_vault", mrkt_vault_username="no way", **creds)
