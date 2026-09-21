from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup
from imap_tools import AND, MailBox

from app.config import settings
from app.connectors.base import BaseConnector, JobDTO

logger = logging.getLogger(__name__)

# Expéditeurs reconnus : (motif dans l'adresse, nom de la source)
SENDERS = {
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
    "indeedemail.com": "indeed",
    "jobup.ch": "jobup",
    "jobs.ch": "jobs_ch",
}

LOOKBACK_DAYS = 7  # on ne relit que les mails récents

# Liens à ignorer : désabonnement, réglages, profil, etc.
URL_BLACKLIST = (
    "unsubscribe", "settings", "/help", "/legal", "privacy", "psettings",
    "/mypreferences", "comm/", "/feed", "/mynetwork", "notifications",
)


def _clean_url(url: str) -> str:
    """Déroule les liens de tracking pour retrouver l'URL réelle de l'offre."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    # LinkedIn et Indeed encapsulent parfois la vraie URL dans un paramètre
    for key in ("url", "u", "targetUrl", "redirect"):
        if key in params and params[key]:
            return unquote(params[key][0])

    # Sinon on retire les paramètres de tracking, en gardant les identifiants utiles
    keep = {"currentJobId", "jk", "vjk"}
    useful = {k: v for k, v in params.items() if k in keep}
    if useful:
        query = "&".join(f"{k}={v[0]}" for k, v in useful.items())
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query}"

    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _is_job_url(url: str, source: str) -> bool:
    """Ne garde que les liens pointant vers une offre."""
    lowered = url.lower()
    if any(bad in lowered for bad in URL_BLACKLIST):
        return False

    if source == "linkedin":
        return "/jobs/view/" in lowered or "currentjobid" in lowered
    if source == "indeed":
        return "/rc/clk" in lowered or "/viewjob" in lowered or "jk=" in lowered
    return "/job" in lowered or "/emploi" in lowered or "/vacancy" in lowered


class ImapAlertsConnector(BaseConnector):
    """Lit les alertes emploi reçues par mail et en extrait les offres."""

    name = "imap_alerts"
    _UNSET = object()

    def __init__(self, host=_UNSET, user=_UNSET, password=_UNSET, folder: str = "INBOX"):
        self.host = settings.imap_host if host is self._UNSET else host
        self.user = settings.imap_user if user is self._UNSET else user
        self.password = settings.imap_password if password is self._UNSET else password
        self.folder = folder

    async def fetch(self) -> list[JobDTO]:
        if not (self.host and self.user and self.password):
            logger.warning("IMAP : identifiants absents, collecte ignoree")
            return []

        since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).date()
        jobs: list[JobDTO] = []

        try:
            with MailBox(self.host).login(self.user, self.password, initial_folder=self.folder) as mailbox:
                for message in mailbox.fetch(AND(date_gte=since), mark_seen=False):
                    source = self._detect_source(message.from_)
                    if not source:
                        continue
                    jobs.extend(self.parse_email(message.html or message.text or "", source, message.date))
        except Exception as exc:  # noqa: BLE001 — une boîte injoignable ne doit pas tout arrêter
            logger.warning("IMAP : echec de connexion ou de lecture : %s", exc)
            return []

        logger.info("IMAP : %d offres extraites", len(jobs))
        return jobs

    def _detect_source(self, sender: str) -> str | None:
        """Identifie la plateforme d'origine d'après l'expéditeur."""
        lowered = (sender or "").lower()
        for pattern, source in SENDERS.items():
            if pattern in lowered:
                return source
        return None

    def parse_email(self, html: str, source: str, received_at: datetime | None = None) -> list[JobDTO]:
        """Extrait les offres du corps HTML d'une alerte."""
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        jobs: list[JobDTO] = []
        seen_urls: set[str] = set()

        for link in soup.find_all("a", href=True):
            url = _clean_url(link["href"])
            if not _is_job_url(url, source) or url in seen_urls:
                continue

            title = " ".join(link.get_text(" ", strip=True).split())
            # Les liens image ou "Voir l'offre" n'ont pas de titre exploitable
            if len(title) < 5 or title.lower() in ("voir l offre", "postuler", "voir le poste"):
                continue

            seen_urls.add(url)
            company, location = self._extract_context(link)

            # jobup.ch et jobs.ch affichent "Societe, Ville" sur une seule ligne
            if source in ("jobup", "jobs_ch") and company is None and location and "," in location:
                company, _, location = location.rpartition(",")
                company, location = company.strip(), location.strip()

            jobs.append(
                JobDTO(
                    source_name=f"imap_{source}",
                    external_id=self._extract_id(url, source),
                    title=title,
                    url=url,
                    company=company,
                    location=location,
                    country="CH" if source in ("jobup", "jobs_ch") else "FR",
                    published_at=received_at,
                    raw={"source": source, "url": url},
                )
            )

        return jobs

    def _extract_context(self, link) -> tuple[str | None, str | None]:
        """Cherche la société et le lieu dans le texte entourant le lien."""
        container = link.find_parent(["td", "div", "tr"])
        if not container:
            return None, None

        lines = [
            " ".join(line.split())
            for line in container.get_text("\n", strip=True).split("\n")
            if line.strip()
        ]
        title = " ".join(link.get_text(" ", strip=True).split())

        company = location = None
        for line in lines:
            if line == title or len(line) < 2:
                continue
            # Un lieu contient souvent une virgule ou un code postal
            if location is None and (re.search(r"\b\d{4,5}\b", line) or "," in line):
                                location = line[:120]
            elif company is None:
                company = line[:120]

        return company, location

    def _extract_id(self, url: str, source: str) -> str | None:
        """Récupère l'identifiant de l'offre depuis l'URL."""
        if source == "linkedin":
            if match := re.search(r"/jobs/view/(\d+)", url):
                return match.group(1)
            if match := re.search(r"currentJobId=(\d+)", url):
                return match.group(1)
        if source == "indeed":
            if match := re.search(r"[?&](?:jk|vjk)=([a-f0-9]+)", url):
                return match.group(1)
        return None