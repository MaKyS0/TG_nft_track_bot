from typing import Protocol

from app.domain import STARS, Listing


class FilterLike(Protocol):
    collection: str | None
    model: str | None
    backdrop: str | None
    symbol: str | None
    min_price: int | None
    max_price: int | None
    max_rarity: int | None


def normalize(value: str | None) -> str:
    """Case/apostrophe/whitespace-insensitive form used to compare names."""
    if not value:
        return ""
    value = value.replace("’", "'").replace("`", "'").replace("ʼ", "'")
    return " ".join(value.split()).casefold()


def _name_ok(wanted: str | None, actual: str | None) -> bool:
    return not wanted or normalize(wanted) == normalize(actual)


def matches(listing: Listing, f: FilterLike) -> bool:
    """True if ``listing`` satisfies every criterion set in ``f`` (unset = any).

    Price bounds are in Stars; offers priced in TON never match a filter with price bounds.
    ``max_rarity`` (per-mille): the rarest of model/backdrop/symbol must be at most this value.
    """
    if not _name_ok(f.collection, listing.collection):
        return False
    if not _name_ok(f.model, listing.model):
        return False
    if not _name_ok(f.backdrop, listing.backdrop):
        return False
    if not _name_ok(f.symbol, listing.symbol):
        return False

    if f.min_price is not None or f.max_price is not None:
        if listing.currency != STARS:
            return False
        if f.min_price is not None and listing.price < f.min_price:
            return False
        if f.max_price is not None and listing.price > f.max_price:
            return False

    if f.max_rarity is not None:
        rarities = [
            r for r in (listing.model_rarity, listing.backdrop_rarity, listing.symbol_rarity) if r is not None
        ]
        if not rarities or min(rarities) > f.max_rarity:
            return False
    return True
