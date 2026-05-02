from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from creditunder.config import settings

# Primary write engine — used by the pipeline.
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Read-only engine — used by the workbench API. Points at a read replica when
# `DATABASE_READ_URL` is set; otherwise falls back to the primary so dev/CI
# does not need a second Postgres instance. Sessions opened against this
# factory still allow writes at the SQL level (Postgres enforces replica
# read-only behaviour, not SQLAlchemy) — application code must not write here.
read_engine = create_async_engine(
    settings.effective_database_read_url, echo=False, pool_pre_ping=True
)
AsyncReadSessionLocal = async_sessionmaker(
    read_engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncSession:
    """Primary (write) session. Use only from the pipeline."""
    async with AsyncSessionLocal() as session:
        yield session


async def get_read_session() -> AsyncSession:
    """Read-only session for the workbench. Routes to the read replica if configured."""
    async with AsyncReadSessionLocal() as session:
        yield session
