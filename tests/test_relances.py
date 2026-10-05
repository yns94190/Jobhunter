from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models import Job, JobStatus, Source, Zone
from app.services.tracker import RELANCE_APRES_JOURS


def _naive_utc(**delta) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(**delta)).replace(tzinfo=None)


def _job(session: Session, key: str, **kwargs) -> Job:
    defaults = dict(
        title=f"Poste {key}", title_normalized=f"poste {key}", url=f"https://example.org/{key}",
        dedup_hash=f"h-{key}", zone=Zone.P1_IDF, company="Acme", location="Lyon",
    )
    job = Job(**{**defaults, **kwargs})
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_relances_returns_display_fields(client, session):
    # Les sources sont créées par le seed au démarrage du client
    source = session.exec(select(Source).where(Source.name == "adzuna_fr")).one()
    old = _job(session, "vieux", status=JobStatus.APPLIED, applied_at=_naive_utc(days=RELANCE_APRES_JOURS + 4),
               source_id=source.id)
    _job(session, "recent", status=JobStatus.APPLIED, applied_at=_naive_utc(days=2))
    _job(session, "relance", status=JobStatus.FOLLOWED_UP, applied_at=_naive_utc(days=30))

    relances = client.get("/relances").json()
    assert [r["job_id"] for r in relances] == [old.id]
    r = relances[0]
    assert r["id"] == old.id
    assert r["jours"] == RELANCE_APRES_JOURS + 4
    assert (r["title"], r["company"], r["location"]) == ("Poste vieux", "Acme", "Lyon")
    assert r["source_name"] == "adzuna_fr"
    assert r["url"] == "https://example.org/vieux"
    assert r["applied_at"] is not None
    assert r["status"] == "applied"


def test_marking_followed_up_removes_from_relances(client, session):
    job = _job(session, "a-relancer", status=JobStatus.APPLIED, applied_at=_naive_utc(days=RELANCE_APRES_JOURS + 1))
    assert len(client.get("/relances").json()) == 1
    client.patch(f"/jobs/{job.id}/status?status=followed_up")
    assert client.get("/relances").json() == []


def test_stats_exposes_threshold_and_open_counts(client, session):
    _job(session, "n1", status=JobStatus.NEW, zone=Zone.P4_SUISSE, country="CH")
    _job(session, "n2", status=JobStatus.DRAFTED, zone=Zone.P1_IDF)
    _job(session, "c1", status=JobStatus.APPLIED, zone=Zone.P4_SUISSE, country="CH")
    _job(session, "c2", status=JobStatus.IGNORED, zone=Zone.P1_IDF)

    stats = client.get("/stats").json()
    assert stats["relance_apres_jours"] == RELANCE_APRES_JOURS
    assert stats["total"] == 4
    assert stats["open_total"] == 2
    assert stats["open_par_zone"] == {"P4_SUISSE": 1, "P1_IDF": 1}
    assert stats["par_zone"] == {"P4_SUISSE": 2, "P1_IDF": 2}
