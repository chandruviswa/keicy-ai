"""Recreate a database from scratch: drop → create → migrate → seed. Used by the E2E suite.

    uv run python -m scripts.reset_db paypal_e2e

Refuses to touch the database named in DB_NAME (your dev data) unless --force is given.
"""

import argparse
import asyncio
import os
import sys
from urllib.parse import quote_plus

import asyncpg

from app.core.config import Settings


async def recreate(s: Settings, name: str) -> None:
    conn = await asyncpg.connect(host=s.db_host, port=s.db_port, user=s.db_user, password=s.db_password, database="postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def _dev_db_name() -> str:
    """DB_NAME as written in .env (ignoring environment overrides) — the database holding your dev data."""
    from dotenv import dotenv_values

    return (dotenv_values(".env").get("DB_NAME") or "procura").strip()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    base = Settings()
    if not a.name.replace("_", "").isalnum():
        sys.exit("invalid database name")
    if a.name == _dev_db_name() and not a.force:
        sys.exit(f"refusing to reset your development database {a.name!r} (use --force)")

    asyncio.run(recreate(base, a.name))
    url = f"postgresql+asyncpg://{quote_plus(base.db_user)}:{quote_plus(base.db_password)}@{base.db_host}:{base.db_port}/{a.name}"
    os.environ["DATABASE_URL"] = url

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    from app.db.session import SessionLocal, engine
    from seed.seed import seed_all

    async def seed():
        async with SessionLocal() as db:
            print("seeded:", await seed_all(db))
        await engine.dispose()

    asyncio.run(seed())


if __name__ == "__main__":
    main()
