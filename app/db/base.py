import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base

log = logging.getLogger(__name__)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def _add_missing_columns(conn: Connection) -> None:
    """Lightweight forward migration: ``create_all`` never alters existing tables, so columns added in
    newer versions are appended here. Only additive, nullable (or defaulted) columns are supported;
    anything else needs a real migration tool (Alembic)."""
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            default = column.server_default
            if not column.nullable and default is None:
                raise RuntimeError(f"cannot auto-migrate {table.name}.{column.name}: NOT NULL without default")
            clause = ""
            if default is not None:
                arg = default.arg
                clause = " DEFAULT " + ("'%s'" % arg.replace("'", "''") if isinstance(arg, str) else str(arg))
            ddl = column.type.compile(dialect=conn.dialect)
            # identifiers come from our own metadata, never from user input
            conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}{clause}'))
            log.warning("Database upgraded: added %s.%s", table.name, column.name)


async def init_db(engine: AsyncEngine) -> None:
    """Create missing tables and add missing columns. MVP shortcut: use Alembic for anything bigger."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
