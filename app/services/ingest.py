from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.connectors.base import BaseConnector, JobDTO
from app.models import Job, Source
from app.services.dedup import compute_hash, is_duplicate, normalize
from app.services.normalize import detect_country, detect_metier, detect_zone
logger = logging.getLogger(__name__)


def _get_or_create_source(session: Session, name: str) -> Source:
    source = session.exec(select(Source).where(Source.name == name)).first()
    if not source:
        source = Source(name=name)
        session.add(source)
        session.commit()
        session.refresh(source)
    return source


def save_jobs(session: Session, dtos: list[JobDTO], source_name: str) -> int:
    """Enregistre les offres non encore connues. Renvoie le nombre d'ajouts."""
    source = _get_or_create_source(session, source_name)

    # Empreintes déjà en base, pour écarter les doublons exacts sans requête par offre
    known_hashes = set(session.exec(select(Job.dedup_hash)).all())
    known_triples = [
        (job.title, job.company, job.location)
        for job in session.exec(select(Job)).all()
    ]

    inserted = 0
    for dto in dtos:
        dedup_hash = compute_hash(dto.title, dto.company, dto.location)

        if dedup_hash in known_hashes:
            continue
        if is_duplicate(dto, known_triples):
            continue


        country = detect_country(dto.location, hint=dto.country)

        session.add(
            Job(
                source_id=source.id,
                external_id=dto.external_id,
                title=dto.title,
                title_normalized=normalize(dto.title),
                company=dto.company,
                location=dto.location,
                country=country,
                zone=detect_zone(dto.location, country, dto.description),
                metier=detect_metier(dto.title, dto.description),
                contract_type=dto.contract_type,
                description=dto.description,
                url=dto.url,
                contact_email=dto.contact_email,
                published_at=dto.published_at,
                dedup_hash=dedup_hash,
            )
        )
        # On met à jour les références locales pour dédoublonner aussi au sein du lot
        known_hashes.add(dedup_hash)
        known_triples.append((dto.title, dto.company, dto.location))
        inserted += 1

    source.last_run_at = datetime.now(timezone.utc)
    source.last_status = "ok"
    source.last_job_count = inserted
    session.add(source)
    session.commit()

    logger.info("%s : %d nouvelles offres sur %d recues", source_name, inserted, len(dtos))
    return inserted


async def run_connector(session: Session, connector: BaseConnector) -> int:
    """Lance un connecteur et enregistre ses résultats."""
    try:
        dtos = await connector.fetch()
    except Exception as exc:  # noqa: BLE001 — un connecteur en panne ne doit pas bloquer les autres
        logger.exception("%s : echec de la collecte", connector.name)
        source = _get_or_create_source(session, connector.name)
        source.last_run_at = datetime.now(timezone.utc)
        source.last_status = f"erreur: {exc}"
        session.add(source)
        session.commit()
        return 0

    # Un connecteur peut produire des offres de plusieurs plateformes (cas de l'IMAP) :
    # chaque offre est enregistree sous sa vraie source, pour le badge et les statistiques
    groupes: dict[str, list[JobDTO]] = {}
    for dto in dtos:
        groupes.setdefault(dto.source_name or connector.name, []).append(dto)

    if not groupes:
        return save_jobs(session, [], connector.name)
    return sum(save_jobs(session, lot, nom) for nom, lot in groupes.items())