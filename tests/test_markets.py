import dataclasses
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlalchemy import text

from app.bot.parsing import InvalidValue, parse_field_value, validate_filter_payload
from app.config import Settings, parse_sources
from app.db import repo
from app.db.base import create_engine, init_db
from app.domain import Collection, SourceError, SourceRateLimited
from app.formatting import format_filter, format_listing, ton_str
from app.matching import matches
from app.services.monitor import Monitor, MonitorConfig
from app.services.notifier import NotifyResult
from app.sources.base import FetchResult, GiftSource
from app.sources.composite import CompositeSource
from app.sources.telegram_account import init_data_from_url

PEPE = Collection(id=1, title="Plush Pepe")


def flt(**kw):
    base = dict(collection=None, model=None, backdrop=None, symbol=None, min_price=None, max_price=None,
                min_ton=None, max_ton=None, max_rarity=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── TON filters ────────────────────────────────────────────────────────────


@pytest.fixture
def ton_offer(make_listing):
    return make_listing(slug="PlushPepe-9", currency="TON", price=12_500_000_000, market="mrkt")


def test_ton_bounds_apply_to_ton_offers(ton_offer):
    assert matches(ton_offer, flt(min_ton=10_000_000_000, max_ton=15_000_000_000))
    assert not matches(ton_offer, flt(max_ton=12_499_999_999))
    assert not matches(ton_offer, flt(min_ton=12_500_000_001))
    assert matches(ton_offer, flt(collection="Plush Pepe"))  # no price bounds at all: any currency


def test_bounds_in_one_currency_never_match_offers_in_the_other(ton_offer, make_listing):
    assert not matches(ton_offer, flt(max_price=10**9))  # Stars-only filter vs TON offer
    assert not matches(make_listing(price=100), flt(max_ton=10**12))  # TON-only filter vs Stars offer
    both = flt(max_price=500, max_ton=20_000_000_000)
    assert matches(make_listing(price=100), both) and matches(ton_offer, both)
    assert not matches(make_listing(price=900), both)


def test_ton_input_parsing():
    assert parse_field_value("min_ton", "12.5") == 12_500_000_000
    assert parse_field_value("max_ton", "1 000,25") == 1_000_250_000_000
    assert parse_field_value("min_ton", "0") == 0
    assert parse_field_value("min_ton", "-") is None
    for bad in ("abc", "-1", "1e400", "NaN", "Infinity", "1.23456", "2000000000"):
        with pytest.raises(InvalidValue):
            parse_field_value("min_ton", bad)


def test_ton_range_is_cross_checked():
    with pytest.raises(InvalidValue):
        validate_filter_payload({"min_ton": "5", "max_ton": "2"})
    with pytest.raises(InvalidValue):
        validate_filter_payload({"min_ton": "5"}, current=flt(max_ton=2_000_000_000))
    assert validate_filter_payload({"min_ton": 1.5, "max_ton": "2"}) == {"min_ton": 1_500_000_000, "max_ton": 2_000_000_000}


def test_ton_formatting(ton_offer):
    assert ton_str(12_500_000_000) == "12.5" and ton_str(3_000_000_000) == "3" and ton_str(1_234_567_890) == "1.23456789"
    assert "12.5 TON" in format_listing(ton_offer)
    assert "MRKT" in format_listing(ton_offer)
    assert "12.5" in format_filter(flt(min_ton=12_500_000_000))


# ── composite source ───────────────────────────────────────────────────────


class Stub(GiftSource):
    def __init__(self, name, market, titles=(), *, follows=False, offers=None):
        self.name, self.market, self.follows_catalog = name, market, follows
        self.titles, self.offers = list(titles), offers or {}
        self.error: SourceError | None = None
        self.fetched: list[str] = []

    async def list_collections(self):
        if self.error:
            raise self.error
        return [Collection(id=i + 100, title=t) for i, t in enumerate(self.titles)]

    async def fetch_listings(self, collection, limit):
        self.fetched.append(collection.title)
        if self.error:
            raise self.error
        return list(self.offers.get(collection.title, []))


async def test_collections_are_merged_by_title_and_catalog_followers_are_polled(make_listing):
    telegram = Stub("telegram", "telegram", ["Plush Pepe", "Heart Locket"], offers={"Plush Pepe": [make_listing()]})
    mrkt = Stub("mrkt", "mrkt", [], follows=True, offers={"Plush Pepe": [make_listing(slug="PlushPepe-5", market="mrkt")]})
    source = CompositeSource([telegram, mrkt])
    collections = await source.list_collections()
    assert [c.title for c in collections] == ["Plush Pepe", "Heart Locket"]

    result = await source.fetch(collections[0], 50)
    assert {item.market for item in result.listings} == {"telegram", "mrkt"}
    assert result.markets_ok == {"telegram", "mrkt"}
    assert mrkt.fetched == ["Plush Pepe"]


async def test_one_failing_source_does_not_stop_the_other(make_listing):
    telegram = Stub("telegram", "telegram", ["Plush Pepe"], offers={"Plush Pepe": [make_listing()]})
    mrkt = Stub("mrkt", "mrkt", follows=True)
    mrkt.error = SourceRateLimited(120)
    source = CompositeSource([telegram, mrkt])
    collections = await source.list_collections()

    result = await source.fetch(collections[0], 50)
    assert result.markets_ok == {"telegram"} and len(result.listings) == 1  # mrkt did NOT count as answered

    mrkt.fetched.clear()
    await source.fetch(collections[0], 50)
    assert mrkt.fetched == []  # cooling down, not hammered again


async def test_all_sources_failing_raises():
    a, b = Stub("a", "telegram", ["X"]), Stub("b", "mrkt", ["X"])
    source = CompositeSource([a, b])
    await source.list_collections()
    a.error = SourceError("down")
    b.error = SourceError("down too")
    with pytest.raises(SourceError):
        await source.fetch(Collection(id=100, title="X"), 10)


# ── monitor: baseline is per market ────────────────────────────────────────


class Scripted(GiftSource):
    """Returns whatever the test put in `result` for the next fetch."""

    name = "scripted"

    def __init__(self):
        self.result: FetchResult | None = None

    async def list_collections(self):
        return [PEPE]

    async def fetch_listings(self, collection, limit):  # pragma: no cover - fetch() is overridden
        raise NotImplementedError

    async def fetch(self, collection, limit):
        return self.result


class Recorder:
    def __init__(self):
        self.sent = []

    async def notify(self, user_id, listing, filter_name):
        self.sent.append((listing.market, listing.slug))
        return NotifyResult.SENT


async def test_enabling_a_new_market_later_does_not_flood_users(session_factory, make_listing):
    async with session_factory() as s:
        await repo.upsert_user(s, 1, "u1", "U")
        f = await repo.create_filter(s, 1, 10)
        f.collection = "Plush Pepe"
        await s.commit()
    source, notes = Scripted(), Recorder()
    monitor = Monitor(source, session_factory, notes, MonitorConfig(poll_interval=0.01))

    tg_old = make_listing(slug="PlushPepe-1")
    mrkt_old = make_listing(slug="PlushPepe-2", market="mrkt", currency="TON", price=5_000_000_000)

    source.result = FetchResult([tg_old], frozenset({"telegram"}))  # MRKT down on day one
    await monitor.poll_once()

    source.result = FetchResult([tg_old, mrkt_old], frozenset({"telegram", "mrkt"}))  # MRKT comes online
    await monitor.poll_once()
    assert notes.sent == []  # its existing inventory is baseline, not news

    mrkt_new = dataclasses.replace(mrkt_old, slug="PlushPepe-3")
    source.result = FetchResult([tg_old, mrkt_old, mrkt_new], frozenset({"telegram", "mrkt"}))
    await monitor.poll_once()
    assert notes.sent == [("mrkt", "PlushPepe-3")]

    await monitor.poll_once()
    assert len(notes.sent) == 1  # no duplicates


async def test_market_that_did_not_answer_is_not_baselined(session_factory, make_listing):
    async with session_factory() as s:
        await repo.upsert_user(s, 1, "u1", "U")
        await repo.create_filter(s, 1, 10)
        await s.commit()
    source, notes = Scripted(), Recorder()
    monitor = Monitor(source, session_factory, notes, MonitorConfig(poll_interval=0.01))
    source.result = FetchResult([], frozenset({"telegram"}))  # mrkt silent: must stay un-baselined
    await monitor.poll_once()
    async with session_factory() as s:
        assert not await repo.is_collection_seeded(s, repo.poll_key(1, "mrkt"))
        assert await repo.is_collection_seeded(s, repo.poll_key(1, "telegram"))


# ── account / config / database ────────────────────────────────────────────


def test_init_data_is_extracted_from_the_web_view_url():
    data = "query_id=AAH&user=%7B%22id%22%3A1%7D&auth_date=1&hash=abc"
    url = f"https://app.example/#tgWebAppData={quote(data, safe='')}&tgWebAppVersion=8.0&tgWebAppPlatform=android"
    assert init_data_from_url(url) == data
    with pytest.raises(SourceError):
        init_data_from_url("https://app.example/")


def test_source_list_parsing():
    assert parse_sources("telegram, MRKT") == ["telegram", "mrkt"]
    assert parse_sources("telethon") == ["telegram"]
    for bad in ("", "tonnel", "mock,mrkt"):
        with pytest.raises(ValueError):
            parse_sources(bad)


def settings(**kw):
    base = dict(bot_token="1:x", database_url="sqlite+aiosqlite://")
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_mrkt_needs_telegram_credentials_and_a_collection_list_when_alone():
    creds = dict(tg_api_id=1, tg_api_hash="h", tg_session_string="s")
    with pytest.raises(ValueError, match="TG_API_ID"):
        settings(gift_source="telegram,mrkt")
    with pytest.raises(ValueError, match="MRKT_COLLECTIONS"):
        settings(gift_source="mrkt", **creds)
    assert settings(gift_source="mrkt", mrkt_collections="Plush Pepe, Heart Locket", **creds).mrkt_collection_names == [
        "Plush Pepe", "Heart Locket",
    ]
    assert settings(gift_source="telegram,mrkt", **creds).sources == ["telegram", "mrkt"]


async def test_old_database_is_upgraded_in_place(tmp_path):
    """A database created by an earlier version (without the newest columns) keeps its data."""
    from app.db.models import Base

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("INSERT INTO users (id, notifications_enabled, is_blocked, created_at, last_seen_at) "
                                "VALUES (1, 1, 0, '2026-01-01', '2026-01-01')"))
        await conn.execute(text("INSERT INTO user_filters (user_id, name, is_active, created_at, max_price) "
                                "VALUES (1, 'old filter', 1, '2026-01-01', 500)"))
        await conn.execute(text("INSERT INTO listings (listing_key, slug, collection_id, collection, number, price, currency, "
                                "first_seen_at, last_seen_at) VALUES ('A-1|5|XTR', 'A-1', 1, 'A', 1, 5, 'XTR', '2026-01-01', '2026-01-01')"))
        for statement in ("ALTER TABLE listings DROP COLUMN market", "ALTER TABLE user_filters DROP COLUMN min_ton",
                          "ALTER TABLE user_filters DROP COLUMN max_ton"):
            await conn.execute(text(statement))  # what an earlier version's database looks like
    await init_db(engine)
    await init_db(engine)  # idempotent
    async with engine.connect() as conn:
        market = (await conn.execute(text("SELECT market FROM listings"))).scalar_one()
        assert market == "telegram"  # existing rows get the default market
        row = (await conn.execute(text("SELECT name, max_price, min_ton, max_ton FROM user_filters"))).one()
        assert tuple(row) == ("old filter", 500, None, None)
    await engine.dispose()
