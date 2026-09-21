from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session

from app.config import settings
from app.connectors.adzuna import AdzunaConnector
from app.connectors.france_travail import FranceTravailConnector
from app.connectors.imap_alerts import ImapAlertsConnector
from app.db import engine
from app.services.ingest import run_connector
from app.services.scoring import score_all

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Paris")


async def collect_all() -> dict:
    """Collecte toutes les sources puis score les nouvelles offres."""
    connectors = [
        FranceTravailConnector(),
        AdzunaConnector(country="fr"),
        AdzunaConnector(country="ch"),
        ImapAlertsConnector(),
    ]
    results = {}
    with Session(engine) as session:
        for connector in connectors:
            # Un connecteur en panne ne bloque pas les suivants (gere dans run_connector)
            results[connector.name] = await run_connector(session, connector)
        scoring = score_all(session, only_new=True)

    logger.info("Collecte planifiee terminee : %s | %s", results, scoring)
    return results


def start_scheduler() -> None:
    if not settings.scheduler_enabled:
        logger.info("Scheduler desactive (SCHEDULER_ENABLED=false)")
        return

    scheduler.add_job(
        collect_all,
        "interval",
        hours=settings.scheduler_interval_hours,
        id="collect_all",
        max_instances=1,      # jamais deux collectes en parallele
        coalesce=True,        # si des executions ont ete manquees, une seule rattrape
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler demarre : collecte toutes les %sh", settings.scheduler_interval_hours)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
