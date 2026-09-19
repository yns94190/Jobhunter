from collections.abc import Generator

from sqlmodel import Session, create_engine

from app.config import settings

# check_same_thread=False : nécessaire pour SQLite servi par FastAPI
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


def get_session() -> Generator[Session, None, None]:
    """Dépendance FastAPI : une session par requête."""
    with Session(engine) as session:
        yield session