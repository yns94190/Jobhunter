from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class StatusHistory(SQLModel, table=True):
    """Trace chaque changement de statut d'une offre."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    from_status: str | None = None
    to_status: str
    changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    note: str | None = None
