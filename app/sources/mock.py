import random

from app.domain import STARS, Collection, Listing
from app.sources.base import GiftSource

_COLLECTIONS = ["Plush Pepe", "Heart Locket", "Durov's Cap", "Precious Peach", "Astral Shard"]
_MODELS = ["Classic", "Neon", "Royal", "Frost", "Ember", "Jade"]
_BACKDROPS = ["Black", "Midnight Blue", "Satin Gold", "Emerald", "Cappuccino", "Burgundy"]
_SYMBOLS = ["Stars", "Hearts", "Crowns", "Flowers", "Skulls", "Waves"]


class MockGiftSource(GiftSource):
    """Deterministic fake market for development and demos: every poll may add a new offer.

    NOT real data. Use ``GIFT_SOURCE=telethon`` for the real marketplace.
    """

    name = "mock"

    def __init__(self, seed: int | None = None, new_listing_probability: float = 0.6) -> None:
        self._rng = random.Random(seed)
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
        return Listing(
            slug=slug,
            collection_id=collection.id,
            collection=collection.title,
            number=number,
            price=self._rng.choice([50, 120, 300, 800, 1500, 4000, 12000]),
            currency=STARS,
            model=self._rng.choice(_MODELS),
            model_rarity=self._rng.choice([5, 10, 20, 30, 50]),
            backdrop=self._rng.choice(_BACKDROPS),
            backdrop_rarity=self._rng.choice([5, 10, 20, 30, 50]),
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
