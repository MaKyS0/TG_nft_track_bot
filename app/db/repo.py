"""All database access. Functions take an AsyncSession and never commit (callers own the transaction)."""

import hashlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListingRow, Notification, PollState, User, UserFilter, as_utc, utcnow
from app.domain import UNPRICED, Listing


def _insert(session: AsyncSession, model):
    """Dialect-specific INSERT supporting ON CONFLICT (PostgreSQL in production, SQLite in tests)."""
    dialect = session.bind.dialect.name  # type: ignore[union-attr]
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover
        raise RuntimeError(f"unsupported database dialect: {dialect}")
    return insert(model)


# ── users ──────────────────────────────────────────────────────────────────


async def upsert_user(session: AsyncSession, user_id: int, username: str | None, first_name: str | None) -> None:
    """Create or refresh a user. A single atomic statement: the Mini App fires several requests at once
    on first open, and a read-then-insert would make them race on the primary key."""
    now = utcnow()
    stmt = (
        _insert(session, User)
        .values(
            id=user_id,
            username=username,
            first_name=first_name,
            notifications_enabled=True,
            is_blocked=False,
            created_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            # they are talking to us, so they have not blocked the bot
            set_={"username": username, "first_name": first_name, "last_seen_at": now, "is_blocked": False},
        )
    )
    await session.execute(stmt)


async def set_notifications(session: AsyncSession, user_id: int, enabled: bool) -> None:
    await session.execute(update(User).where(User.id == user_id).values(notifications_enabled=enabled))


async def mark_blocked(session: AsyncSession, user_id: int) -> None:
    await session.execute(update(User).where(User.id == user_id).values(is_blocked=True))


# ── filters ────────────────────────────────────────────────────────────────


async def list_filters(session: AsyncSession, user_id: int, *, only_active: bool = False) -> Sequence[UserFilter]:
    stmt = select(UserFilter).where(UserFilter.user_id == user_id).order_by(UserFilter.id)
    if only_active:
        stmt = stmt.where(UserFilter.is_active.is_(True))
    return (await session.execute(stmt)).scalars().all()


async def get_filter(session: AsyncSession, filter_id: int, user_id: int) -> UserFilter | None:
    """Fetch a filter only if it belongs to ``user_id`` (callback data is user-controlled)."""
    f = await session.get(UserFilter, filter_id)
    return f if f is not None and f.user_id == user_id else None


async def create_filter(session: AsyncSession, user_id: int, max_filters: int) -> UserFilter | None:
    count = await session.scalar(select(func.count()).select_from(UserFilter).where(UserFilter.user_id == user_id))
    if (count or 0) >= max_filters:
        return None
    f = UserFilter(user_id=user_id, name=f"Фильтр {(count or 0) + 1}")
    session.add(f)
    await session.flush()
    return f


async def delete_filter(session: AsyncSession, filter_id: int, user_id: int) -> None:
    await session.execute(delete(UserFilter).where(UserFilter.id == filter_id, UserFilter.user_id == user_id))


async def active_filters_for_delivery(session: AsyncSession) -> Sequence[UserFilter]:
    """Active filters of users who want notifications and have not blocked the bot."""
    stmt = (
        select(UserFilter)
        .join(User, User.id == UserFilter.user_id)
        .where(
            UserFilter.is_active.is_(True),
            User.notifications_enabled.is_(True),
            User.is_blocked.is_(False),
        )
    )
    return (await session.execute(stmt)).scalars().all()


# ── listings ───────────────────────────────────────────────────────────────


async def save_listings(
    session: AsyncSession, listings: Sequence[Listing], new_markets: Collection[str] = ()
) -> list[Listing]:
    """Store offers; return the ones never seen before. Already known offers just get ``last_seen_at`` bumped.

    ``new_markets``: markets whose first-seen offers count as *new* (their first pass is already done).
    Offers of other markets are stored as plain inventory (``is_new = False``)."""
    now = utcnow()
    new: list[Listing] = []
    known_keys: list[str] = []
    for item in listings:
        stmt = (
            _insert(session, ListingRow)
            .values(
                listing_key=item.key,
                slug=item.slug,
                collection_id=item.collection_id,
                collection=item.collection,
                number=item.number,
                price=item.price,
                currency=item.currency,
                market=item.market,
                external_id=item.external_id,
                buy_url=item.buy_url,
                is_new=item.market in new_markets,
                model=item.model,
                model_rarity=item.model_rarity,
                backdrop=item.backdrop,
                backdrop_rarity=item.backdrop_rarity,
                backdrop_center=item.backdrop_center,
                backdrop_edge=item.backdrop_edge,
                symbol=item.symbol,
                symbol_rarity=item.symbol_rarity,
                owner_id=item.owner_id,
                owner_username=item.owner_username,
                owner_name=item.owner_name,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_nothing(index_elements=["listing_key"])
            .returning(ListingRow.id)
        )
        inserted = (await session.execute(stmt)).first()
        if inserted is not None:
            new.append(item)
        else:
            known_keys.append(item.key)
    if known_keys:
        await session.execute(update(ListingRow).where(ListingRow.listing_key.in_(known_keys)).values(last_seen_at=now))
    return new


async def fresh_listings(session: AsyncSession, since: datetime, limit: int = 3000) -> list[Listing]:
    stmt = select(ListingRow).where(ListingRow.last_seen_at >= since).order_by(ListingRow.currency == UNPRICED, ListingRow.price).limit(limit)
    return [row.to_domain() for row in (await session.execute(stmt)).scalars()]


# ── notifications ──────────────────────────────────────────────────────────


async def record_notification(session: AsyncSession, user_id: int, filter_id: int, listing_key: str) -> bool:
    """Atomically claim the right to notify ``user_id`` about ``listing_key``. False = already notified."""
    stmt = (
        _insert(session, Notification)
        .values(user_id=user_id, filter_id=filter_id, listing_key=listing_key, sent_at=utcnow())
        .on_conflict_do_nothing(index_elements=["user_id", "listing_key"])
        .returning(Notification.id)
    )
    return (await session.execute(stmt)).first() is not None


# ── poll state ─────────────────────────────────────────────────────────────


def poll_key(collection_id: int, market: str) -> int:
    """PollState key. Telegram keeps the plain collection id (backwards compatible); other markets get a
    stable negative number, so each (collection, market) pair has its own "first pass" baseline."""
    if market == "telegram":
        return collection_id
    digest = hashlib.sha1(f"{market}:{collection_id}".encode(), usedforsecurity=False).hexdigest()
    return -int(digest[:12], 16)



async def is_collection_seeded(session: AsyncSession, collection_id: int) -> bool:
    return await session.get(PollState, collection_id) is not None


async def mark_collection_polled(session: AsyncSession, collection_id: int) -> None:
    state = await session.get(PollState, collection_id)
    if state is None:
        session.add(PollState(collection_id=collection_id, last_polled_at=utcnow()))
    else:
        state.last_polled_at = utcnow()
    await session.flush()


# ── statistics ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UserStats:
    filters_total: int
    filters_active: int
    notifications_received: int


@dataclass(frozen=True, slots=True)
class GlobalStats:
    users: int
    active_filters: int
    listings_known: int
    notifications_sent: int
    last_poll_at: datetime | None


async def user_stats(session: AsyncSession, user_id: int) -> UserStats:
    total = await session.scalar(select(func.count()).select_from(UserFilter).where(UserFilter.user_id == user_id))
    active = await session.scalar(
        select(func.count())
        .select_from(UserFilter)
        .where(UserFilter.user_id == user_id, UserFilter.is_active.is_(True))
    )
    received = await session.scalar(
        select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
    )
    return UserStats(total or 0, active or 0, received or 0)


async def global_stats(session: AsyncSession) -> GlobalStats:
    async def count(model, *where) -> int:
        return (await session.scalar(select(func.count()).select_from(model).where(*where))) or 0

    last_poll = await session.scalar(select(func.max(PollState.last_polled_at)))
    return GlobalStats(
        users=await count(User),
        active_filters=await count(UserFilter, UserFilter.is_active.is_(True)),
        listings_known=await count(ListingRow),
        notifications_sent=await count(Notification),
        last_poll_at=as_utc(last_poll),
    )
