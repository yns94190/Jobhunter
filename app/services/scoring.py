from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml

from app.models import Job
from app.services.dedup import normalize

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/app/scoring.yaml")


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Charge les pondérations depuis le YAML. Mis en cache."""
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def reload_config() -> dict:
    """Vide le cache et relit le fichier (après modification des poids)."""
    load_config.cache_clear()
    return load_config()


def _score_mots_cles(title: str, config: dict) -> tuple[float, str]:
    """Correspondance des mots-clés dans le titre."""
    poids = config["mots_cles"]["poids"]
    haystack = normalize(title)

    for mot in config["mots_cles"]["forts"]:
        if mot in haystack:
            return poids, f"fort:{mot}"
    for mot in config["mots_cles"]["moyens"]:
        if mot in haystack:
            return poids * 0.6, f"moyen:{mot}"
    for mot in config["mots_cles"]["faibles"]:
        if mot in haystack:
            return poids * 0.3, f"faible:{mot}"

    return 0.0, "aucun"


def _score_zone(zone, config: dict) -> float:
    key = zone.value if hasattr(zone, "value") else str(zone)
    return float(config["zone"]["bonus"].get(key, 0))


def _score_contrat(contract_type: str | None, config: dict) -> float:
    if not contract_type:
        return 0.0
    # Les libellés varient selon les sources : comparaison insensible à la casse
    bonus = config["contrat"]["bonus"]
    for key, value in bonus.items():
        if key.lower() == contract_type.lower():
            return float(value)
    return 0.0


def _score_fraicheur(published_at: datetime | None, config: dict) -> float:
    """Décroissance linéaire sur la fenêtre configurée."""
    if not published_at:
        return 0.0

    poids = config["fraicheur"]["poids"]
    fenetre = config["fraicheur"]["jours"]

    # Les dates de la base sont parfois naïves : on les rend comparables
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    age_jours = (datetime.now(timezone.utc) - published_at).days
    if age_jours < 0:
        age_jours = 0
    if age_jours >= fenetre:
        return 0.0

    return poids * (1 - age_jours / fenetre)


def _score_malus(title: str, description: str | None, config: dict) -> tuple[float, list[str]]:
    """Pénalités cumulées, plafonnées."""
    haystack = f"{normalize(title)} {normalize(description)}"
    total = 0.0
    motifs: list[str] = []

    for regle in config["malus"]["regles"]:
        if regle["motif"] in haystack:
            total += regle["points"]
            motifs.append(regle["motif"])

    plafond = config["malus"]["poids_max"]
    return min(total, plafond), motifs


def compute_score(job: Job, config: dict | None = None) -> tuple[int, dict]:
    """Calcule le score 0-100 d'une offre et le détail de son calcul."""
    config = config or load_config()

    mots_cles, mot_trouve = _score_mots_cles(job.title, config)
    zone = _score_zone(job.zone, config)
    contrat = _score_contrat(job.contract_type, config)
    fraicheur = _score_fraicheur(job.published_at, config)
    malus, motifs_malus = _score_malus(job.title, job.description, config)

    total = mots_cles + zone + contrat + fraicheur - malus
    total = max(0, min(100, round(total)))

    detail = {
        "mots_cles": round(mots_cles, 1),
        "mot_trouve": mot_trouve,
        "zone": round(zone, 1),
        "contrat": round(contrat, 1),
        "fraicheur": round(fraicheur, 1),
        "malus": round(malus, 1),
        "motifs_malus": motifs_malus,
        "total": total,
    }

    return total, detail


def score_all(session, only_new: bool = False) -> dict:
    """Applique le scoring à toutes les offres en base."""
    from sqlmodel import select

    config = reload_config()
    statement = select(Job)
    if only_new:
        statement = statement.where(Job.score == 0)

    jobs = session.exec(statement).all()
    for job in jobs:
        score, detail = compute_score(job, config)
        job.score = score
        job.score_detail = detail
        session.add(job)

    session.commit()

    au_dessus_du_seuil = sum(1 for j in jobs if j.score >= config["seuil_generation"])
    logger.info("Scoring : %d offres traitees, %d au-dessus du seuil", len(jobs), au_dessus_du_seuil)

    return {
        "traitees": len(jobs),
        "seuil": config["seuil_generation"],
        "au_dessus_du_seuil": au_dessus_du_seuil,
    }