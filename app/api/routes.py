from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlmodel import Session

from app.config import settings
from app.db import get_session

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