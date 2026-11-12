"""Sign-in. Email + password → short-lived JWT access token (sent as `Authorization: Bearer …`)."""

import time
from collections import defaultdict, deque
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas import UserOut
from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.db.session import get_session
from app.models import AuditLog, User
from app.models.enums import ActorType

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILURES, WINDOW_S = 5, 300
_failures: dict[str, deque[float]] = defaultdict(deque)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


def _throttled(key: str) -> bool:
    q = _failures[key]
    now = time.monotonic()
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    return len(q) >= MAX_FAILURES


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_session)):
    email = body.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else '?'}"
    if _throttled(key):
        raise HTTPException(429, "Too many failed sign-in attempts — wait a few minutes and try again")
    user = await db.scalar(select(User).where(func.lower(User.email) == email))
    ok = verify_password(body.password, user.password_hash if user else None) and user is not None and user.is_active
    db.add(AuditLog(actor_type=ActorType.USER if ok else ActorType.SYSTEM, actor_user_id=user.id if ok else None,
                    actor_name=None if ok else "auth", action="auth.login" if ok else "auth.login_failed",
                    entity_type="user", entity_id=user.id if user else None, details={"email": email}))
    await db.commit()
    if not ok:
        _failures[key].append(time.monotonic())
        raise HTTPException(401, "Incorrect email or password")  # same message whether or not the account exists
    _failures.pop(key, None)
    token, exp = create_access_token(user.id, user.role)
    return TokenOut(access_token=token, expires_at=exp, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.get("/demo-accounts")
async def demo_accounts():
    """Seeded accounts for the login page's quick sign-in. Disabled in production."""
    s = get_settings()
    if not s.demo_accounts_enabled or s.app_env == "production":
        return {"enabled": False, "accounts": []}
    from seed.data import DEMO_PASSWORD, USERS

    return {"enabled": True, "password": DEMO_PASSWORD,
            "accounts": [{"email": e, "name": n, "role": r} for e, n, r in USERS]}
