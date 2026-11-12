"""Tests run against a real PostgreSQL database: <DB_NAME>_test, created automatically.

Session setup: create the test DB → alembic upgrade → downgrade → upgrade (proves migrations
are reversible) → seed accounts → load the test-only catalogue (tests/catalog_fixture.py). Tests add their own rows with unique names, so they don't need cleanup.
"""

import asyncio
import os
from urllib.parse import quote_plus

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from app.core.config import Settings

_s = Settings()
TEST_DB = f"{_s.db_name}_test"
TEST_URL = (
    f"postgresql+asyncpg://{quote_plus(_s.db_user)}:{quote_plus(_s.db_password)}"
    f"@{_s.db_host}:{_s.db_port}/{TEST_DB}"
)
# Must be set before anything calls get_settings() / imports app.db.session.
os.environ["DATABASE_URL"] = TEST_URL
os.environ["APP_ENV"] = "test"

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="also run tests that call a real local Ollama")


async def _recreate_test_db() -> None:
    conn = await asyncpg.connect(host=_s.db_host, port=_s.db_port, user=_s.db_user, password=_s.db_password, database="postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()


def _alembic(fn, rev: str) -> None:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    cfg.attributes["database_url"] = TEST_URL
    cfg.attributes["configure_logger"] = False
    fn(cfg, rev)


@pytest.fixture(scope="session", autouse=True)
def database():
    asyncio.run(_recreate_test_db())
    _alembic(command.upgrade, "head")
    _alembic(command.downgrade, "base")
    _alembic(command.upgrade, "head")

    from app.db.session import SessionLocal
    from seed.seed import seed_all
    from seed.catalog import load_catalog

    async def _seed():
        async with SessionLocal() as db:
            await seed_all(db)
            await load_catalog(db)

    asyncio.run(_seed())
    yield


@pytest.fixture
async def db():
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def request_id(db):
    """A fresh procurement request (status CREATED) owned by the demo user."""
    from sqlalchemy import select

    from app.models import ProcurementRequest, User
    from seed.data import DEMO_USER_EMAIL

    user = await db.scalar(select(User).where(User.email == DEMO_USER_EMAIL))
    pr = ProcurementRequest(requester_id=user.id, raw_text="5 laptops for the design team", budget=10000, currency="USD")
    db.add(pr)
    await db.commit()
    return pr.id


@pytest.fixture
async def agent_run_id(db, request_id):
    from app.models import AgentRun

    run = AgentRun(request_id=request_id, agent_name="test")
    db.add(run)
    await db.commit()
    return run.id


async def token_for(email: str) -> str:
    from sqlalchemy import select

    from app.core.security import create_access_token
    from app.db.session import SessionLocal
    from app.models import User

    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.email == email))
    return create_access_token(user.id, user.role)[0]


async def auth(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {await token_for(email)}"}


@pytest.fixture
async def anon_client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def client(anon_client):
    """Signed in as the seeded requester (pass headers=approver / admin to act as someone else)."""
    from seed.data import DEMO_USER_EMAIL

    anon_client.headers.update(await auth(DEMO_USER_EMAIL))
    yield anon_client


@pytest.fixture
async def approver():
    return await auth("approver@keicy.local")


@pytest.fixture
async def admin():
    return await auth("admin@keicy.local")
