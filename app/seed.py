from sqlmodel import Session, select

from app.models import Profile, Source, SourceKind

DEFAULT_SOURCES = [
    ("france_travail", SourceKind.API),
    ("adzuna_fr", SourceKind.API),
    ("adzuna_ch", SourceKind.API),
    ("jobup", SourceKind.SCRAPE),
    ("imap_alerts", SourceKind.IMAP),
]


def seed(session: Session) -> None:
    """Insère le profil et les sources s'ils n'existent pas déjà."""
    if not session.exec(select(Profile)).first():
        session.add(
            Profile(
                full_name="Yanis Flitti",
                age=20,
                location="Île-de-France",
                education="BTS SIO option SISR (Bac+2, 2026) — Bac Pro Systèmes Numériques, mention Bien",
                skills=[
                    "support informatique N1/N2",
                    "Windows",
                    "Active Directory",
                    "réseau",
                    "ticketing",
                ],
                licences=["Permis B", "Permis A2"],
            )
        )

    existing = {s.name for s in session.exec(select(Source)).all()}
    for name, kind in DEFAULT_SOURCES:
        if name not in existing:
            session.add(Source(name=name, kind=kind))

    session.commit()