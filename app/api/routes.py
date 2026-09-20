from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlmodel import Session, select

from app.config import settings
from app.connectors.france_travail import FranceTravailConnector
from app.db import get_session
from app.models import Job, JobStatus, Metier, Source, Zone
from app.services.ingest import run_connector
from app.services.backfill import backfill_zones_et_metiers
from app.connectors.adzuna import AdzunaConnector
from app.connectors.imap_alerts import ImapAlertsConnector
from app.services.analytics import analytics, historique_offre
from app.services.analytics import analytics, historique_offre
from app.services.scoring import score_all
from app.services.tracker import (
    TrackerError,
    jobs_a_relancer,
    send_application,
    update_draft,
    update_status,
)
from pydantic import BaseModel
from app.services.tracker import (
    TrackerError,
    jobs_a_relancer,
    send_application,
    update_draft,
    update_status,
)
from pydantic import BaseModel
from app.models import Application
from app.services.generator import GeneratorError, generate_application, generate_batch
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
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    """Liste les offres, filtrées et paginées."""
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
        statement = statement.where(Job.status == status)
    if country:
        statement = statement.where(Job.country == country)

    # À score égal, la France passe devant la Suisse (règle de tri du cahier des charges)
    statement = statement.order_by(
        Job.score.desc(),
        Job.country.asc(),  # "CH" < "FR" en ASCII, donc on inverse plus bas
        Job.published_at.desc(),
    )
    jobs = session.exec(statement.offset(offset).limit(limit)).all()

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

    return {"count": len(items), "items": items}


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

    return {
        "total": len(jobs),
        "par_zone": count_by(lambda j: j.zone.value if hasattr(j.zone, "value") else j.zone),
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
    """Envoie la candidature par mail. Declenche par l'utilisateur uniquement."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    try:
        return send_application(session, job, payload.to_email if payload else None)
    except TrackerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/relances")
def list_relances(session: Session = Depends(get_session)) -> list[dict]:
    """Offres postulees depuis plus de 10 jours sans reponse."""
    return jobs_a_relancer(session)


class DraftUpdate(BaseModel):
    """Corrections apportees par l'utilisateur a un brouillon."""

    subject: str
    cover_letter: str


class SendRequest(BaseModel):
    to_email: str | None = None


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
    """Envoie la candidature par mail. Declenche par l'utilisateur uniquement."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    try:
        return send_application(session, job, payload.to_email if payload else None)
    except TrackerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/relances")
def list_relances(session: Session = Depends(get_session)) -> list[dict]:
    """Offres postulees depuis plus de 10 jours sans reponse."""
    return jobs_a_relancer(session)


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
