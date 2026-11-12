"""Run with:  uv run python -m seed"""

import asyncio

from app.db.session import SessionLocal, engine
from seed.seed import seed_all


async def main() -> None:
    async with SessionLocal() as db:
        added = await seed_all(db)
    await engine.dispose()
    print("Seed complete. Added:", ", ".join(f"{k}={v}" for k, v in added.items()))


if __name__ == "__main__":
    asyncio.run(main())
