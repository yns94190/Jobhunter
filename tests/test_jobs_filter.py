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
