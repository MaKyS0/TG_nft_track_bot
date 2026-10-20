import logging
import time

from app.domain import Collection, Listing, SourceError, SourceRateLimited
from app.matching import normalize
from app.sources.base import FetchResult, GiftSource

log = logging.getLogger(__name__)


class CompositeSource(GiftSource):
    """Several markets behind one interface.

    Collections are merged by (normalised) title. One failing or rate-limited source never takes the
    others down: it is put on a cool-down and the cycle continues with the rest, and ``markets_ok``
    tells the monitor which markets actually answered (so a silent market is never "baselined" empty).
    """

    name = "multi"

    def __init__(self, sources: list[GiftSource]) -> None:
        if not sources:
            raise ValueError("at least one source is required")
        self._sources = sources
        self._by_title: dict[str, dict[str, Collection]] = {}  # normalised title -> {source name: collection}
        self._cooldown_until: dict[str, float] = {}

    async def start(self) -> None:
        for source in self._sources:
            await source.start()

    async def stop(self) -> None:
        for source in self._sources:
            await source.stop()

    def _available(self, source: GiftSource) -> bool:
        return time.monotonic() >= self._cooldown_until.get(source.name, 0.0)

    def _penalise(self, source: GiftSource, error: SourceError) -> None:
        if isinstance(error, SourceRateLimited):
            wait = error.retry_after
        else:
            wait = 60.0
        self._cooldown_until[source.name] = time.monotonic() + wait
        log.warning("%s source paused for %.0fs: %s", source.name, wait, error)

    async def list_collections(self) -> list[Collection]:
        merged: dict[str, Collection] = {}
        by_title: dict[str, dict[str, Collection]] = {}
        failures: list[SourceError] = []
        answered = False
        for source in self._sources:
            if not self._available(source):
                continue
            try:
                collections = await source.list_collections()
            except SourceError as e:
                self._penalise(source, e)
                failures.append(e)
                continue
            answered = True
            for collection in collections:
                key = normalize(collection.title)
                merged.setdefault(key, collection)  # the first source's id wins (Telegram, when enabled)
                by_title.setdefault(key, {})[source.name] = collection
        if not answered and failures:
            raise failures[0]
        self._by_title = by_title
        return list(merged.values())

    async def fetch_listings(self, collection: Collection, limit: int) -> list[Listing]:
        return (await self.fetch(collection, limit)).listings

    async def fetch(self, collection: Collection, limit: int) -> FetchResult:
        per_source = self._by_title.get(normalize(collection.title), {})
        listings: list[Listing] = []
        markets: set[str] = set()
        failures: list[SourceError] = []
        for source in self._sources:
            own = per_source.get(source.name)
            if own is None and source.follows_catalog:
                own = Collection(id=collection.id, title=collection.title)
            if own is None or not self._available(source):
                continue
            try:
                result = await source.fetch(own, limit)
            except SourceError as e:
                self._penalise(source, e)
                failures.append(e)
                continue
            listings.extend(result.listings)
            markets |= result.markets_ok
        if not markets and failures:
            raise failures[0]
        return FetchResult(listings, frozenset(markets))
