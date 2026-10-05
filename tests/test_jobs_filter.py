import pytest
from sqlmodel import Session

from app.models import Job, JobStatus, Zone


@pytest.fixture(name="jobs")
def jobs_fixture(session: Session) -> dict[str, Job]:
    """Une offre par statut, toutes en France."""
    jobs = {}
    for i, status in enumerate(JobStatus):
        job = Job(
            title=f"Offre {status.value}",
            title_normalized=f"offre {status.value}",
            url=f"https://example.org/{i}",
            dedup_hash=f"hash-{i}",
            zone=Zone.P1_IDF,
            country="FR",
            status=status,
            score=70,
        )
        session.add(job)
        jobs[status.value] = job
    session.commit()
    return jobs


def _statuses(response) -> set[str]:
    assert response.status_code == 200
    return {item["status"] for item in response.json()["items"]}


def test_closed_jobs_hidden_by_default(client, jobs):
    assert _statuses(client.get("/jobs")) == {"new", "drafted"}


def test_closed_jobs_hidden_in_country_and_zone_tabs(client, jobs):
    assert _statuses(client.get("/jobs?country=FR&min_score=60")) == {"new", "drafted"}
    assert _statuses(client.get("/jobs?zone=P1_IDF")) == {"new", "drafted"}


def test_include_closed_shows_everything(client, jobs):
    assert _statuses(client.get("/jobs?include_closed=true")) == {s.value for s in JobStatus}


@pytest.mark.parametrize("status", ["applied", "followed_up", "interview", "rejected", "ignored"])
def test_explicit_status_filter_still_works(client, jobs, status):
    assert _statuses(client.get(f"/jobs?status={status}")) == {status}


def test_job_disappears_after_being_marked_applied(client, jobs):
    job_id = jobs["new"].id
    client.patch(f"/jobs/{job_id}/status?status=applied")
    ids = {item["id"] for item in client.get("/jobs").json()["items"]}
    assert job_id not in ids
    applied_ids = {item["id"] for item in client.get("/jobs?status=applied").json()["items"]}
    assert job_id in applied_ids


def test_search_also_hides_closed_jobs(client, jobs):
    assert _statuses(client.get("/jobs?q=offre")) == {"new", "drafted"}


# --- date_field=applied (filtre sur la date de candidature) ---

from datetime import datetime, timedelta, timezone  # noqa: E402


def _naive_utc(**delta) -> datetime:
    """Les dates en base sont naïves (UTC) : on construit les données de test de la même façon."""
    return (datetime.now(timezone.utc) - timedelta(**delta)).replace(tzinfo=None)


def _make(session: Session, key: str, **kwargs) -> Job:
    defaults = dict(
        title=f"Poste {key}",
        title_normalized=f"poste {key}",
        url=f"https://example.org/{key}",
        dedup_hash=f"h-{key}",
        zone=Zone.P1_IDF,
        country="FR",
        score=50,
    )
    job = Job(**{**defaults, **kwargs})
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _ids(response) -> list[int]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_applied_today_on_old_posting_found_with_date_field_applied(client, session):
    job = _make(
        session, "ancienne",
        status=JobStatus.APPLIED, published_at=_naive_utc(days=21), applied_at=_naive_utc(hours=1),
    )
    # Filtre historique sur la publication : l'offre n'apparaît pas
    assert _ids(client.get("/jobs?status=applied&max_age_days=1")) == []
    # Filtre sur la date de candidature : elle apparaît
    assert _ids(client.get("/jobs?status=applied&max_age_days=1&date_field=applied")) == [job.id]


def test_applied_long_ago_excluded_from_recent_window(client, session):
    _make(session, "vieille", status=JobStatus.APPLIED, published_at=_naive_utc(hours=2), applied_at=_naive_utc(days=5))
    assert _ids(client.get("/jobs?status=applied&max_age_days=1&date_field=applied")) == []
    assert len(_ids(client.get("/jobs?status=applied&max_age_days=7&date_field=applied"))) == 1


def test_job_without_applied_at_never_listed_with_date_field_applied(client, session):
    _make(session, "sans-date", status=JobStatus.APPLIED, published_at=_naive_utc(hours=1), applied_at=None)
    _make(session, "nouvelle", status=JobStatus.NEW, published_at=_naive_utc(hours=1))
    assert _ids(client.get("/jobs?status=applied&date_field=applied")) == []
    assert _ids(client.get("/jobs?status=applied&max_age_days=1&date_field=applied")) == []
    assert _ids(client.get("/jobs?date_field=applied&include_closed=true")) == []


def test_date_field_applied_sorts_by_applied_at_desc(client, session):
    # Le score ne doit plus dicter l'ordre : la candidature la plus récente d'abord
    old = _make(session, "a", status=JobStatus.APPLIED, score=90, applied_at=_naive_utc(days=3))
    recent = _make(session, "b", status=JobStatus.APPLIED, score=10, applied_at=_naive_utc(hours=1))
    middle = _make(session, "c", status=JobStatus.APPLIED, score=50, applied_at=_naive_utc(days=1))
    assert _ids(client.get("/jobs?status=applied&date_field=applied")) == [recent.id, middle.id, old.id]


def test_invalid_date_field_rejected(client):
    assert client.get("/jobs?date_field=nimportequoi").status_code == 422


def test_marking_applied_now_shows_in_today(client, session):
    """Critère de réussite du point 1, de bout en bout via l'API."""
    job = _make(session, "bout-en-bout", status=JobStatus.NEW, published_at=_naive_utc(days=30))
    client.patch(f"/jobs/{job.id}/status?status=applied")
    assert _ids(client.get("/jobs?status=applied&max_age_days=1&date_field=applied")) == [job.id]


@pytest.mark.parametrize("status", ["followed_up", "interview", "rejected"])
def test_post_application_status_sets_missing_applied_at(client, session, status):
    job = _make(session, f"direct-{status}", status=JobStatus.NEW)
    client.patch(f"/jobs/{job.id}/status?status={status}")
    session.refresh(job)
    assert job.applied_at is not None
    assert _ids(client.get(f"/jobs?status={status}&date_field=applied")) == [job.id]


def test_existing_applied_at_is_kept(client, session):
    original = _naive_utc(days=12)
    job = _make(session, "garde-date", status=JobStatus.APPLIED, applied_at=original)
    client.patch(f"/jobs/{job.id}/status?status=interview")
    session.refresh(job)
    assert job.applied_at == original


# --- Zone combinée au statut, total de résultats ---


def test_zone_and_status_combination_returns_only_swiss_applications(client, session):
    ch = _make(session, "ch", status=JobStatus.APPLIED, zone=Zone.P4_SUISSE, country="CH", applied_at=_naive_utc(hours=1))
    _make(session, "fr", status=JobStatus.APPLIED, zone=Zone.P1_IDF, applied_at=_naive_utc(hours=1))
    _make(session, "ch-new", status=JobStatus.NEW, zone=Zone.P4_SUISSE, country="CH")
    assert _ids(client.get("/jobs?status=applied&zone=P4_SUISSE&date_field=applied")) == [ch.id]
    assert _ids(client.get("/jobs?status=applied&zone=P4_SUISSE")) == [ch.id]


def test_total_counts_all_results_beyond_page(client, session):
    for i in range(5):
        _make(session, f"t{i}", status=JobStatus.NEW)
    body = client.get("/jobs?limit=2").json()
    assert body["count"] == 2
    assert body["total"] == 5
    body = client.get("/jobs?limit=2&q=poste").json()
    assert (body["count"], body["total"]) == (2, 5)
