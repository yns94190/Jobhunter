from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from dateutil import parser as date_parser

from app.config import settings
from app.connectors.base import BaseConnector, JobDTO

logger = logging.getLogger(__name__)

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
API_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"
USER_AGENT = "JobHunter/0.1 (projet personnel de recherche d'emploi)"

# Départements ciblés : Île-de-France (P1) + Haute-Savoie et Ain (P2)
DEPARTEMENTS = ["75", "77", "78", "91", "92", "93", "94", "95", "74", "01"]

# Mots-clés par métier ciblé
KEYWORDS = [
    "technicien support informatique",
    "technicien helpdesk",
    "technicien de proximite",
    "administrateur systeme",
    "technicien reseau",
    "chauffeur livreur",
]

PAGE_SIZE = 50      # maximum autorisé par l'API
MAX_PAGES = 4       # garde-fou : 200 offres par couple mot-clé/département
RATE_LIMIT_S = 2.0  # 1 requête toutes les 2 secondes


class FranceTravailConnector(BaseConnector):
    """Connecteur de l'API Offres d'emploi v2 (OAuth client_credentials)."""

    name = "france_travail"

    _UNSET = object()

    def __init__(self, client_id=_UNSET, client_secret=_UNSET):
        # _UNSET distingue "argument non fourni" (→ .env) de "None explicite" (→ pas de clé)
        self.client_id = settings.france_travail_client_id if client_id is self._UNSET else client_id
        self.client_secret = (
            settings.france_travail_client_secret if client_secret is self._UNSET else client_secret
        )
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    # --- Authentification ---

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        """Récupère un token, ou réutilise celui en cache s'il est encore valide."""
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token

        response = await client.post(
            TOKEN_URL,
            params={"realm": "/partenaire"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()

        self._token = payload["access_token"]
        # On retire 60 s de marge pour ne jamais utiliser un token expiré
        self._token_expires_at = now + timedelta(seconds=payload.get("expires_in", 1500) - 60)
        return self._token

    # --- Collecte ---

    async def fetch(self) -> list[JobDTO]:
        if not self.client_id or not self.client_secret:
            logger.warning("France Travail : identifiants absents, collecte ignoree")
            return []

        jobs: list[JobDTO] = []
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
            token = await self._get_token(client)

            for keyword in KEYWORDS:
                for departement in DEPARTEMENTS:
                    jobs.extend(await self._search(client, token, keyword, departement))

        logger.info("France Travail : %d offres collectees", len(jobs))
        return jobs

    async def _search(
        self,
        client: httpx.AsyncClient,
        token: str,
        keyword: str,
        departement: str,
    ) -> list[JobDTO]:
        """Parcourt les pages de résultats pour un couple mot-clé / département."""
        results: list[JobDTO] = []

        for page in range(MAX_PAGES):
            start = page * PAGE_SIZE
            end = start + PAGE_SIZE - 1

            try:
                response = await client.get(
                    API_URL,
                    params={
                        "motsCles": keyword,
                        "departement": departement,
                        "range": f"{start}-{end}",
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                logger.warning("France Travail : erreur reseau (%s/%s) : %s", keyword, departement, exc)
                break

            # 204 = aucun résultat, 206 = résultat partiel (pagination normale)
            if response.status_code == 204:
                break
            if response.status_code == 429:
                logger.warning("France Travail : quota atteint, pause de 10 s")
                await asyncio.sleep(10)
                continue
            if response.status_code not in (200, 206):
                logger.warning("France Travail : statut %s inattendu", response.status_code)
                break

            offres = response.json().get("resultats", [])
            if not offres:
                break

            results.extend(self._to_dto(offre) for offre in offres)

            if len(offres) < PAGE_SIZE:
                break  # dernière page

            await asyncio.sleep(RATE_LIMIT_S)

        return results

    # --- Normalisation ---

    def _to_dto(self, offre: dict) -> JobDTO:
        """Traduit une offre brute de l'API en JobDTO."""
        lieu = offre.get("lieuTravail") or {}
        entreprise = offre.get("entreprise") or {}
        contact = offre.get("contact") or {}
        origine = offre.get("origineOffre") or {}

        published_at = None
        if raw_date := offre.get("dateCreation"):
            try:
                published_at = date_parser.isoparse(raw_date)
            except (ValueError, TypeError):
                pass

        offre_id = offre.get("id", "")
        url = origine.get("urlOrigine") or f"https://candidat.francetravail.fr/offres/recherche/detail/{offre_id}"

        return JobDTO(
            source_name=self.name,
            external_id=offre_id,
            title=offre.get("intitule", ""),
            url=url,
            company=entreprise.get("nom"),
            location=lieu.get("libelle"),
            country="FR",
            contract_type=offre.get("typeContrat"),
            description=offre.get("description"),
            contact_email=contact.get("courriel"),
            published_at=published_at,
            raw=offre,
        )