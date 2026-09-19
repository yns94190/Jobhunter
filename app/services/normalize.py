from __future__ import annotations

import re

from app.models import Metier, Zone
from app.services.dedup import normalize

# --- Zones géographiques ---

# P1 : Île-de-France
DEPARTEMENTS_IDF = {"75", "77", "78", "91", "92", "93", "94", "95"}

# P2 : zone frontalière Haute-Savoie / Pays de Gex
VILLES_FRONTALIERES = {
    "annemasse", "saint julien en genevois", "st julien en genevois",
    "thonon", "thonon les bains", "ferney voltaire", "gex", "annecy",
    "cluses", "bonneville", "sallanches", "divonne", "divonne les bains",
    "archamps", "cranves sales", "gaillard", "ville la grand", "ambilly",
}
DEPARTEMENTS_FRONTALIERS = {"74", "01"}

# P4 : Suisse romande
VILLES_SUISSES = {
    "geneve", "geneva", "genf", "lausanne", "nyon", "vaud", "morges",
    "vevey", "montreux", "yverdon", "renens", "meyrin", "carouge",
    "vernier", "lancy", "onex", "versoix", "gland", "rolle", "prilly",
}
CANTONS_SUISSES = {"ge", "vd", "vs", "fr", "ne", "ju"}

TELETRAVAIL_MARKERS = ("teletravail", "100 remote", "full remote", "remote", "a distance")


def _extract_departement(location: str) -> str | None:
    """Extrait le numéro de département d'un libellé France Travail.

    Format habituel : "94 - Champigny sur Marne".
    """
    match = re.match(r"^\s*(\d{2,3})\s*[-–]", location)
    return match.group(1) if match else None


def detect_country(location: str | None, hint: str | None = None) -> str:
    """Devine le pays à partir du lieu. `hint` prime s'il est fourni."""
    if hint in ("FR", "CH"):
        return hint

    normalized = normalize(location)
    if not normalized:
        return "FR"

    if any(ville in normalized for ville in VILLES_SUISSES):
        return "CH"
    if "suisse" in normalized or "switzerland" in normalized:
        return "CH"
    return "FR"


def detect_zone(location: str | None, country: str = "FR", description: str | None = None) -> Zone:
    """Classe une offre dans l'une des zones P1 à P4."""
    normalized = normalize(location)

    if country == "CH":
        return Zone.P4_SUISSE

    if normalized:
        departement = _extract_departement(location or "")

        if departement in DEPARTEMENTS_IDF:
            return Zone.P1_IDF
        if departement in DEPARTEMENTS_FRONTALIERS:
            return Zone.P2_FRONTALIER
        if any(ville in normalized for ville in VILLES_FRONTALIERES):
            return Zone.P2_FRONTALIER
        if any(ville in normalized for ville in VILLES_SUISSES):
            return Zone.P4_SUISSE
        if "ile de france" in normalized or "paris" in normalized:
            return Zone.P1_IDF

    # Le télétravail intégral entre en P3, quel que soit le lieu annoncé
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