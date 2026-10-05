from __future__ import annotations

import logging
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlmodel import Session, select

from app.config import settings
from app.models import Application, Attachment, Job, JobStatus
from app.models_history import StatusHistory
from app.services.attachments import AttachmentError, read_bytes

logger = logging.getLogger(__name__)

RELANCE_APRES_JOURS = 10

# Controle volontairement simple : une seule adresse, sans espace ni retour a la ligne
EMAIL_RE = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


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


def _check_smtp_config() -> None:
    """Refuse l'envoi si une variable SMTP manque, en nommant lesquelles."""
    manquantes = [
        nom
        for nom, valeur in (
            ("SMTP_HOST", settings.smtp_host),
            ("SMTP_USER", settings.smtp_user),
            ("SMTP_PASSWORD", settings.smtp_password),
        )
        if not valeur
    ]
    if manquantes:
        raise TrackerError("configuration SMTP incomplete : " + ", ".join(manquantes) + " manquant(s) dans .env")


def send_application(
    session: Session,
    job: Job,
    to_email: str | None = None,
    attachment_ids: list[int] | None = None,
) -> dict:
    """Envoie la candidature par SMTP. L'utilisateur declenche, jamais le systeme.

    Aucune tache planifiee ne doit appeler cette fonction : seule la route
    POST /jobs/{id}/send, declenchee par un clic confirme, l'utilise.
    """
    _check_smtp_config()

    destinataire = (to_email or job.contact_email or "").strip()
    if not destinataire:
        raise TrackerError("aucune adresse de destinataire")
    if not EMAIL_RE.fullmatch(destinataire):
        raise TrackerError(f"adresse de destinataire invalide : {destinataire}")

    application = session.exec(
        select(Application)
        .where(Application.job_id == job.id, Application.is_current == True)  # noqa: E712
    ).first()

    if not application:
        raise TrackerError("aucun brouillon courant")

    # Toutes les pieces jointes sont chargees avant d'ouvrir la connexion SMTP
    pieces = []
    for attachment_id in dict.fromkeys(attachment_ids or []):  # sans doublons, ordre conserve
        attachment = session.get(Attachment, attachment_id)
        if not attachment:
            raise TrackerError(f"piece jointe introuvable : {attachment_id}")
        try:
            pieces.append((attachment, read_bytes(attachment)))
        except AttachmentError as exc:
            raise TrackerError(str(exc)) from exc

    expediteur = settings.smtp_from or settings.smtp_user

    message = EmailMessage()
    message["From"] = expediteur
    message["Reply-To"] = expediteur
    message["To"] = destinataire
    message["Subject"] = application.subject or f"Candidature - {job.title}"
    message.set_content(application.cover_letter or "")

    for attachment, contenu in pieces:
        maintype, _, subtype = attachment.content_type.partition("/")
        message.add_attachment(contenu, maintype=maintype, subtype=subtype, filename=attachment.filename)

    try:
        # Port 465 : TLS des la connexion ; sinon STARTTLS (587)
        smtp_cls = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
        with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if smtp_cls is smtplib.SMTP:
                smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            # Enveloppe : le compte authentifie ; en-tete From : SMTP_FROM
            smtp.send_message(message, from_addr=settings.smtp_user, to_addrs=[destinataire])
    except Exception as exc:
        raise TrackerError(f"envoi echoue : {exc}") from exc

    application.sent_at = datetime.now(timezone.utc)
    session.add(application)
    update_status(session, job, JobStatus.APPLIED, note=f"envoye par mail a {destinataire}")

    logger.info(
        "Candidature envoyee pour l'offre %s a %s (%d piece(s) jointe(s))",
        job.id, destinataire, len(pieces),
    )
    return {
        "sent_to": destinataire,
        "job_id": job.id,
        "version": application.version,
        "attachments": [a.filename for a, _ in pieces],
    }


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
