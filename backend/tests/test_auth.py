"""Authentication and authorization (Phase 11)."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.api.routes import auth as auth_routes
from app.core.security import create_access_token, decode_token, hash_password, verify_password
from app.models import AuditLog, User
from seed.data import DEMO_PASSWORD, DEMO_USER_EMAIL
from tests.conftest import auth


@pytest.fixture(autouse=True)
def reset_throttle():
    auth_routes._failures.clear()
    yield
    auth_routes._failures.clear()


async def login(c: AsyncClient, email: str, password: str = DEMO_PASSWORD):
    return await c.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_password_hashing():
    h1, h2 = hash_password("s3cret!"), hash_password("s3cret!")
    assert h1 != h2 and h1.startswith("scrypt$")  # salted
    assert verify_password("s3cret!", h1) and not verify_password("wrong", h1)
    assert not verify_password("x", None) and not verify_password("x", "garbage")


async def test_login_and_me(anon_client: AsyncClient, db):
    r = await login(anon_client, "Approver@Keicy.local")  # email is case-insensitive
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["role"] == "approver" and body["token_type"] == "bearer"
    me = await anon_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "approver@keicy.local"
    assert await db.scalar(select(AuditLog.id).where(AuditLog.action == "auth.login", AuditLog.entity_id == uuid.UUID(body["user"]["id"])))


async def test_bad_credentials_look_identical(anon_client: AsyncClient):
    wrong_pw = await login(anon_client, DEMO_USER_EMAIL, "nope")
    unknown = await login(anon_client, "ghost@keicy.local", "nope")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json() == {"detail": "Incorrect email or password"}


async def test_login_throttling(anon_client: AsyncClient):
    for _ in range(5):
        assert (await login(anon_client, DEMO_USER_EMAIL, "bad")).status_code == 401
    assert (await login(anon_client, DEMO_USER_EMAIL)).status_code == 429  # even the right password, until the window passes


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/procurement/requests"), ("POST", "/api/v1/procurement/requests"), ("GET", "/api/v1/vendors"),
    ("GET", "/api/v1/products"), ("GET", "/api/v1/purchase-orders"), ("GET", "/api/v1/dashboard/summary"),
    ("POST", "/api/v1/payments/create-order"), ("POST", "/api/v1/payments/capture"), ("GET", "/api/v1/payments"),
    ("GET", "/api/v1/payments/config"), ("GET", "/api/v1/audit/logs"), ("GET", "/api/v1/intelligence/summary"), ("GET", "/api/v1/auth/me"),
])
async def test_private_endpoints_require_sign_in(anon_client: AsyncClient, method, path):
    r = await anon_client.request(method, path, json={})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


async def test_public_endpoints(anon_client: AsyncClient):
    assert (await anon_client.get("/api/v1/health")).status_code == 200
    assert (await anon_client.post("/api/v1/payments/webhook", content=b"not json")).status_code == 400  # PayPal can reach it; signature-checked
    demo = (await anon_client.get("/api/v1/auth/demo-accounts")).json()
    assert demo["enabled"] and {a["role"] for a in demo["accounts"]} == {"requester", "approver", "admin"}


async def test_bad_and_expired_tokens(anon_client: AsyncClient, db):
    assert (await anon_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})).status_code == 401
    user = await db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    expired, _ = create_access_token(user.id, user.role, minutes=-1)
    r = await anon_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    # A token signed with another key is rejected.
    import jwt as pyjwt
    forged = pyjwt.encode({"sub": str(user.id), "iss": "keicy-ai", "iat": 0, "exp": 9_999_999_999}, "attacker-key-that-is-long-enough-for-hs256-0123456789", algorithm="HS256")
    assert (await anon_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401


async def test_disabled_user_is_locked_out(anon_client: AsyncClient, db):
    u = User(email=f"temp-{uuid.uuid4().hex[:6]}@keicy.local", full_name="Temp", role="requester", password_hash=hash_password("pw"))
    db.add(u)
    await db.commit()
    headers = await auth(u.email)
    assert (await anon_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    await db.execute(text("UPDATE users SET is_active = false WHERE id = :id"), {"id": u.id})
    await db.commit()
    assert (await anon_client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (await login(anon_client, u.email, "pw")).status_code == 401


async def test_roles_and_ownership(client: AsyncClient, approver):
    rid = (await client.post("/api/v1/procurement/requests", json={"raw_text": "two monitors please"})).json()["id"]
    other = await auth("requester2@keicy.local")
    # Another requester can't see, edit, run or cancel it — and gets 404, not 403, so ids can't be probed.
    for method, path in (("GET", ""), ("PATCH", ""), ("POST", "/execute"), ("POST", "/cancel"), ("GET", "/status")):
        r = await client.request(method, f"/api/v1/procurement/requests/{rid}{path}", json={}, headers=other)
        assert r.status_code == 404, (method, path, r.status_code)
    # Requesters can't read the audit trail or model usage; approvers can.
    assert (await client.get("/api/v1/audit/logs")).status_code == 403
    assert (await client.get("/api/v1/intelligence/summary")).status_code == 403
    assert (await client.get("/api/v1/audit/logs", headers=approver)).status_code == 200


def test_token_round_trip():
    uid = uuid.uuid4()
    token, exp = create_access_token(uid, "admin", minutes=5)
    assert decode_token(token) == uid
