import smtplib

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import Application, Attachment, Job, JobStatus, Zone
from app.models_history import StatusHistory

PDF = b"%PDF-1.4\n% faux pdf de test\n"


class FakeSMTP:
    """Remplace smtplib.SMTP : aucun mail ne part pendant les tests."""

    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message, from_addr=None, to_addrs=None):
        FakeSMTP.sent.append({"message": message, "from_addr": from_addr, "to_addrs": to_addrs})


class FailingSMTP(FakeSMTP):
    def login(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")


@pytest.fixture(autouse=True)
def smtp_settings(tmp_path, monkeypatch):
    """Config SMTP fictive, valeurs neutres sans aucune vraie adresse."""
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.org")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "relay@example.org")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    monkeypatch.setattr(settings, "smtp_from", "candidat@example.org")
    monkeypatch.setattr(settings, "attachments_dir", str(tmp_path / "attachments"))
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []


@pytest.fixture(name="job")
def job_fixture(session: Session) -> Job:
    job = Job(
        title="Technicien support",
        title_normalized="technicien support",
        url="https://example.org/offre",
        dedup_hash="h1",
        zone=Zone.P1_IDF,
        status=JobStatus.DRAFTED,
        contact_email="rh@entreprise.example",
    )
    session.add(job)
    session.commit()
    session.add(
        Application(job_id=job.id, subject="Candidature technicien", cover_letter="Madame, Monsieur,\nLettre.")
    )
    session.commit()
    session.refresh(job)
    return job


def _upload(client, name="CV.pdf", content=PDF) -> dict:
    response = client.post("/attachments", files={"file": (name, content, "application/pdf")})
    assert response.status_code == 201
    return response.json()


def test_send_with_attachments(client, session, job):
    cv = _upload(client, "CV.pdf")
    lettre = _upload(client, "Lettre.pdf")

    response = client.post(
        f"/jobs/{job.id}/send",
        json={"to_email": "autre@entreprise.example", "attachment_ids": [cv["id"], lettre["id"]]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sent_to"] == "autre@entreprise.example"
    assert response.json()["attachments"] == ["CV.pdf", "Lettre.pdf"]

    assert len(FakeSMTP.sent) == 1
    sent = FakeSMTP.sent[0]
    message = sent["message"]
    assert sent["from_addr"] == "relay@example.org"          # enveloppe : SMTP_USER
    assert sent["to_addrs"] == ["autre@entreprise.example"]
    assert message["From"] == "candidat@example.org"          # en-tete : SMTP_FROM
    assert message["Reply-To"] == "candidat@example.org"
    assert message["To"] == "autre@entreprise.example"
    assert message["Subject"] == "Candidature technicien"

    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "Madame, Monsieur," in body

    pieces = list(message.iter_attachments())
    assert [p.get_filename() for p in pieces] == ["CV.pdf", "Lettre.pdf"]
    assert pieces[0].get_content_type() == "application/pdf"
    assert pieces[0].get_content() == PDF


def test_send_marks_job_applied_with_history(client, session, job):
    response = client.post(f"/jobs/{job.id}/send", json={})
    assert response.status_code == 200, response.text

    session.refresh(job)
    assert job.status == JobStatus.APPLIED
    assert job.applied_at is not None

    application = session.exec(select(Application).where(Application.job_id == job.id)).one()
    assert application.sent_at is not None

    history = session.exec(select(StatusHistory).where(StatusHistory.job_id == job.id)).all()
    assert [(h.from_status, h.to_status) for h in history] == [("drafted", "applied")]


def test_send_defaults_to_job_contact_email(client, job):
    response = client.post(f"/jobs/{job.id}/send", json={})
    assert response.json()["sent_to"] == "rh@entreprise.example"


def test_send_uses_current_draft_version(client, job):
    client.put(f"/jobs/{job.id}/draft", json={"subject": "Objet corrige", "cover_letter": "Lettre corrigee"})
    client.post(f"/jobs/{job.id}/send", json={})
    message = FakeSMTP.sent[0]["message"]
    assert message["Subject"] == "Objet corrige"
    assert "Lettre corrigee" in message.get_content()


def test_from_falls_back_to_smtp_user(client, job, monkeypatch):
    monkeypatch.setattr(settings, "smtp_from", None)
    client.post(f"/jobs/{job.id}/send", json={})
    assert FakeSMTP.sent[0]["message"]["From"] == "relay@example.org"


@pytest.mark.parametrize("missing", ["smtp_host", "smtp_user", "smtp_password"])
def test_incomplete_smtp_config_returns_400(client, session, job, monkeypatch, missing):
    monkeypatch.setattr(settings, missing, None)
    response = client.post(f"/jobs/{job.id}/send", json={})
    assert response.status_code == 400
    assert missing.upper() in response.json()["detail"]
    assert FakeSMTP.sent == []
    session.refresh(job)
    assert job.status == JobStatus.DRAFTED


def test_missing_recipient_returns_400(client, session, job):
    job.contact_email = None
    session.add(job)
    session.commit()
    response = client.post(f"/jobs/{job.id}/send", json={})
    assert response.status_code == 400
    assert FakeSMTP.sent == []


@pytest.mark.parametrize("bad", ["pas-une-adresse", "a@b.fr, c@d.fr", "a@b.fr\nBcc: x@y.fr"])
def test_invalid_recipient_returns_400(client, job, bad):
    response = client.post(f"/jobs/{job.id}/send", json={"to_email": bad})
    assert response.status_code == 400
    assert FakeSMTP.sent == []


def test_unknown_attachment_returns_400_without_sending(client, session, job):
    response = client.post(f"/jobs/{job.id}/send", json={"attachment_ids": [999]})
    assert response.status_code == 400
    assert FakeSMTP.sent == []
    session.refresh(job)
    assert job.status == JobStatus.DRAFTED


def test_attachment_file_missing_on_disk_returns_400(client, session, job):
    cv = _upload(client)
    attachment = session.get(Attachment, cv["id"])
    attachment.path = "inexistant.pdf"
    session.add(attachment)
    session.commit()
    response = client.post(f"/jobs/{job.id}/send", json={"attachment_ids": [cv["id"]]})
    assert response.status_code == 400
    assert FakeSMTP.sent == []


def test_smtp_failure_returns_400_and_keeps_status(client, session, job, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FailingSMTP)
    response = client.post(f"/jobs/{job.id}/send", json={})
    assert response.status_code == 400
    assert "envoi echoue" in response.json()["detail"]
    session.refresh(job)
    assert job.status == JobStatus.DRAFTED
    application = session.exec(select(Application).where(Application.job_id == job.id)).one()
    assert application.sent_at is None


def test_no_scheduled_job_can_send_applications():
    """Règle du projet : aucun envoi automatique, le scheduler ne fait que collecter."""
    import inspect

    from app import scheduler

    source = inspect.getsource(scheduler)
    assert "send_application" not in source
    assert "tracker" not in source
