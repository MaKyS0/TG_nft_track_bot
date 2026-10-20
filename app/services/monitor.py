import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import repo
from app.domain import Collection, Listing, SourceError, SourceRateLimited
from app.matching import matches, normalize
from app.services.notifier import Notifier, NotifyResult
from app.sources.base import GiftSource

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    poll_interval: float = 60
    listings_per_collection: int = 50
    catalog_ttl: float = 900


class Monitor:
    """Polls the gift source, remembers offers and notifies users whose filters match new ones.

    Guarantees:
      * the first time a collection is polled its offers are only memorised (no flood of "new" offers);
      * each (user, offer) pair is notified at most once (unique row in ``notifications``);
      * a user with several matching filters gets one notification per offer.
    Delivery is at-most-once: the claim row is committed before sending.
    """

    def __init__(
        self,
        source: GiftSource,
        session_factory: async_sessionmaker[AsyncSession],
        notifier: Notifier,
        config: MonitorConfig | None = None,
    ) -> None:
        self._source = source
        self._session_factory = session_factory
        self._notifier = notifier
        self._config = config or MonitorConfig()
        self._catalog: list[Collection] = []
        self._catalog_at = 0.0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("Monitor started (source=%s, interval=%ss)", self._source.name, self._config.poll_interval)
        while not self._stop.is_set():
            delay = self._config.poll_interval
            try:
                await self.poll_once()
            except SourceRateLimited as e:
                delay = max(delay, e.retry_after)
                log.warning("Source rate limited, pausing polling for %.0fs", delay)
            except Exception:
                log.exception("Polling cycle failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        log.info("Monitor stopped")

    async def _collections(self) -> list[Collection]:
        if not self._catalog or time.monotonic() - self._catalog_at > self._config.catalog_ttl:
            self._catalog = await self._source.list_collections()
            self._catalog_at = time.monotonic()
            log.info("Catalog refreshed: %d collections with offers", len(self._catalog))
        return self._catalog

    async def poll_once(self) -> int:
        """One polling cycle. Returns the number of notifications sent."""
        async with self._session_factory() as session:
            filters = list(await repo.active_filters_for_delivery(session))
        if not filters:
            log.debug("No active filters, skipping poll")
            return 0

        wanted = {normalize(f.collection) for f in filters}
        poll_all = "" in wanted
        targets = [c for c in await self._collections() if poll_all or normalize(c.title) in wanted]

        sent = 0
        for collection in targets:
            try:
                listings = await self._source.fetch_listings(collection, self._config.listings_per_collection)
            except SourceRateLimited:
                raise
            except SourceError as e:
                log.warning("Skipping %s this cycle: %s", collection.title, e)
                continue
            sent += await self._process(collection, listings, filters)
        return sent

    async def _process(self, collection: Collection, listings: list[Listing], filters: list) -> int:
        async with self._session_factory() as session:
            seeded = await repo.is_collection_seeded(session, collection.id)
            new = await repo.save_listings(session, listings)
            await repo.mark_collection_polled(session, collection.id)
            await session.commit()

        if not seeded:
            log.info("Baseline for %s: %d offers memorised, no notifications", collection.title, len(listings))
            return 0
        if new:
            log.info("%s: %d new offers", collection.title, len(new))

        sent = 0
        for listing in new:
            # one notification per user per offer, attributed to the first matching filter
            by_user: dict[int, object] = {}
            for f in filters:
                if f.user_id not in by_user and matches(listing, f):
                    by_user[f.user_id] = f
            for user_id, f in by_user.items():
                if await self._deliver(user_id, f, listing):
                    sent += 1
        return sent

    async def _deliver(self, user_id: int, f, listing: Listing) -> bool:
        async with self._session_factory() as session:
            claimed = await repo.record_notification(session, user_id, f.id, listing.key)
            await session.commit()
        if not claimed:
            return False

        result = await self._notifier.notify(user_id, listing, f.name)
        if result is NotifyResult.BLOCKED:
            async with self._session_factory() as session:
                await repo.mark_blocked(session, user_id)
                await session.commit()
            log.info("User %s unreachable, notifications paused until they /start again", user_id)
        elif result is NotifyResult.FAILED:
            log.error("Notification for %s to user %s was not delivered", listing.key, user_id)
        return result is NotifyResult.SENT
