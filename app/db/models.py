from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain import Listing

# BigInteger primary keys do not autoincrement on SQLite (used in tests); fall back to Integer there.
BigPK = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything we store is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # Telegram user id
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(256))
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserFilter(Base):
    __tablename__ = "user_filters"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    collection: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    backdrop: Mapped[str | None] = mapped_column(String(128))
    symbol: Mapped[str | None] = mapped_column(String(128))
    min_price: Mapped[int | None] = mapped_column(BigInteger)  # Stars
    max_price: Mapped[int | None] = mapped_column(BigInteger)  # Stars
    min_ton: Mapped[int | None] = mapped_column(BigInteger)  # nanoTON
    max_ton: Mapped[int | None] = mapped_column(BigInteger)  # nanoTON
    max_rarity: Mapped[int | None] = mapped_column(Integer)  # per-mille
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ListingRow(Base):
    """Offers seen by the monitor. Used for dedup of the snapshot and for the "find gifts" search."""

    __tablename__ = "listings"
    __table_args__ = (Index("ix_listings_last_seen", "last_seen_at"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    listing_key: Mapped[str] = mapped_column(String(200), unique=True)
    slug: Mapped[str] = mapped_column(String(128))
    collection_id: Mapped[int] = mapped_column(BigInteger)
    collection: Mapped[str] = mapped_column(String(128))
    number: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(8))
    market: Mapped[str] = mapped_column(String(16), default="telegram", server_default="telegram")
    external_id: Mapped[str | None] = mapped_column(String(64))
    buy_url: Mapped[str | None] = mapped_column(String(300))
    # False for the inventory memorised during the first pass; True for offers discovered afterwards.
    # Rows that existed before this column was added default to False, i.e. they are never shown as new.
    is_new: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    model: Mapped[str | None] = mapped_column(String(128))
    model_rarity: Mapped[int | None] = mapped_column(Integer)
    backdrop: Mapped[str | None] = mapped_column(String(128))
    backdrop_rarity: Mapped[int | None] = mapped_column(Integer)
    backdrop_center: Mapped[int | None] = mapped_column(Integer)
    backdrop_edge: Mapped[int | None] = mapped_column(Integer)
    symbol: Mapped[str | None] = mapped_column(String(128))
    symbol_rarity: Mapped[int | None] = mapped_column(Integer)
    owner_id: Mapped[int | None] = mapped_column(BigInteger)
    owner_username: Mapped[str | None] = mapped_column(String(64))
    owner_name: Mapped[str | None] = mapped_column(String(256))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def to_domain(self) -> Listing:
        return Listing(
            slug=self.slug,
            collection_id=self.collection_id,
            collection=self.collection,
            number=self.number,
            price=self.price,
            currency=self.currency,
            market=self.market,
            external_id=self.external_id,
            buy_url=self.buy_url,
            seen_at=as_utc(self.first_seen_at),
            is_new=bool(self.is_new),
            model=self.model,
            model_rarity=self.model_rarity,
            backdrop=self.backdrop,
            backdrop_rarity=self.backdrop_rarity,
            backdrop_center=self.backdrop_center,
            backdrop_edge=self.backdrop_edge,
            symbol=self.symbol,
            symbol_rarity=self.symbol_rarity,
            owner_id=self.owner_id,
            owner_username=self.owner_username,
            owner_name=self.owner_name,
        )


class Notification(Base):
    """One row per (user, offer). The unique constraint is what prevents duplicate notifications."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "listing_key", name="uq_notification_user_listing"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filter_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user_filters.id", ondelete="SET NULL"))
    listing_key: Mapped[str] = mapped_column(String(200))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PollState(Base):
    __tablename__ = "poll_state"

    collection_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_polled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
