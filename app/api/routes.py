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

    return {"count": len(jobs), "items": jobs}


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