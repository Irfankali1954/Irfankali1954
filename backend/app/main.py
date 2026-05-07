import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import Base, engine

# Surface our own loggers (notably ``app.services.watchdog``) in the
# uvicorn console. ``force=True`` is required because uvicorn's logging
# config sets handlers on the root logger before our app module is
# imported; without it, basicConfig is a no-op.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)

log = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Models are imported via app.models.__init__ to register on Base.metadata.
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Heatmap dwell-time watchdog runs as a single background task. It
    # iterates every project on a configurable cadence (default 30 min)
    # and asks risk_heatmap.evaluate to re-classify cells and fire any
    # Tier-3 alerts whose dwell-time has crossed 48h.
    watchdog_task: asyncio.Task | None = None
    if settings.watchdog_enabled:
        from app.services.watchdog import loop as watchdog_loop
        watchdog_task = asyncio.create_task(watchdog_loop(), name="heatmap-watchdog")

    try:
        yield
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except (asyncio.CancelledError, Exception):  # pragma: no cover
                pass


app = FastAPI(
    title="EPC Master-Wrap Agent",
    version=__version__,
    description="Cross-organizational intelligence layer for major EPC firms.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


app.include_router(api_router, prefix="/api/v1")
