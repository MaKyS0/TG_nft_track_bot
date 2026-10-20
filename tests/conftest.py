import os

import pytest
import pytest_asyncio

from app.db.base import create_engine, create_session_factory, init_db
from app.db.models import Base
from app.domain import Listing


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """SQLite by default; set TEST_DATABASE_URL=postgresql+asyncpg://... to run the suite on PostgreSQL
    (the database is wiped for every test, so point it at a throwaway one)."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db(engine)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def make_listing():
    def factory(**overrides) -> Listing:
        values = dict(
            slug="PlushPepe-1",
            collection_id=1,
            collection="Plush Pepe",
            number=1,
            price=1000,
            currency="XTR",
            model="Classic",
            model_rarity=10,
            backdrop="Black",
            backdrop_rarity=20,
            symbol="Stars",
            symbol_rarity=30,
            owner_id=7,
            owner_username="seller",
            owner_name="Seller",
        )
        values.update(overrides)
        return Listing(**values)

    return factory
