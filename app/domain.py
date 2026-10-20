import re
from dataclasses import dataclass

STARS = "XTR"
TON = "TON"
NANO_TON = 1_000_000_000

TELEGRAM = "telegram"
MRKT = "mrkt"
MARKET_TITLES = {TELEGRAM: "Telegram", MRKT: "MRKT"}
# Where to buy, per market (Telegram sells on the gift page itself).
MARKET_URLS = {MRKT: "https://t.me/mrkt/app"}

# Values below come from third parties: build links only from strings that match these patterns.
_SLUG_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
_USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")  # Telegram: 5-32 chars


def is_safe_slug(value: str) -> bool:
    return bool(_SLUG_RE.fullmatch(value))


class SourceError(Exception):
    """A data source failed to return data (network, API error, bad response)."""


class SourceRateLimited(SourceError):
    """The data source asked us to slow down."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Collection:
    id: int
    title: str
    resale_count: int | None = None
    floor_price: int | None = None  # in Stars


@dataclass(frozen=True, slots=True)
class Listing:
    """One upgraded gift offered for resale.

    ``price`` is in Stars when ``currency == "XTR"`` and in nanoTON when ``currency == "TON"``.
    Rarity values are per-mille (e.g. 15 == 1.5%); ``None`` when the source gives no number.
    """

    slug: str
    collection_id: int
    collection: str
    number: int
    price: int
    currency: str = STARS
    model: str | None = None
    model_rarity: int | None = None
    backdrop: str | None = None
    backdrop_rarity: int | None = None
    backdrop_center: int | None = None  # 0xRRGGBB, as given by Telegram; used to tint cards in the mini app
    backdrop_edge: int | None = None
    symbol: str | None = None
    symbol_rarity: int | None = None
    owner_id: int | None = None
    owner_username: str | None = None
    owner_name: str | None = None
    market: str = TELEGRAM

    @property
    def key(self) -> str:
        """Identity of an *offer*: the same gift at a new price (or on another market) is a new offer."""
        base = f"{self.slug}|{self.price}|{self.currency}"
        return base if self.market == TELEGRAM else f"{self.market}:{base}"

    @property
    def market_title(self) -> str:
        return MARKET_TITLES.get(self.market, self.market)

    @property
    def market_url(self) -> str | None:
        """Where the offer can be bought when that is not the Telegram gift page itself."""
        return MARKET_URLS.get(self.market)

    @property
    def url(self) -> str:
        return f"https://t.me/nft/{self.slug}"

    @property
    def owner_url(self) -> str | None:
        if self.owner_username and _USERNAME_RE.fullmatch(self.owner_username):
            return f"https://t.me/{self.owner_username}"
        return None
