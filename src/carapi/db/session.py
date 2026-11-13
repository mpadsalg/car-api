from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from carapi.core.config import Settings


def build_engine(settings: Settings) -> Engine:
    kwargs: dict[str, Any] = {"echo": settings.sql_echo}
    if settings.database_url.startswith("sqlite"):
        # Share one connection across threads so the in-memory DB survives between requests.
        kwargs |= {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    return create_engine(settings.database_url, **kwargs)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


DbSession = Annotated[Session, Depends(get_db)]
