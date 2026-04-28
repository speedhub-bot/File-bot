from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import desc, func, select, update

from bot.db.db import Job, User, _SessionMaker


async def get_or_create_user(
    user_id: int, username: str | None, first_name: str | None
) -> User:
    async with _SessionMaker() as s:
        u = await s.get(User, user_id)
        if u is None:
            u = User(user_id=user_id, username=username, first_name=first_name)
            s.add(u)
            await s.commit()
            await s.refresh(u)
        else:
            changed = False
            if username and u.username != username:
                u.username = username
                changed = True
            if first_name and u.first_name != first_name:
                u.first_name = first_name
                changed = True
            if changed:
                await s.commit()
        return u


async def reset_quota_if_needed(user_id: int) -> User:
    async with _SessionMaker() as s:
        u = await s.get(User, user_id)
        if u is None:
            raise LookupError(f"user {user_id} missing")
        if datetime.utcnow() - u.daily_reset_at >= timedelta(days=1):
            u.daily_used_bytes = 0
            u.daily_reset_at = datetime.utcnow()
            await s.commit()
            await s.refresh(u)
        return u


async def add_quota_used(user_id: int, n: int) -> None:
    async with _SessionMaker() as s:
        await s.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(
                daily_used_bytes=User.daily_used_bytes + n,
                total_bytes=User.total_bytes + n,
                total_jobs=User.total_jobs + 1,
            )
        )
        await s.commit()


async def get_user(user_id: int) -> User | None:
    async with _SessionMaker() as s:
        return await s.get(User, user_id)


async def set_vip(user_id: int, vip: bool) -> bool:
    async with _SessionMaker() as s:
        u = await s.get(User, user_id)
        if u is None:
            return False
        u.is_vip = vip
        await s.commit()
        return True


async def set_banned(user_id: int, banned: bool) -> bool:
    async with _SessionMaker() as s:
        u = await s.get(User, user_id)
        if u is None:
            return False
        u.is_banned = banned
        await s.commit()
        return True


async def list_users(limit: int = 50) -> Sequence[User]:
    async with _SessionMaker() as s:
        res = await s.execute(select(User).order_by(desc(User.total_bytes)).limit(limit))
        return res.scalars().all()


async def all_user_ids() -> list[int]:
    async with _SessionMaker() as s:
        res = await s.execute(select(User.user_id).where(User.is_banned.is_(False)))
        return [r[0] for r in res.all()]


async def record_access_request(user_id: int) -> bool:
    """Mark `requested_at = now` so the user shows up under pending. No-op if
    the user is already VIP/banned (those states already encode a final
    decision). Returns whether the row was actually changed."""
    async with _SessionMaker() as s:
        u = await s.get(User, user_id)
        if u is None or u.is_vip or u.is_banned or u.requested_at is not None:
            return False
        u.requested_at = datetime.utcnow()
        await s.commit()
        return True


async def clear_request(user_id: int) -> None:
    """Drop the pending marker (called after approve/ban/deny)."""
    async with _SessionMaker() as s:
        await s.execute(
            update(User).where(User.user_id == user_id).values(requested_at=None)
        )
        await s.commit()


async def list_pending_requests(limit: int = 50) -> Sequence[User]:
    """Users who tapped 'Request access' but haven't been approved or banned."""
    async with _SessionMaker() as s:
        res = await s.execute(
            select(User)
            .where(
                User.requested_at.is_not(None),
                User.is_vip.is_(False),
                User.is_banned.is_(False),
            )
            .order_by(User.requested_at.asc())
            .limit(limit)
        )
        return res.scalars().all()


async def list_approved(limit: int = 50) -> Sequence[User]:
    async with _SessionMaker() as s:
        res = await s.execute(
            select(User).where(User.is_vip.is_(True)).order_by(desc(User.user_id)).limit(limit)
        )
        return res.scalars().all()


async def list_banned(limit: int = 50) -> Sequence[User]:
    async with _SessionMaker() as s:
        res = await s.execute(
            select(User).where(User.is_banned.is_(True)).order_by(desc(User.user_id)).limit(limit)
        )
        return res.scalars().all()


async def insert_job(user_id: int, file_name: str, size_bytes: int) -> int:
    async with _SessionMaker() as s:
        j = Job(user_id=user_id, file_name=file_name, size_bytes=size_bytes)
        s.add(j)
        await s.commit()
        await s.refresh(j)
        return j.id


async def update_job(
    job_id: int,
    *,
    status: str | None = None,
    parts: int | None = None,
    mode: str | None = None,
    error: str | None = None,
    finished: bool = False,
) -> None:
    values: dict[str, object] = {}
    if status is not None:
        values["status"] = status
    if parts is not None:
        values["parts"] = parts
    if mode is not None:
        values["mode"] = mode
    if error is not None:
        values["error"] = error
    if finished:
        values["finished_at"] = datetime.utcnow()
    if not values:
        return
    async with _SessionMaker() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(**values))
        await s.commit()


async def list_recent_jobs(limit: int = 20) -> Sequence[Job]:
    async with _SessionMaker() as s:
        res = await s.execute(select(Job).order_by(desc(Job.id)).limit(limit))
        return res.scalars().all()


async def stats() -> dict[str, int]:
    async with _SessionMaker() as s:
        users_total = (await s.execute(select(func.count(User.user_id)))).scalar_one()
        jobs_total = (await s.execute(select(func.count(Job.id)))).scalar_one()
        bytes_total = (await s.execute(select(func.coalesce(func.sum(User.total_bytes), 0)))).scalar_one()
        active = (
            await s.execute(
                select(func.count(Job.id)).where(Job.status.in_(("queued", "running")))
            )
        ).scalar_one()
    return {
        "users": int(users_total or 0),
        "jobs": int(jobs_total or 0),
        "bytes": int(bytes_total or 0),
        "active": int(active or 0),
    }
