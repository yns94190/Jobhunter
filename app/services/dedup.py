from __future__ import annotations

import hashlib
import re
import unicodedata

from rapidfuzz import fuzz

from app.connectors.base import JobDTO

FUZZY_THRESHOLD = 90  # similarité au-delà de laquelle deux offres sont jugées identiques


def normalize(text: str | None) -> str:
    """Minuscules, sans accents, sans ponctuation, espaces réduits."""
    if not text:
        return ""
    # NFKD sépare les lettres de leurs accents, qu'on supprime ensuite
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compute_hash(title: str, company: str | None, location: str | None) -> str:
    """Empreinte stable d'une offre : titre + société + lieu, normalisés."""
    payload = "|".join([normalize(title), normalize(company), normalize(location)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def is_duplicate(dto: JobDTO, existing: list[tuple[str, str | None, str | None]]) -> bool:
    """Compare l'offre aux offres connues par similarité floue.

    `existing` : liste de (titre, société, lieu) déjà en base.
    Rattrape les variantes que le hash exact laisse passser
    ("Technicien support N1" vs "Technicien support niveau 1").
    """
    candidate = normalize(dto.title)
    candidate_company = normalize(dto.company)

    for title, company, _location in existing:
        # La société doit correspondre : deux sociétés différentes ne sont jamais des doublons
        if candidate_company and normalize(company) != candidate_company:
            continue
        if fuzz.token_sort_ratio(candidate, normalize(title)) >= FUZZY_THRESHOLD:
            return True

    return False