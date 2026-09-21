from __future__ import annotations

import re

from app.models import Metier, Zone
from app.services.dedup import normalize

# --- Zones géographiques ---

# P1 : Île-de-France
DEPARTEMENTS_IDF = {"75", "77", "78", "91", "92", "93", "94", "95"}

# P2 : communes francaises a environ 45 min maximum de Geneve ou du canton de Vaud.
# Liste volontairement fermee : un departement entier (74, 01...) irait jusqu'a 1h30 de la frontiere.
# Les noms ambigus qui existent ailleurs en France (Viry, Thoiry, Chevry, Pringy...) sont exclus.
VILLES_FRONTALIERES = {
    # Genevois haut-savoyard
    "annemasse", "ambilly", "gaillard", "ville la grand", "vetraz monthoux", "etrembieres",
    "cranves sales", "reignier", "reignier esery", "saint julien en genevois", "st julien en genevois",
    "archamps", "collonges sous saleve", "neydens", "presilly", "valleiry", "vulbens", "frangy",
    "cruseilles", "allonzier la caille", "fillinges", "la roche sur foron", "saint pierre en faucigny",
    "amancy", "bonneville", "contamine sur arve", "marignier", "cluses", "scionzier", "marnaz", "thyez",
    # Bassin annecien
    "annecy", "annecy le vieux", "seynod", "cran gevrier", "meythet", "epagny", "metz tessy",
    "epagny metz tessy", "argonay", "poisy", "sillingy",
    # Chablais, face a Lausanne
    "thonon", "thonon les bains", "evian", "evian les bains", "publier", "amphion", "douvaine", "sciez",
    "anthy sur leman", "margencel", "allinges", "perrignier", "bons en chablais", "machilly",
    "veigy foncenex", "chens sur leman", "loisin",
    # Pays de Gex et Valserine (Ain)
    "gex", "ferney voltaire", "divonne", "divonne les bains", "saint genis pouilly", "st genis pouilly",
    "prevessin", "prevessin moens", "ornex", "versonnex", "segny", "echenevex", "sauverny", "vesancy",
    "grilly", "saint jean de gonville", "pougny", "bellegarde sur valserine", "valserhone",
    "chatillon en michaille",
    # Frontiere vaudoise cote Jura et Doubs
    "les rousses", "bois d amont", "premanon", "lamoura", "morez", "hauts de bienne",
    "pontarlier", "jougne", "les hopitaux neufs", "metabief", "labergement sainte marie", "houtaud",
}

# P4 : Suisse romande
VILLES_SUISSES = {
    "geneve", "geneva", "genf", "lausanne", "nyon", "vaud", "morges", "vevey", "montreux", "yverdon",
    "renens", "meyrin", "carouge", "vernier", "lancy", "onex", "versoix", "gland", "rolle", "prilly",
}

TELETRAVAIL_MARKERS = ("teletravail", "100 remote", "full remote", "remote", "a distance")


def _contient_ville(texte: str, villes: set[str]) -> bool:
    """Cherche un nom de ville entier : "gex" ne doit pas matcher dans un autre mot."""
    return any(re.search(rf"\b{re.escape(ville)}\b", texte) for ville in villes)


def _extract_departement(location: str) -> str | None:
    """Extrait le numero de departement d'un libelle France Travail ("94 - Champigny")."""
    match = re.match(r"^\s*(\d{2,3})\s*[-–]", location)
    return match.group(1) if match else None


def detect_country(location: str | None, hint: str | None = None) -> str:
    """Devine le pays a partir du lieu. `hint` prime s'il est fourni."""
    if hint in ("FR", "CH"):
        return hint
    normalized = normalize(location)
    if not normalized:
        return "FR"
    if _contient_ville(normalized, VILLES_SUISSES) or "suisse" in normalized or "switzerland" in normalized:
        return "CH"
    return "FR"


def detect_zone(location: str | None, country: str = "FR", description: str | None = None) -> Zone:
    """Classe une offre dans l'une des zones P1 a P4."""
    if country == "CH":
        return Zone.P4_SUISSE

    normalized = normalize(location)
    if normalized:
        if _extract_departement(location or "") in DEPARTEMENTS_IDF:
            return Zone.P1_IDF
        if _contient_ville(normalized, VILLES_FRONTALIERES):
            return Zone.P2_FRONTALIER
        if _contient_ville(normalized, VILLES_SUISSES):
            return Zone.P4_SUISSE
        if "ile de france" in normalized or "paris" in normalized:
            return Zone.P1_IDF

    haystack = f"{normalized} {normalize(description)}"
    if any(marker in haystack for marker in TELETRAVAIL_MARKERS):
        return Zone.P3_FRANCE

    return Zone.P3_FRANCE if normalized else Zone.UNKNOWN


# --- Métiers ---

MOTS_SUPPORT = (
    "support informatique", "support technique", "helpdesk", "help desk",
    "hotline", "technicien support", "support utilisateur", "assistance informatique",
    "technicien informatique", "service desk", "n1", "n2",
    "support de proximite", "maintenance informatique", "technicien poste de travail",
    "assistance utilisateur", "technicien assistance",
)
MOTS_LIVREUR = (
    "chauffeur livreur", "livreur", "chauffeur vl", "conducteur livreur",
    "coursier", "chauffeur poids", "preparateur livreur",
    "chauffeur pl", "chauffeur spl", "conducteur pl", "conducteur spl",
    "conducteur vl", "chauffeur magasinier", "conducteur accompagnateur",
    "conducteur de cour", "chauffeur de",
)
MOTS_POLYVALENT = (
    "technicien reseau", "administrateur systeme", "administrateur reseau",
    "technicien de proximite", "technicien proximite", "sysadmin", "systeme et reseau",
    "infrastructure", "exploitation informatique", "technicien deploiement",
    "administrateur infrastructure", "technicien maintenance informatique",
    "technicien systeme", "support systeme", "technicien it", "technicien parc",
)


def detect_metier(title: str, description: str | None = None) -> Metier:
    """Classe une offre par métier, en s'appuyant d'abord sur le titre."""
    haystack = normalize(title)

    # Le titre est prioritaire : plus fiable que la description
    if any(mot in haystack for mot in MOTS_SUPPORT):
        return Metier.SUPPORT
    if any(mot in haystack for mot in MOTS_LIVREUR):
        return Metier.LIVREUR
    if any(mot in haystack for mot in MOTS_POLYVALENT):
        return Metier.POLYVALENT

    # Repli sur la description, moins fiable
    if description:
        body = normalize(description)
        if any(mot in body for mot in MOTS_SUPPORT):
            return Metier.SUPPORT
        if any(mot in body for mot in MOTS_POLYVALENT):
            return Metier.POLYVALENT

    return Metier.AUTRE