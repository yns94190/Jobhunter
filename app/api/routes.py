from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlmodel import Session, select

from app.config import settings
from app.connectors.adzuna import AdzunaConnector
from app.connectors.france_travail import FranceTravailConnector
from app.connectors.imap_alerts import ImapAlertsConnector
from app.db import get_session
from app.models import CLOSED_STATUSES, Application, Attachment, Job, JobStatus, Metier, Source, Zone
from app.services.analytics import analytics, historique_offre
from app.services.attachments import AttachmentError, delete_attachment, save_upload
from app.services.backfill import backfill_zones_et_metiers
from app.services.contact import extract_contact_email, is_valid_email
from app.services.dedup import normalize
from app.services.generator import GeneratorError, generate_application, generate_batch
from app.services.ingest import run_connector
from app.services.scoring import score_all
from app.services.tracker import (
    RELANCE_APRES_JOURS,
    TrackerError,
    jobs_a_relancer,
    send_application,
    update_draft,
    update_status,
)

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """Vérifie que l'API répond et que la base est joignable."""
    try:
        session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:  # noqa: BLE001 — on ne veut jamais faire tomber /health
        db_status = "error"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "version": settings.app_version,
    }


@router.get("/jobs")
def list_jobs(
    min_score: int = 0,
    source: str | None = None,
    zone: Zone | None = None,
    metier: Metier | None = None,
    status: JobStatus | None = None,
    country: str | None = None,
    q: str | None = None,
    max_age_days: int | None = None,
    date_field: Literal["published", "applied"] = "published",
    include_closed: bool = False,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    """Liste les offres, filtrées et paginées.

    Sans filtre de statut, les offres déjà traitées (postulées, relancées, entretien,
    refusées, ignorées) sont masquées, sauf si include_closed=true.

    date_field=applied : max_age_days porte sur la date de candidature, le tri se fait
    par candidature la plus récente, et les offres jamais postulées sont exclues.
    """
    statement = select(Job).where(Job.score >= min_score)

    if source:
        src = session.exec(select(Source).where(Source.name == source)).first()
        if not src:
            raise HTTPException(status_code=404, detail=f"Source inconnue : {source}")
        statement = statement.where(Job.source_id == src.id)

    if zone:
        statement = statement.where(Job.zone == zone)
    if metier:
        statement = statement.where(Job.metier == metier)
    if status:
        # Un filtre explicite par statut l'emporte toujours sur le masquage
        statement = statement.where(Job.status == status)
    elif not include_closed:
        statement = statement.where(Job.status.not_in(CLOSED_STATUSES))
    if country:
        statement = statement.where(Job.country == country)

    by_applied = date_field == "applied"
    date_column = Job.applied_at if by_applied else Job.published_at
    if by_applied:
        statement = statement.where(Job.applied_at.is_not(None))

    if max_age_days:
        limite = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        # Les dates en base sont naives : on compare sans fuseau
        statement = statement.where(date_column >= limite.replace(tzinfo=None))

    if by_applied:
        statement = statement.order_by(Job.applied_at.desc(), Job.id.desc())
    else:
        # À score égal, la France passe devant la Suisse (règle de tri du cahier des charges)
        statement = statement.order_by(
            Job.score.desc(),
            Job.country.asc(),  # "CH" < "FR" en ASCII, donc on inverse plus bas
            Job.published_at.desc(),
        )
    if q and q.strip():
        # Recherche insensible aux accents, sur l'intitule, la societe et la ville.
        # Tous les mots doivent etre presents : "technicien geneve".
        mots = normalize(q).split()
        candidats = session.exec(statement).all()
        matching = [
            j for j in candidats
            if all(m in normalize(f"{j.title} {j.company or ''} {j.location or ''}") for m in mots)
        ]
        total = len(matching)
        jobs = matching[offset:offset + limit]
    else:
        # Nombre total de résultats, indépendamment de la pagination
        total = session.exec(select(func.count()).select_from(statement.order_by(None).subquery())).one()
        jobs = session.exec(statement.offset(offset).limit(limit)).all()

    if not by_applied:
        # Tri final en Python : France d'abord à score égal
        jobs = sorted(jobs, key=lambda j: (-j.score, 0 if j.country == "FR" else 1))

    # On expose le nom de la source pour l'affichage des badges
    sources = {s.id: s.name for s in session.exec(select(Source)).all()}
    items = []
    for job in jobs:
        data = job.model_dump()
        data["source_name"] = sources.get(job.source_id, "inconnue")
        data["has_draft"] = job.status != JobStatus.NEW
        items.append(data)

    return {"count": len(items), "total": total, "items": items}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)) -> Job:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    return job


@router.post("/jobs/refresh")
async def refresh_jobs(
    source: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    """Déclenche une collecte. Sans paramètre, lance toutes les sources."""
    connectors = {
        "france_travail": FranceTravailConnector,
        "adzuna_fr": lambda: AdzunaConnector(country="fr"),
        "adzuna_ch": lambda: AdzunaConnector(country="ch"),
        "imap_alerts": ImapAlertsConnector,
    }

    if source:
        if source not in connectors:
            raise HTTPException(status_code=404, detail=f"Connecteur inconnu : {source}")
        selected = {source: connectors[source]}
    else:
        selected = connectors

    results = {}
    for name, factory in selected.items():
        results[name] = await run_connector(session, factory())

    return {"inserted": results, "total": sum(results.values())}


@router.get("/sources")
def list_sources(session: Session = Depends(get_session)) -> list[Source]:
    """État des connecteurs : dernière exécution, statut, volume."""
    return session.exec(select(Source)).all()


@router.post("/admin/backfill")
def run_backfill(session: Session = Depends(get_session)) -> dict:
    """Recalcule zone et métier sur les offres déjà collectées."""
    return backfill_zones_et_metiers(session)


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict:
    """Répartition des offres par zone, métier, source et statut."""
    jobs = session.exec(select(Job)).all()
    sources = {s.id: s.name for s in session.exec(select(Source)).all()}

    def count_by(key) -> dict:
        result: dict = {}
        for job in jobs:
            value = key(job)
            result[value] = result.get(value, 0) + 1
        return dict(sorted(result.items(), key=lambda kv: -kv[1]))

    zone_of = lambda j: j.zone.value if hasattr(j.zone, "value") else j.zone  # noqa: E731
    open_jobs = [j for j in jobs if j.status not in CLOSED_STATUSES]
    open_par_zone: dict = {}
    for job in open_jobs:
        open_par_zone[zone_of(job)] = open_par_zone.get(zone_of(job), 0) + 1

    return {
        "total": len(jobs),
        # Offres encore à traiter : ce que montrent les onglets France / Suisse / Toutes
        "open_total": len(open_jobs),
        "open_par_zone": open_par_zone,
        # Seuil de relance unique, lu par le frontend plutôt que recopié
        "relance_apres_jours": RELANCE_APRES_JOURS,
        "par_zone": count_by(zone_of),
        "par_metier": count_by(lambda j: j.metier.value if hasattr(j.metier, "value") else j.metier),
        "par_source": count_by(lambda j: sources.get(j.source_id, "?")),
        "par_statut": count_by(lambda j: j.status.value if hasattr(j.status, "value") else j.status),
    }


@router.post("/admin/score")
def run_scoring(only_new: bool = False, session: Session = Depends(get_session)) -> dict:
    """Recalcule le score de toutes les offres."""
    return score_all(session, only_new=only_new)


@router.post("/jobs/{job_id}/generate")
def generate_for_job(
    job_id: int,
    force: bool = False,
    session: Session = Depends(get_session),
) -> Application:
    """(Re)génère le brouillon de candidature d'une offre."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")

    try:
        return generate_application(session, job, force=force)
    except GeneratorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/applications")
def list_applications(job_id: int, session: Session = Depends(get_session)) -> list[Application]:
    """Historique des brouillons d'une offre, le plus récent d'abord."""
    return session.exec(
        select(Application)
        .where(Application.job_id == job_id)
        .order_by(Application.version.desc())
    ).all()


@router.post("/admin/generate-batch")
def run_generate_batch(
    min_score: int = 60,
    limit: int = Query(default=5, le=20),
    session: Session = Depends(get_session),
) -> dict:
    """Génère les candidatures des meilleures offres non encore traitées."""
    return generate_batch(session, min_score=min_score, limit=limit)


class DraftUpdate(BaseModel):
    """Corrections apportees par l'utilisateur a un brouillon."""

    subject: str
    cover_letter: str


class SendRequest(BaseModel):
    to_email: str | None = None
    attachment_ids: list[int] = []


@router.patch("/jobs/{job_id}/status")
def set_status(
    job_id: int,
    status: JobStatus,
    session: Session = Depends(get_session),
) -> Job:
    """Change le statut d'une offre : postule, relance, entretien, refuse."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    return update_status(session, job, status)


@router.put("/jobs/{job_id}/draft")
def save_draft(
    job_id: int,
    payload: DraftUpdate,
    session: Session = Depends(get_session),
) -> Application:
    """Enregistre les corrections manuelles du brouillon (nouvelle version)."""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Offre introuvable")
    try:
        return update_draft(session, job_id, payload.subject, payload.cover_letter)
    except TrackerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/send")
def send_job_application(
    job_id: int,
    payload: SendRequest | None = None,
    session: Session = Depends(get_session),
) -> dict:
    """Envoie la candidature par mail. Declenche par l'utilisateur uniquement :
    cette route n'est appelee que par le bouton "Envoyer par mail", apres confirmation.
    """
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    payload = payload or SendRequest()
    try:
        return send_application(session, job, payload.to_email, payload.attachment_ids)
    except TrackerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/detect-contact")
def detect_contact(job_id: int, session: Session = Depends(get_session)) -> dict:
    """Cherche une adresse publiée dans le texte de l'annonce, et seulement là.

    Aucune adresse n'est devinée : si l'annonce n'en contient pas, on le dit.
    """
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")

    if is_valid_email(job.contact_email):
        return {"found": True, "contact_email": job.contact_email, "source": "existing"}

    email = extract_contact_email(job.description)
    if not email:
        # On ne touche pas à contact_email : il peut contenir la consigne de la source
        return {"found": False, "contact_email": None, "source": None}

    job.contact_email = email
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    return {"found": True, "contact_email": email, "source": "description"}


@router.get("/relances")
def list_relances(session: Session = Depends(get_session)) -> list[dict]:
    """Offres postulees depuis plus de RELANCE_APRES_JOURS jours sans reponse.

    La selection reste celle de jobs_a_relancer ; on y ajoute seulement
    les champs necessaires a l'affichage des cartes.
    """
    sources = {s.id: s.name for s in session.exec(select(Source)).all()}
    resultats = []
    for relance in jobs_a_relancer(session):
        job = session.get(Job, relance["job_id"])
        data = job.model_dump()
        data["source_name"] = sources.get(job.source_id, "inconnue")
        resultats.append({**data, **relance})
    return resultats


@router.get("/analytics")
def get_analytics(session: Session = Depends(get_session)) -> dict:
    """Metriques de suivi : entonnoir, sources, activite hebdomadaire."""
    return analytics(session)


@router.get("/jobs/{job_id}/history")
def get_history(job_id: int, session: Session = Depends(get_session)) -> list[dict]:
    """Chronologie des changements de statut d'une offre."""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Offre introuvable")
    return historique_offre(session, job_id)


# --- Pièces jointes (CV, lettres...) ---


class AttachmentUpdate(BaseModel):
    """Sans valeur explicite, is_default est inversé."""

    is_default: bool | None = None


@router.get("/attachments")
def list_attachments(session: Session = Depends(get_session)) -> list[Attachment]:
    """Documents téléversés, les plus récents d'abord."""
    return session.exec(select(Attachment).order_by(Attachment.created_at.desc())).all()


@router.post("/attachments", status_code=201)
async def upload_attachment(file: UploadFile, session: Session = Depends(get_session)) -> Attachment:
    """Téléverse un document (5 Mo max, pdf/docx/odt/png/jpg)."""
    try:
        return await save_upload(session, file)
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.patch("/attachments/{attachment_id}")
def update_attachment(
    attachment_id: int,
    payload: AttachmentUpdate | None = None,
    session: Session = Depends(get_session),
) -> Attachment:
    """Marque (ou démarque) un document comme joint par défaut aux candidatures."""
    attachment = session.get(Attachment, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Document introuvable")
    if payload is None or payload.is_default is None:
        attachment.is_default = not attachment.is_default
    else:
        attachment.is_default = payload.is_default
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    return attachment


@router.delete("/attachments/{attachment_id}", status_code=204)
def remove_attachment(attachment_id: int, session: Session = Depends(get_session)) -> None:
    """Supprime le document et son fichier."""
    attachment = session.get(Attachment, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Document introuvable")
    delete_attachment(session, attachment)
