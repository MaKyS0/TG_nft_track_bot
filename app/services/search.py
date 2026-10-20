from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import utcnow
from app.domain import STARS, Listing
from app.matching import matches


@dataclass(frozen=True, slots=True)
class SearchResult:
    has_filters: bool
    listings: list[Listing]  # cheapest first (Stars before TON)


async def find_offers(session: AsyncSession, user_id: int, fresh_minutes: int) -> SearchResult:
    """Recently seen offers that satisfy at least one of the user's active filters."""
    filters = await repo.list_filters(session, user_id, only_active=True)
    if not filters:
        return SearchResult(False, [])
    fresh = await repo.fresh_listings(session, utcnow() - timedelta(minutes=fresh_minutes))
    found = [item for item in fresh if any(matches(item, f) for f in filters)]
    found.sort(key=lambda item: (item.currency != STARS, item.price))
    return SearchResult(True, found)
