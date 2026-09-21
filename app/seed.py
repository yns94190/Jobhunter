from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models import Profile, Source, SourceKind
from app.profile_config import format_experience, load_profile

DEFAULT_SOURCES = [
    ("france_travail", SourceKind.API),
    ("adzuna_fr", SourceKind.API),
    ("adzuna_ch", SourceKind.API),
    ("jobup", SourceKind.SCRAPE),
    ("imap_alerts", SourceKind.IMAP),
]


def sync_profile(session: Session) -> Profile:
    """Aligne le profil en base sur profile.yaml, qui fait foi."""
    data = load_profile()
    identite = data.get("identite") or {}

    profile = session.exec(select(Profile)).first() or Profile(full_name=identite.get("nom", "Candidat"))
    profile.full_name = identite.get("nom", profile.full_name)
    profile.age = identite.get("age")
    profile.location = identite.get("localisation")
    profile.email = identite.get("email")
    profile.phone = str(identite["telephone"]) if identite.get("telephone") else None
    profile.education = data.get("formation")
    profile.skills = list(data.get("competences") or [])
    profile.licences = list(data.get("permis_et_mobilite") or [])
    profile.sample_letters = [format_experience(e) for e in data.get("experiences") or []]
    profile.updated_at = datetime.now(timezone.utc)
    session.add(profile)
    return profile


def seed(session: Session) -> None:
    """Synchronise le profil et cree les sources manquantes. Idempotent."""
    sync_profile(session)

    existing = {s.name for s in session.exec(select(Source)).all()}
    for name, kind in DEFAULT_SOURCES:
        if name not in existing:
            session.add(Source(name=name, kind=kind))

    session.commit()
