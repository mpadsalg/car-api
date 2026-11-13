from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from carapi.db.base import Base


class _Probe(Base):
    __tablename__ = "_probe"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(20))


def test_data_persists_across_sessions(client: TestClient) -> None:
    app = client.app
    assert isinstance(app, FastAPI)
    factory: sessionmaker[Session] = app.state.session_factory
    with factory() as s:
        s.add(_Probe(name="civic"))
        s.commit()
    with factory() as s:
        assert s.scalars(select(_Probe.name)).all() == ["civic"]
