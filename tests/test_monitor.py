import dataclasses

import pytest

from app.db import repo
from app.domain import Collection, SourceError, SourceRateLimited
from app.services.monitor import Monitor, MonitorConfig
from app.services.notifier import NotifyResult
from app.sources.base import GiftSource

PEPE = Collection(id=1, title="Plush Pepe")
LOCKET = Collection(id=2, title="Heart Locket")


class FakeSource(GiftSource):
    name = "fake"

    def __init__(self):
        self.market = {PEPE.id: [], LOCKET.id: []}
        self.fetched = []
        self.fail_with: Exception | None = None

    async def list_collections(self):
        return [PEPE, LOCKET]

    async def fetch_listings(self, collection, limit):
        self.fetched.append(collection.title)
        if self.fail_with:
            raise self.fail_with
        return list(self.market[collection.id])


class FakeNotifier:
    def __init__(self):
        self.sent = []
        self.result = NotifyResult.SENT

    async def notify(self, user_id, listing, filter_name):
        self.sent.append((user_id, listing.key, filter_name))
        return self.result


async def add_user(session_factory, user_id, **filter_kw):
    async with session_factory() as s:
        await repo.upsert_user(s, user_id, f"u{user_id}", "U")
        f = await repo.create_filter(s, user_id, 10)
        for k, v in filter_kw.items():
            setattr(f, k, v)
        await s.commit()
        return f.id


@pytest.fixture
def source():
    return FakeSource()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def monitor(source, session_factory, notifier):
    return Monitor(source, session_factory, notifier, MonitorConfig(poll_interval=0.01))


async def test_no_active_filters_means_no_source_calls(monitor, source):
    assert await monitor.poll_once() == 0
    assert source.fetched == []


async def test_baseline_is_silent_then_new_offer_notifies_once(monitor, source, notifier, session_factory, make_listing):
    await add_user(session_factory, 1, collection="Plush Pepe")
    source.market[PEPE.id] = [make_listing(slug="PlushPepe-1")]

    assert await monitor.poll_once() == 0  # baseline: remembered, not announced
    assert notifier.sent == []
    assert source.fetched == ["Plush Pepe"]  # only the collection the filter asks for

    source.market[PEPE.id].insert(0, make_listing(slug="PlushPepe-2", number=2))
    assert await monitor.poll_once() == 1
    assert [s[1] for s in notifier.sent] == ["PlushPepe-2|1000|XTR"]

    assert await monitor.poll_once() == 0  # same offers again: no duplicates
    assert len(notifier.sent) == 1


async def test_price_change_is_a_new_offer(monitor, source, notifier, session_factory, make_listing):
    await add_user(session_factory, 1)
    source.market[PEPE.id] = [make_listing(price=1000)]
    await monitor.poll_once()
    source.market[PEPE.id] = [make_listing(price=900)]
    await monitor.poll_once()
    assert [s[1] for s in notifier.sent] == ["PlushPepe-1|900|XTR"]


async def test_filters_decide_who_is_notified(monitor, source, notifier, session_factory, make_listing):
    await add_user(session_factory, 1, collection="Plush Pepe", max_price=500)
    await add_user(session_factory, 2, collection="Plush Pepe", min_price=500)
    await monitor.poll_once()  # baseline (empty market)
    source.market[PEPE.id] = [make_listing(price=1000)]
    await monitor.poll_once()
    assert [s[0] for s in notifier.sent] == [2]


async def test_one_notification_per_user_even_with_several_matching_filters(
    monitor, source, notifier, session_factory, make_listing
):
    await add_user(session_factory, 1, collection="Plush Pepe")
    async with session_factory() as s:
        extra = await repo.create_filter(s, 1, 10)
        extra.model = "Classic"
        await s.commit()
    await monitor.poll_once()
    source.market[PEPE.id] = [make_listing()]
    await monitor.poll_once()
    assert len(notifier.sent) == 1


async def test_disabled_notifications_and_paused_filters_are_respected(
    monitor, source, notifier, session_factory, make_listing
):
    await add_user(session_factory, 1)
    fid = await add_user(session_factory, 2)
    await add_user(session_factory, 3)
    async with session_factory() as s:
        await repo.set_notifications(s, 1, False)
        (await repo.get_filter(s, fid, 2)).is_active = False
        await repo.mark_blocked(s, 3)
        await s.commit()
    # all three are excluded -> nothing to watch
    assert await monitor.poll_once() == 0 and notifier.sent == []


async def test_blocked_user_is_marked_and_not_retried(monitor, source, notifier, session_factory, make_listing):
    await add_user(session_factory, 1)
    await monitor.poll_once()
    notifier.result = NotifyResult.BLOCKED
    source.market[PEPE.id] = [make_listing(slug="A-1")]
    await monitor.poll_once()
    async with session_factory() as s:
        assert (await s.get(repo.User, 1)).is_blocked is True
    source.market[PEPE.id].append(make_listing(slug="B-2"))
    await monitor.poll_once()
    assert len(notifier.sent) == 1  # no more attempts for the blocked user


async def test_source_error_skips_collection_but_cycle_survives(monitor, source, notifier, session_factory):
    await add_user(session_factory, 1)
    source.fail_with = SourceError("boom")
    assert await monitor.poll_once() == 0
    assert set(source.fetched) == {"Plush Pepe", "Heart Locket"}  # kept going after the first failure


async def test_rate_limit_aborts_cycle(monitor, source, session_factory):
    await add_user(session_factory, 1)
    source.fail_with = SourceRateLimited(30)
    with pytest.raises(SourceRateLimited):
        await monitor.poll_once()
    assert source.fetched == ["Plush Pepe"]


async def test_search_snapshot_only_returns_fresh_offers(session_factory, make_listing):
    from datetime import timedelta

    from app.db.models import ListingRow, utcnow

    async with session_factory() as s:
        await repo.save_listings(s, [make_listing(slug="Fresh-1"), make_listing(slug="Stale-2")])
        stale = (await s.execute(ListingRow.__table__.select().where(ListingRow.slug == "Stale-2"))).first()
        await s.execute(
            ListingRow.__table__.update().where(ListingRow.id == stale.id).values(last_seen_at=utcnow() - timedelta(hours=2))
        )
        await s.commit()
        fresh = await repo.fresh_listings(s, utcnow() - timedelta(minutes=30))
    assert [item.slug for item in fresh] == ["Fresh-1"]


def test_listing_key_changes_with_price(make_listing):
    a = make_listing()
    assert a.key != dataclasses.replace(a, price=a.price + 1).key
