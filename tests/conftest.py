import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import main
from app.db import get_session
from app.main import app


@pytest.fixture(name="test_engine")
def test_engine_fixture():
    """Base SQLite en mémoire, recréée à chaque test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="session")
def session_fixture(test_engine):
    with Session(test_engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session, test_engine, monkeypatch):
    # Le lifespan appelle seed() via main.engine : on le fait pointer sur la base de test.
    monkeypatch.setattr(main, "engine", test_engine)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()