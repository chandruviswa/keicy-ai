from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.ai.gateway import LLMGateway
from app.api.deps import get_llm, get_paypal
from app.core.config import get_settings
from app.db.session import engine
from app.payments.paypal import PayPalClient

router = APIRouter(tags=["system"])

CLOUD = {"anthropic", "openai"}


@router.get("/health")
async def health(llm: LLMGateway = Depends(get_llm), pp: PayPalClient = Depends(get_paypal)):
    schema_version = None
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            schema_version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        db_ok = True
    except Exception:  # noqa: BLE001 — unreachable DB or migrations not yet run
        db_ok = schema_version is not None

    providers = await llm.status()
    cloud = [p for p in providers if p["name"] in CLOUD and p["configured"]]
    primary = next((p["name"] for p in providers if p["configured"]), None)
    s = get_settings()
    return {
        "status": "ok" if db_ok and primary else "degraded",
        "database": db_ok,
        "schema_version": schema_version,
        "llm": {
            "primary": primary,
            "providers": providers,  # in routing order
            "cloud_configured": bool(cloud),
            "cloud_model": cloud[0]["model"] if cloud else None,
        },
        "paypal": {"configured": pp.configured, "mode": s.paypal_mode},
    }
