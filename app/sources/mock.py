import random

from app.domain import STARS, Collection, Listing
from app.sources.base import GiftSource

_COLLECTIONS = ["Plush Pepe", "Heart Locket", "Durov's Cap", "Precious Peach", "Astral Shard"]
_MODELS = ["Classic", "Neon", "Royal", "Frost", "Ember", "Jade"]
# name -> (center, edge) colours, mimicking how real backdrops are tinted
_BACKDROPS = {
    "Black": (0x2B2B2F, 0x0B0B0D),
    "Midnight Blue": (0x2F4B8F, 0x101C3D),
    "Satin Gold": (0xE3BE62, 0x9A7421),
    "Emerald": (0x2E9E73, 0x0F4B36),
    "Cappuccino": (0xB98C6E, 0x6B4A36),
    "Burgundy": (0xA63D52, 0x4A1220),
}
_SYMBOLS = ["Stars", "Hearts", "Crowns", "Flowers", "Skulls", "Waves"]


class MockGiftSource(GiftSource):
    """Deterministic fake market for development and demos: every poll may add a new offer.

    NOT real data. Use ``GIFT_SOURCE=telethon`` for the real marketplace.
    """

    name = "mock"

    def __init__(self, seed: int | None = None, new_listing_probability: float = 0.6) -> None:
        # Demo data only, nothing security related depends on this generator.
        self._rng = random.Random(seed)  # nosec B311
        self._probability = new_listing_probability
        self._collections = [Collection(id=i + 1, title=t, resale_count=3) for i, t in enumerate(_COLLECTIONS)]
        self._market: dict[int, list[Listing]] = {c.id: [] for c in self._collections}
        self._counter = 0
        for c in self._collections:
            for _ in range(3):
                self._market[c.id].append(self._make(c))

    def _make(self, collection: Collection) -> Listing:
        self._counter += 1
        number = self._rng.randint(1, 50_000)
        slug = f"{collection.title.replace(' ', '').replace(chr(39), '')}-{number}"
        backdrop = self._rng.choice(list(_BACKDROPS))
        center, edge = _BACKDROPS[backdrop]
        return Listing(
            slug=slug,
            collection_id=collection.id,
            collection=collection.title,
            number=number,
            price=self._rng.choice([50, 120, 300, 800, 1500, 4000, 12000]),
            currency=STARS,
            model=self._rng.choice(_MODELS),
            model_rarity=self._rng.choice([5, 10, 20, 30, 50]),
            backdrop=backdrop,
            backdrop_rarity=self._rng.choice([5, 10, 20, 30, 50]),
            backdrop_center=center,
            backdrop_edge=edge,
            symbol=self._rng.choice(_SYMBOLS),
            symbol_rarity=self._rng.choice([5, 10, 20, 30, 50]),
            owner_id=1000 + self._counter,
            owner_username=f"demo_owner_{self._counter}" if self._counter % 2 else None,
            owner_name=f"Demo Owner {self._counter}",
        )

    async def list_collections(self) -> list[Collection]:
        return list(self._collections)

    async def fetch_listings(self, collection: Collection, limit: int) -> list[Listing]:
        market = self._market[collection.id]
        if self._rng.random() < self._probability:
            market.insert(0, self._make(collection))
            del market[limit:]
        return list(market[:limit])
