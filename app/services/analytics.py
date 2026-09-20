from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models import Job, JobStatus, Source
from app.models_history import StatusHistory


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def funnel(session: Session) -> list[dict]:
    """Entonnoir de conversion : ou les candidatures s'arretent."""
    jobs = session.exec(select(Job)).all()
    total = len(jobs)
    interessantes = sum(1 for j in jobs for s in [j.score] if s >= 60)

    def compte(*statuts) -> int:
        return sum(1 for j in jobs if (j.status.value if hasattr(j.status, "value") else j.status) in statuts)

    # Un statut avance implique d'avoir franchi les precedents
    postulees = compte("applied", "followed_up", "interview", "rejected")
    entretiens = compte("interview")

    etapes = [
        {"etape": "Collectées", "valeur": total},
        {"etape": "Pertinentes (≥60)", "valeur": interessantes},
        {"etape": "Brouillon prêt", "valeur": compte("drafted") + postulees},
        {"etape": "Postulées", "valeur": postulees},
        {"etape": "Entretiens", "valeur": entretiens},
    ]

    for etape in etapes:
        etape["taux"] = round(100 * etape["valeur"] / total, 1) if total else 0.0

    return etapes


def par_source(session: Session) -> list[dict]:
    """Performance de chaque source : volume, qualite, resultats."""
    sources = {s.id: s.name for s in session.exec(select(Source)).all()}
    jobs = session.exec(select(Job)).all()

    agrege: dict[str, dict] = defaultdict(lambda: {"offres": 0, "pertinentes": 0, "postulees": 0, "entretiens": 0, "scores": []})

    for job in jobs:
        nom = sources.get(job.source_id, "inconnue")
        bucket = agrege[nom]
        statut = job.status.value if hasattr(job.status, "value") else job.status

        bucket["offres"] += 1
        bucket["scores"].append(job.score)
        if job.score >= 60:
            bucket["pertinentes"] += 1
        if statut in ("applied", "followed_up", "interview", "rejected"):
            bucket["postulees"] += 1
        if statut == "interview":
            bucket["entretiens"] += 1

    resultats = []
    for nom, b in agrege.items():
        scores = b.pop("scores")
        b["source"] = nom
        b["score_moyen"] = round(sum(scores) / len(scores), 1) if scores else 0
        b["taux_pertinence"] = round(100 * b["pertinentes"] / b["offres"], 1) if b["offres"] else 0
        resultats.append(b)

    return sorted(resultats, key=lambda r: -r["offres"])


def activite_hebdo(session: Session, semaines: int = 8) -> list[dict]:
    """Rythme de candidature, semaine par semaine."""
    debut = datetime.now(timezone.utc) - timedelta(weeks=semaines)
    historique = session.exec(select(StatusHistory)).all()

    buckets: dict[str, dict] = {}
    for i in range(semaines):
        jour = (datetime.now(timezone.utc) - timedelta(weeks=semaines - 1 - i))
        lundi = jour - timedelta(days=jour.weekday())
        buckets[lundi.strftime("%d/%m")] = {"semaine": lundi.strftime("%d/%m"), "postulees": 0, "entretiens": 0}

    for entree in historique:
        quand = _as_utc(entree.changed_at)
        if not quand or quand < debut:
            continue
        lundi = quand - timedelta(days=quand.weekday())
        cle = lundi.strftime("%d/%m")
        if cle not in buckets:
            continue
        if entree.to_status == "applied":
            buckets[cle]["postulees"] += 1
        elif entree.to_status == "interview":
            buckets[cle]["entretiens"] += 1

    return list(buckets.values())


def historique_offre(session: Session, job_id: int) -> list[dict]:
    """Chronologie des changements de statut d'une offre."""
    entrees = session.exec(
        select(StatusHistory).where(StatusHistory.job_id == job_id).order_by(StatusHistory.changed_at.desc())
    ).all()
    return [
        {
            "de": e.from_status,
            "vers": e.to_status,
            "quand": e.changed_at,
            "note": e.note,
        }
        for e in entrees
    ]


def analytics(session: Session) -> dict:
    """Toutes les metriques, en un appel."""
    jobs = session.exec(select(Job)).all()

    par_zone: dict[str, int] = defaultdict(int)
    par_metier: dict[str, int] = defaultdict(int)
    for job in jobs:
        par_zone[job.zone.value if hasattr(job.zone, "value") else str(job.zone)] += 1
        par_metier[job.metier.value if hasattr(job.metier, "value") else str(job.metier)] += 1

    return {
        "funnel": funnel(session),
        "par_source": par_source(session),
        "activite": activite_hebdo(session),
        "par_zone": dict(sorted(par_zone.items(), key=lambda kv: -kv[1])),
        "par_metier": dict(sorted(par_metier.items(), key=lambda kv: -kv[1])),
    }
