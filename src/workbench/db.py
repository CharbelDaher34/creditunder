from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workbench.config import settings

_read_engine = create_async_engine(settings.effective_read_url, echo=False, pool_pre_ping=True)

AsyncReadSessionLocal = async_sessionmaker(
    _read_engine, expire_on_commit=False, class_=AsyncSession
)


async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    """Read-only session backed by the Postgres read replica (or primary when replica is not configured)."""
    async with AsyncReadSessionLocal() as session:
        yield session
