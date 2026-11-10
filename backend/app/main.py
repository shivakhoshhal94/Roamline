from contextlib import asynccontextmanager
from threading import Lock
from app.api.discovery import router as discovery_router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.health import router
from app.api.recommendations import router as recommendations_router
from app.api.traveler_profile import router as profile_router
from app.core.config import Settings
from app.db.connection import create_db_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_db_engine(config)
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Roamline API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=config.cors_origins,
        allow_methods=["GET", "POST"], allow_headers=["Accept", "Content-Type"],
    )
    app.state.retrieval_lock = Lock()
    app.state.retrieval_encoder = None
    app.state.settings = config
    app.include_router(discovery_router)
    app.include_router(recommendations_router)
    app.include_router(router)
    app.include_router(profile_router)
    return app
