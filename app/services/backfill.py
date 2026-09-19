from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.models import Job
from app.services.normalize import detect_country, detect_metier, detect_zone

logger = logging.getLogger(__name__)


def backfill_zones_et_metiers(session: Session) -> dict:
    """Recalcule pays, zone et métier sur toutes les offres déjà en base."""
    jobs = session.exec(select(Job)).all()
    updated = 0

    for job in jobs:
        country = detect_country(job.location, hint=None)
        zone = detect_zone(job.location, country, job.description)
        metier = detect_metier(job.title, job.description)

        if (job.country, job.zone, job.metier) != (country, zone, metier):
            job.country = country
            job.zone = zone
            job.metier = metier
            session.add(job)
            updated += 1

    session.commit()
    logger.info("Backfill : %d offres mises a jour sur %d", updated, len(jobs))
    return {"total": len(jobs), "updated": updated}