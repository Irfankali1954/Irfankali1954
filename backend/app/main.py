from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.db.session import Base, engine

settings = get_settings()

app = FastAPI(
    title="EPC Master-Wrap Agent",
    version=__version__,
    description="Cross-organizational intelligence layer for major EPC firms.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    # Models are imported via app.models.__init__ to register on Base.metadata.
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


app.include_router(api_router, prefix="/api/v1")
