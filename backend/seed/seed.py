"""Idempotent seed of sign-in accounts: inserts missing users by email, never duplicates or overwrites."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import User
from seed.data import DEMO_PASSWORD, USERS


async def seed_all(db: AsyncSession) -> dict[str, int]:
    added = {"users": 0}
    existing = {u.email: u for u in await db.scalars(select(User))}
    for email, name, role in USERS:
        if email not in existing:
            db.add(User(email=email, full_name=name, role=role, password_hash=hash_password(DEMO_PASSWORD)))
            added["users"] += 1
        elif existing[email].password_hash is None:  # users seeded before auth existed
            existing[email].password_hash = hash_password(DEMO_PASSWORD)
    await db.commit()
    return added
