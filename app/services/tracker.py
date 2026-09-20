from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlmodel import Session, select

from app.config import settings
from app.models import Application, Job, JobStatus
from app.models_history import StatusHistory

logger = logging.getLogger(__name__)

RELANCE_APRES_JOURS = 10


class TrackerError(RuntimeError):
    """Echec d'une operation de suivi."""


def update_status(session: Session, job: Job, status: JobStatus, note: str | None = None) -> Job:
    """Change le statut d'une offre, horodate et trace le changement."""
    ancien = job.status.value if hasattr(job.status, "value") else str(job.status)
    nouveau = status.value if hasattr(status, "value") else str(status)

    if ancien != nouveau:
        session.add(StatusHistory(job_id=job.id, from_status=ancien, to_status=nouveau, note=note))

    job.status = status
    job.updated_at = datetime.now(timezone.utc)

    if status == JobStatus.APPLIED and not job.applied_at:
        job.applied_at = datetime.now(timezone.utc)

    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def update_draft(session: Session, job_id: int, subject: str, cover_letter: str) -> Application:
    """Enregistre les corrections de l'utilisateur comme nouvelle version."""
    existing = session.exec(
        select(Application).where(Application.job_id == job_id).order_by(Application.version.desc())
    ).all()

    if not existing:
        raise TrackerError("aucun brouillon a modifier")

    for app in existing:
        app.is_current = False
        session.add(app)

    current = existing[0]
    revised = Application(
        job_id=job_id,
        version=current.version + 1,
        is_current=True,
        subject=subject,
        cover_letter=cover_letter,
        key_points=current.key_points,
        confidence_score=current.confidence_score,
        model=f"{current.model} + revision manuelle",
        variant=current.variant,
    )
    session.add(revised)
    session.commit()
    session.refresh(revised)

    logger.info("Brouillon revise pour l'offre %s (v%d)", job_id, revised.version)
    return revised


def send_application(session: Session, job: Job, to_email: str | None = None) -> dict:
    """Envoie la candidature par SMTP. L'utilisateur declenche, jamais le systeme."""
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
        raise TrackerError("configuration SMTP incomplete")

    destinataire = to_email or job.contact_email
    if not destinataire:
        raise TrackerError("aucune adresse de contact pour cette offre")

    application = session.exec(
        select(Application)
        .where(Application.job_id == job.id, Application.is_current == True)  # noqa: E712
    ).first()

    if not application:
        raise TrackerError("aucun brouillon courant")

    message = EmailMessage()
    message["From"] = settings.smtp_user
    message["To"] = destinataire
    message["Subject"] = application.subject or f"Candidature - {job.title}"
    message.set_content(application.cover_letter or "")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:
        raise TrackerError(f"envoi echoue : {exc}") from exc

    application.sent_at = datetime.now(timezone.utc)
    session.add(application)
    update_status(session, job, JobStatus.APPLIED)

    logger.info("Candidature envoyee pour l'offre %s a %s", job.id, destinataire)
    return {"sent_to": destinataire, "job_id": job.id, "version": application.version}


def jobs_a_relancer(session: Session) -> list[dict]:
    """Offres postulees depuis plus de 10 jours sans reponse."""
    limite = datetime.now(timezone.utc) - timedelta(days=RELANCE_APRES_JOURS)

    jobs = session.exec(
        select(Job).where(Job.status == JobStatus.APPLIED, Job.applied_at != None)  # noqa: E711
    ).all()

    resultats = []
    for job in jobs:
        applied = job.applied_at
        if applied and applied.tzinfo is None:
            applied = applied.replace(tzinfo=timezone.utc)
        if applied and applied < limite:
            resultats.append(
                {
                    "job_id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "applied_at": job.applied_at,
                    "jours": (datetime.now(timezone.utc) - applied).days,
                }
            )

    return sorted(resultats, key=lambda r: -r["jours"])
