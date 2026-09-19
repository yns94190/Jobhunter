from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlmodel import Session, select

from app.config import settings
from app.connectors.france_travail import FranceTravailConnector
from app.db import get_session
from app.models import Job, Source
from app.services.ingest import run_connector

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

    statement = statement.order_by(Job.score.desc(), Job.published_at.desc())
    jobs = session.exec(statement.offset(offset).limit(limit)).all()

    return {"count": len(jobs), "items": jobs}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)) -> Job:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Offre introuvable")
    return job


@router.post("/jobs/refresh")
async def refresh_jobs(session: Session = Depends(get_session)) -> dict:
    """Déclenche une collecte sur les sources disponibles."""
    inserted = await run_connector(session, FranceTravailConnector())
    return {"inserted": inserted}


@router.get("/sources")
def list_sources(session: Session = Depends(get_session)) -> list[Source]:
    """État des connecteurs : dernière exécution, statut, volume."""
    return session.exec(select(Source)).all()