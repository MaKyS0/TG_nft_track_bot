from typing import Protocol

from app.domain import STARS, TON, Listing


class FilterLike(Protocol):
    collection: str | None
    model: str | None
    backdrop: str | None
    symbol: str | None
    min_price: int | None
    max_price: int | None
    min_ton: int | None
    max_ton: int | None
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

    Price bounds are in Stars (``*_price``) and TON (``*_ton``, nanoTON); see the currency rule below.
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

    if any(v is not None for v in (f.min_price, f.max_price, f.min_ton, f.max_ton)):
        # Prices in different currencies are not comparable: a listing is judged only against the bounds
        # given in its own currency, and never matches a filter that set bounds only for the other one.
        low, high = {STARS: (f.min_price, f.max_price), TON: (f.min_ton, f.max_ton)}.get(listing.currency, (None, None))
        if low is None and high is None:
            return False
        if low is not None and listing.price < low:
            return False
        if high is not None and listing.price > high:
            return False

    if f.max_rarity is not None:
        rarities = [
            r for r in (listing.model_rarity, listing.backdrop_rarity, listing.symbol_rarity) if r is not None
        ]
        if not rarities or min(rarities) > f.max_rarity:
            return False
    return True
