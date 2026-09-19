from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class JobDTO:
    """Offre normalisée, produite par tous les connecteurs.

    C'est le format pivot : chaque source traduit sa réponse brute
    en JobDTO, et seul ce format circule dans le reste de l'application.
    """

    source_name: str
    external_id: str | None
    title: str
    url: str
    company: str | None = None
    location: str | None = None
    country: str = "FR"
    contract_type: str | None = None
    description: str | None = None
    contact_email: str | None = None
    published_at: datetime | None = None
    raw: dict = field(default_factory=dict)  # réponse brute, utile au debug


class BaseConnector(ABC):
    """Interface commune à tous les connecteurs."""

    name: str

    @abstractmethod
    async def fetch(self) -> list[JobDTO]:
        """Récupère les offres de la source."""
        raise NotImplementedError