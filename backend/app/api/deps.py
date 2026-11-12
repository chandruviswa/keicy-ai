import uuid

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import LLMGateway, get_gateway
from app.core.security import TokenError, decode_token
from app.db.session import get_session
from app.models import ProcurementRequest, User
from app.models.enums import UserRole
from app.payments.paypal import PayPalClient, get_client

bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login")


def get_llm() -> LLMGateway:
    return get_gateway()


def get_paypal() -> PayPalClient:
    return get_client()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


async def user_from_token(db: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    try:
        user_id = decode_token(token)
    except TokenError:
        return None
    return await db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_session),
) -> User:
    if creds is None:
        raise _unauthorized("Sign in required")
    try:
        user_id = decode_token(creds.credentials)
    except TokenError as e:
        raise _unauthorized(str(e)) from e
    user = await db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if user is None:
        raise _unauthorized("Account not found or disabled")
    request.state.user_id = str(user.id)  # picked up by the request log line
    return user


def require_role(*roles: UserRole):
    async def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return user
    return dep


def can_view(user: User, pr: ProcurementRequest) -> bool:
    return pr.requester_id == user.id or user.role in (UserRole.APPROVER, UserRole.ADMIN)


async def get_visible_request(db: AsyncSession, request_id: uuid.UUID, user: User) -> ProcurementRequest:
    pr = await db.get(ProcurementRequest, request_id)
    # 404 (not 403) for other users' requests so IDs can't be probed.
    if not pr or not can_view(user, pr):
        raise HTTPException(404, "Procurement request not found")
    return pr
