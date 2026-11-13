import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from carapi import models  # noqa: F401  (register tables on Base.metadata)
from carapi.api.errors import register_error_handlers
from carapi.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from carapi.api.routes import cars, cars_csv, health
from carapi.core.config import Settings, get_settings
from carapi.core.rate_limit import SlidingWindowLimiter
from carapi.core.request_context import configure_logging
from carapi.db.base import Base
from carapi.db.session import build_engine, build_session_factory
from carapi.services.csv_io import ImportMode, decode, import_csv

logger = logging.getLogger("carapi")


class SeedError(RuntimeError):
    pass


def seed_database(settings: Settings, factory: sessionmaker[Session]) -> int:
    """Load settings.seed_file (atomic). Fails startup loudly if the file is missing/invalid."""
    path = settings.seed_file
    if path is None:
        return 0
    try:
        text = decode(path.read_bytes())
    except OSError as exc:
        raise SeedError(f"Cannot read seed file {path}: {exc}") from exc
    with factory() as db:
        result = import_csv(db, text, ImportMode.ATOMIC)
    if result.errors:
        first = "; ".join(
            f"line {e.line}: " + ", ".join(f"{f.field}: {f.msg}" for f in e.errors)
            for e in result.errors[:3]
        )
        raise SeedError(f"Seed file {path} has {result.failed} invalid rows: {first}")
    logger.info("Seeded %d cars from %s", result.created, path)
    return result.created


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = build_engine(settings)
    limiter = SlidingWindowLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
        seed_database(settings, session_factory)
        if not settings.api_keys:
            logger.warning("CARAPI_API_KEYS is empty: all write endpoints will return 401")
        yield
        engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.rate_limiter = limiter

    register_error_handlers(app)
    # add_middleware prepends: last added = outermost. Request context wraps rate limiting.
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=limiter)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(cars_csv.router)  # before cars: /cars/export.csv vs /cars/{car_id}
    app.include_router(cars.router)
    return app


app = create_app()
