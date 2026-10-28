from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.config import Settings, get_settings
from app.db import ensure_database_current, make_engine
from app.providers import build_transcription_provider
from app.routers import events, learners, sources, tenants


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    # Schema is managed by Alembic (`alembic upgrade head`); never created here.
    ensure_database_current(engine)

    app = FastAPI(
        title="AI Skill Upscaler API",
        version="0.1.0",
        description="Multi-tenant learning backend. Authenticate with the X-API-Key header.",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.transcriber = build_transcription_provider(settings)

    app.include_router(tenants.admin_router)
    app.include_router(tenants.router)
    app.include_router(sources.router)
    app.include_router(learners.router)
    app.include_router(events.router)

    @app.get("/health", tags=["meta"])
    def health():
        return {
            "status": "ok",
            "database": engine.dialect.name,
            "transcription_provider": app.state.transcriber.name if app.state.transcriber else None,
        }

    return app
