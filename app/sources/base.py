from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain import Collection, Listing


@dataclass(frozen=True, slots=True)
class FetchResult:
    listings: list[Listing]
    markets_ok: frozenset[str]  # markets that answered successfully (an empty answer still counts)


class GiftSource(ABC):
    """Where marketplace data comes from. Implementations must raise ``SourceError`` /
    ``SourceRateLimited`` (never leak library-specific exceptions) so the monitor can react uniformly."""

    name: str = "base"
    market: str = "telegram"  # value of Listing.market produced by this source
    follows_catalog: bool = False  # True: has no catalogue of its own, polls whatever collections others list

    async def start(self) -> None:  # noqa: B027 - optional hook
        """Open connections. Called once before polling."""

    async def stop(self) -> None:  # noqa: B027 - optional hook
        """Release resources. Called once on shutdown."""

    @abstractmethod
    async def list_collections(self) -> list[Collection]:
        """Gift collections that currently have offers (or may have)."""

    @abstractmethod
    async def fetch_listings(self, collection: Collection, limit: int) -> list[Listing]:
        """Newest offers of one collection (at most ``limit``)."""

    async def fetch(self, collection: Collection, limit: int) -> FetchResult:
        """Like ``fetch_listings`` but also reports which markets answered (see CompositeSource)."""
        return FetchResult(await self.fetch_listings(collection, limit), frozenset({self.market}))
