"""Extraction d'une adresse de contact publiée dans le texte de l'annonce.

Règle du projet : on ne retient QUE des adresses écrites dans l'annonce.
Aucune adresse n'est devinée ou construite par motif (prenom.nom@, rh@domaine...),
aucun service d'enrichissement ni aucun site tiers n'est consulté : une adresse
inventée produit des rebonds qui font sanctionner le compte d'envoi.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models import Job

logger = logging.getLogger(__name__)

# Volontairement strict : partie locale classique, domaine à labels, extension alphabétique
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])"                       # pas collé à un mot (évite "x@y" dans une URL)
    r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,24}"
    r"(?![\w-])"
)

# Adresses techniques : personne ne lit ces boîtes
EXCLUDED_LOCAL_PARTS = {
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "ne-pas-repondre", "nepasrepondre", "postmaster", "webmaster", "abuse", "mailer-daemon",
}

# Domaines des plateformes elles-mêmes : écrire à Indeed ne joint pas le recruteur
PLATFORM_LABELS = {"indeed", "linkedin", "adzuna", "jobup", "francetravail", "pole-emploi"}
PLATFORM_DOMAINS = {"jobs.ch"}

# Extensions de fichiers qui ressemblent à un domaine : "logo@2x.png"
FILE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "pdf"}

# Préfixes typiques d'une boîte de recrutement, testés sur le premier segment
# de la partie locale ("rh", "rh.paris", "recrutement-idf"...)
RECRUITMENT_PREFIXES = {
    "rh", "hr", "recrutement", "recrutements", "recruitment", "jobs", "job",
    "carriere", "carrieres", "careers", "candidature", "candidatures", "emploi", "emplois",
}


def is_valid_email(value: str | None) -> bool:
    """Vrai si la valeur est exactement une adresse (et pas une phrase qui en contient)."""
    return bool(value) and EMAIL_PATTERN.fullmatch(value.strip()) is not None


def _is_excluded(email: str) -> bool:
    local, _, domain = email.lower().partition("@")
    if local in EXCLUDED_LOCAL_PARTS or local.startswith(("noreply", "no-reply")):
        return True
    labels = domain.split(".")
    if labels[-1] in FILE_EXTENSIONS:
        return True
    if any(label in PLATFORM_LABELS for label in labels):
        return True
    return any(domain == d or domain.endswith("." + d) for d in PLATFORM_DOMAINS)


def _is_recruitment_address(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    first_segment = re.split(r"[._+-]", local, maxsplit=1)[0]
    return first_segment in RECRUITMENT_PREFIXES


def find_emails(text: str | None) -> list[str]:
    """Adresses publiées dans le texte, sans doublon, filtrées, dans l'ordre d'apparition."""
    if not text:
        return []
    seen: dict[str, str] = {}
    for match in EMAIL_PATTERN.finditer(text):
        # Le domaine est insensible à la casse : "Hotmail.Fr" -> "hotmail.fr"
        local, _, domain = match.group().partition("@")
        email = f"{local}@{domain.lower()}"
        if email.lower() not in seen and not _is_excluded(email):
            seen[email.lower()] = email
    return list(seen.values())


def extract_contact_email(text: str | None) -> str | None:
    """Meilleure adresse de contact de l'annonce, ou None.

    Priorité à une boîte de recrutement (rh@, recrutement@, jobs@...),
    à défaut la première adresse valide trouvée.
    """
    emails = find_emails(text)
    if not emails:
        return None
    return next((e for e in emails if _is_recruitment_address(e)), emails[0])


def resolve_contact(job: Job) -> str | None:
    """Adresse à enregistrer pour l'offre, ou None s'il n'y a rien à changer.

    Une adresse déjà valide n'est jamais remplacée. Sans adresse dans l'annonce,
    contact_email reste tel quel : il peut contenir la consigne de la source.
    """
    if is_valid_email(job.contact_email):
        return None
    return extract_contact_email(job.description)


def detect_all_contacts(session: Session, dry_run: bool = False) -> dict:
    """Cherche une adresse publiée dans chaque annonce qui n'en a pas encore.

    dry_run=True : calcule le résultat sans rien écrire en base.
    """
    jobs = session.exec(select(Job)).all()
    examined = 0
    updated = []
    now = datetime.now(timezone.utc)

    for job in jobs:
        if is_valid_email(job.contact_email):
            continue
        examined += 1
        email = resolve_contact(job)
        if not email:
            continue
        updated.append({"job_id": job.id, "title": job.title, "contact_email": email})
        if not dry_run:
            job.contact_email = email
            job.updated_at = now
            session.add(job)

    if not dry_run and updated:
        session.commit()

    logger.info(
        "Recherche d'adresses : %d offres sans adresse, %d trouvees%s",
        examined, len(updated), " (simulation)" if dry_run else "",
    )
    return {
        "dry_run": dry_run,
        "already_valid": len(jobs) - examined,
        "examined": examined,
        "found": len(updated),
        "not_found": examined - len(updated),
        "updated": updated,
    }
