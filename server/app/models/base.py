# server/app/models/base.py
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession as _AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# ---- SQLAlchemy base / engine / session ----

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data.db")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

AsyncSession = _AsyncSession  # re-exported for typing elsewhere
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=_AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    """Declarative base for ORM models."""
    pass


@asynccontextmanager
async def get_session() -> AsyncSession: # pyright: ignore[reportInvalidTypeForm]
    """
    FastAPI dependency for an AsyncSession.

    Usage:
        async with get_session() as session:
            ...
    Or as a Depends in route/service functions.
    """
    session: AsyncSession = SessionLocal() # pyright: ignore[reportInvalidTypeForm]
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
