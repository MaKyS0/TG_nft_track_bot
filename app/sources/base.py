from abc import ABC, abstractmethod

from app.domain import Collection, Listing


class GiftSource(ABC):
    """Where marketplace data comes from. Implementations must raise ``SourceError`` /
    ``SourceRateLimited`` (never leak library-specific exceptions) so the monitor can react uniformly."""

    name: str = "base"

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
