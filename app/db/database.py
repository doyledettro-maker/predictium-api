"""
SQLAlchemy async database setup.
Uses asyncpg driver for PostgreSQL.
"""

from typing import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()


def _engine_options(database_url: str) -> tuple[str, dict]:
    """Translate URL SSL hints into asyncpg connect args.

    RDS requires encrypted connections, but SQLAlchemy's asyncpg dialect passes
    URL query params through as connect() kwargs. asyncpg accepts `ssl`, not
    `sslmode`, so normalize that here and keep localhost development unchanged.
    """
    parsed = urlsplit(database_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    wants_ssl = query.pop("ssl", "").lower() in {"1", "true", "require"} or query.pop(
        "sslmode", ""
    ).lower() in {"require", "verify-ca", "verify-full"}
    clean_url = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
    remote_host = parsed.hostname not in {"localhost", "127.0.0.1", None}
    return clean_url, ({"ssl": True} if wants_ssl or remote_host else {})


database_url, connect_args = _engine_options(settings.database_url)

# Create async engine
engine = create_async_engine(
    database_url,
    echo=not settings.is_production,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    connect_args=connect_args,
)

# Create async session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that provides an async database session.
    Yields a session and ensures it's closed after use.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Initialize database tables.
    Note: In production, use Alembic migrations instead.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
