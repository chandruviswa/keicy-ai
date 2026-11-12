import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.tools  # noqa: F401 — registers all agent tools
import app.workflow.events  # noqa: F401 — commit hooks that wake WebSocket subscribers
from app.agents.pipeline import register_pipeline_resumers
from app.api.routes import agents, auth, catalog, dashboard, health, insights, payments, procurement, purchase_orders, ws
from app.core.config import get_settings
from app.core.observability import RequestContextMiddleware, configure_logging, install_error_handlers
from app.db.session import SessionLocal, engine
from app.workflow import recover_interrupted

settings = get_settings()
configure_logging(settings.log_format, settings.log_level)
log = logging.getLogger("keicy")

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema is managed by Alembic (`uv run alembic upgrade head`), not created here.
    register_pipeline_resumers()
    try:
        await recover_interrupted(SessionLocal)  # resume pipelines interrupted by the last shutdown
    except Exception:  # noqa: BLE001 — e.g. DB down; the app should still start and report via /health
        log.exception("Workflow recovery skipped")
    yield
    await engine.dispose()


app = FastAPI(title="Keicy AI", description="Autonomous Procurement Intelligence", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestContextMiddleware)
install_error_handlers(app)

for r in (health.router, auth.router, catalog.router, procurement.router, agents.router, purchase_orders.router, payments.router,
          dashboard.router, insights.router, ws.router):
    app.include_router(r, prefix=API_PREFIX)
