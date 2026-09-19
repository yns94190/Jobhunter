from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Zone(str, Enum):
    """Zones de priorité géographique (cf. scoring)."""

    P1_IDF = "P1_IDF"
    P2_FRONTALIER = "P2_FRONTALIER"
    P3_FRANCE = "P3_FRANCE"
    P4_SUISSE = "P4_SUISSE"
    UNKNOWN = "UNKNOWN"


class Metier(str, Enum):
    SUPPORT = "support"          # helpdesk N1/N2
    LIVREUR = "livreur"          # chauffeur-livreur VL
    POLYVALENT = "polyvalent"    # réseau, sysadmin junior, proximité
    AUTRE = "autre"


class JobStatus(str, Enum):
    NEW = "new"
    DRAFTED = "drafted"        # brouillon de candidature généré
    APPLIED = "applied"
    FOLLOWED_UP = "followed_up"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    IGNORED = "ignored"


class SourceKind(str, Enum):
    API = "api"
    SCRAPE = "scrape"
    IMAP = "imap"


class Source(SQLModel, table=True):
    """Un connecteur de collecte et son état d'exécution."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)       # ex: "france_travail"
    kind: SourceKind = SourceKind.API
    enabled: bool = True
    last_run_at: datetime | None = None
    last_status: str | None = None                   # "ok" / message d'erreur
    last_job_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    """Une offre d'emploi normalisée, toutes sources confondues."""

    __table_args__ = (UniqueConstraint("dedup_hash", name="uq_job_dedup_hash"),)

    id: int | None = Field(default=None, primary_key=True)
    source_id: int | None = Field(default=None, foreign_key="source.id", index=True)
    external_id: str | None = Field(default=None, index=True)  # id chez la source

    title: str
    title_normalized: str = Field(index=True)   # minuscules, sans accents (dedup)
    company: str | None = None
    location: str | None = None
    country: str = "FR"                         # "FR" ou "CH"
    zone: Zone = Field(default=Zone.UNKNOWN, index=True)
    metier: Metier = Field(default=Metier.AUTRE, index=True)
    contract_type: str | None = None            # CDI / CDD / alternance / interim
    description: str | None = None
    url: str
    contact_email: str | None = None            # active le bouton "Envoyer par mail"

    published_at: datetime | None = Field(default=None, index=True)
    fetched_at: datetime = Field(default_factory=utcnow)

    dedup_hash: str = Field(index=True)
    score: int = Field(default=0, index=True)
    score_detail: dict = Field(default_factory=dict, sa_column=Column(JSON))

    status: JobStatus = Field(default=JobStatus.NEW, index=True)
    applied_at: datetime | None = None          # sert aux relances > 10 jours
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Application(SQLModel, table=True):
    """Un brouillon de candidature, versionné par offre."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    version: int = 1
    is_current: bool = Field(default=True, index=True)

    subject: str | None = None                  # objet_mail
    cover_letter: str | None = None             # lettre_motivation
    key_points: list = Field(default_factory=list, sa_column=Column(JSON))
    confidence_score: float | None = None
    model: str | None = None                    # modèle utilisé
    variant: str = "FR"                         # "FR" ou "CH" (format suisse)

    created_at: datetime = Field(default_factory=utcnow)
    sent_at: datetime | None = None


class Profile(SQLModel, table=True):
    """Mon profil — utilisé pour le scoring et la génération de lettres."""

    id: int | None = Field(default=None, primary_key=True)
    full_name: str
    age: int | None = None
    location: str | None = None
    email: str | None = None
    phone: str | None = None
    education: str | None = None
    skills: list = Field(default_factory=list, sa_column=Column(JSON))
    licences: list = Field(default_factory=list, sa_column=Column(JSON))
    sample_letters: list = Field(default_factory=list, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)