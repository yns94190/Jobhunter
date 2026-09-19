import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import Job, Zone
from app.seed import seed


def _job(**kwargs) -> Job:
    defaults = dict(
        title="Technicien support N1",
        title_normalized="technicien support n1",
        company="Acme",
        location="Paris",
        zone=Zone.P1_IDF,
        url="https://example.org/1",
        dedup_hash="abc123",
    )
    return Job(**{**defaults, **kwargs})


def test_create_job(session: Session):
    session.add(_job())
    session.commit()
    job = session.exec(select(Job)).one()
    assert job.id is not None
    assert job.status == "new"
    assert job.score == 0
    assert job.score_detail == {}


def test_dedup_hash_is_unique(session: Session):
    session.add(_job())
    session.commit()
    session.add(_job(url="https://example.org/2"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_seed_is_idempotent(session: Session):
    seed(session)
    seed(session)
    from app.models import Profile, Source

    assert len(session.exec(select(Profile)).all()) == 1
    assert len(session.exec(select(Source)).all()) == 5