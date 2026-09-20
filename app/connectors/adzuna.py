from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx
from dateutil import parser as date_parser

from app.config import settings
from app.connectors.base import BaseConnector, JobDTO

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs"
USER_AGENT = "JobHunter/0.1 (projet personnel de recherche d'emploi)"

RESULTS_PER_PAGE = 50
MAX_PAGES = 3
RATE_LIMIT_S = 2.0

# Recherches par pays : (mot-clé, lieu de référence, rayon en km)
QUERIES_FR = [
    ("technicien support informatique", "Paris", 40),
    ("technicien helpdesk", "Paris", 40),
    ("technicien informatique", "Annemasse", 30),
    ("chauffeur livreur", "Paris", 40),
    ("administrateur systeme junior", "Paris", 40),
]

QUERIES_CH = [
    ("informatique", "Genève", 30),
    ("support", "Genève", 30),
    ("helpdesk", "Genève", 30),
    ("informatique", "Lausanne", 30),
    ("informaticien", "Nyon", 30),
]



class AdzunaConnector(BaseConnector):
    """Connecteur de l'API Adzuna (free tier). Une instance par pays."""

    _UNSET = object()

    def __init__(self, country: str = "fr", app_id=_UNSET, app_key=_UNSET):
        self.country = country.lower()
        self.name = f"adzuna_{self.country}"
        self.app_id = settings.adzuna_app_id if app_id is self._UNSET else app_id
        self.app_key = settings.adzuna_app_key if app_key is self._UNSET else app_key

    @property
    def _queries(self) -> list[tuple[str, str, int]]:
        return QUERIES_CH if self.country == "ch" else QUERIES_FR

    async def fetch(self) -> list[JobDTO]:
        if not self.app_id or not self.app_key:
            logger.warning("%s : identifiants absents, collecte ignoree", self.name)
            return []

        jobs: list[JobDTO] = []
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
            for keyword, location, distance in self._queries:
                jobs.extend(await self._search(client, keyword, location, distance))

        logger.info("%s : %d offres collectees", self.name, len(jobs))
        return jobs

    async def _search(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        location: str,
        distance: int,
    ) -> list[JobDTO]:
        results: list[JobDTO] = []

        for page in range(1, MAX_PAGES + 1):
            params = {
                "app_id": self.app_id,
                "app_key": self.app_key,
                "what": keyword,
                "results_per_page": RESULTS_PER_PAGE,
                "content-type": "application/json",
            }
            if location:
                params["where"] = location
                params["distance"] = distance

            try:
                response = await client.get(
                    f"{BASE_URL}/{self.country}/search/{page}",
                    params=params,
                )
            except httpx.HTTPError as exc:
                logger.warning("%s : erreur reseau (%s) : %s", self.name, keyword, exc)
                break

            if response.status_code == 429:
                logger.warning("%s : quota atteint, pause de 10 s", self.name)
                await asyncio.sleep(10)
                continue
            if response.status_code != 200:
                logger.warning("%s : statut %s inattendu", self.name, response.status_code)
                break

            offres = response.json().get("results", [])
            if not offres:
                break

            results.extend(self._to_dto(offre) for offre in offres)

            if len(offres) < RESULTS_PER_PAGE:
                break

            await asyncio.sleep(RATE_LIMIT_S)

        return results

    def _to_dto(self, offre: dict) -> JobDTO:
        """Traduit une offre brute Adzuna en JobDTO."""
        company = (offre.get("company") or {}).get("display_name")
        location = (offre.get("location") or {}).get("display_name")
        contract = offre.get("contract_type") or offre.get("contract_time")

        published_at: datetime | None = None
        if raw_date := offre.get("created"):
            try:
                published_at = date_parser.isoparse(raw_date)
            except (ValueError, TypeError):
                pass

        return JobDTO(
            source_name=self.name,
            external_id=str(offre.get("id", "")),
            title=offre.get("title", ""),
            url=offre.get("redirect_url", ""),
            company=company,
            location=location,
            country=self.country.upper(),
            contract_type=contract,
            description=offre.get("description"),
            published_at=published_at,
            raw=offre,
        )