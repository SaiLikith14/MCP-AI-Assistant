from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base

_settings = get_settings()

engine = create_async_engine(_settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create tables if they don't exist.

    Fine for a single-service app. If the schema starts changing after real data
    exists, switch to Alembic migrations instead of relying on this.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
