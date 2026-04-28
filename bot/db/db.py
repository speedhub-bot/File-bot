from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from bot.config import settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # NULL once the user has either been approved or denied. Set to "now"
    # when the user taps the "Request VIP access" button on /start.
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    daily_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    daily_reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    total_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


_engine = create_async_engine(settings.database_url, future=True, echo=False)
_SessionMaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight in-place migrations for SQLite users upgrading from v2.
        # SQLAlchemy's create_all only creates *new* tables; columns added to
        # existing models won't appear unless we ALTER TABLE ourselves.
        await _add_column_if_missing(conn, "users", "is_vip",
                                     "BOOLEAN NOT NULL DEFAULT 0")
        await _add_column_if_missing(conn, "users", "requested_at", "DATETIME")


async def _add_column_if_missing(conn, table: str, column: str, decl: str) -> None:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
        if any(r[1] == column for r in rows):
            return
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))
    else:
        try:
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {decl}")
            )
        except Exception:  # noqa: BLE001
            # Best-effort; if it fails the column probably already exists.
            pass


async def session() -> AsyncIterator[AsyncSession]:
    async with _SessionMaker() as s:
        yield s
