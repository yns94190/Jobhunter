from __future__ import annotations

import re

from app.models import Metier, Zone
from app.services.dedup import normalize

# --- Zones géographiques ---

# P1 : Île-de-France
DEPARTEMENTS_IDF = {"75", "77", "78", "91", "92", "93", "94", "95"}

# P2 : communes frontalieres retenues (30 km maximum de Geneve)
VILLES_FRONTALIERES = {
    "gaillard", "ambilly", "annemasse", "ferney voltaire", "etrembieres", "ornex",
    "prevessin", "prevessin moens", "saint genis pouilly", "st genis pouilly",
    "saint julien en genevois", "st julien en genevois", "vetraz monthoux", "ville la grand",
    "archamps", "monnetier mornex", "collonges sous saleve", "cranves sales", "neydens", "segny",
    "versonnex", "sauverny", "fillinges", "gex", "echenevex", "grilly", "reignier", "reignier esery",
    "valleiry", "veigy foncenex", "divonne", "divonne les bains", "chens sur leman", "douvaine",
    "la roche sur foron", "pougny", "bonneville",
}

# Communes dont le nom existe aussi ailleurs (Viry-Chatillon, Thoiry dans les Yvelines,
# Chevry-Cossigny...) : retenues uniquement si le departement attendu est confirme.
VILLES_FRONTALIERES_AMBIGUES = {
    "beaumont": "74", "bonne": "74", "viry": "74",
    "thoiry": "01", "cessy": "01", "chevry": "01", "crozet": "01", "farges": "01", "peron": "01",
}
NOMS_DEPARTEMENTS = {"74": "haute savoie", "01": "ain"}

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


def _est_frontalier(location: str | None, normalized: str) -> bool:
    """Commune frontaliere retenue ; les noms ambigus exigent le bon departement."""
    brut = location or ""
    # Format Adzuna "Commune, Arrondissement" : seule la commune compte
    # ("Marignier, Bonneville" est a Marignier, pas a Bonneville)
    commune = normalize(brut.split(",")[0]) if "," in brut else normalized
    if _contient_ville(commune, VILLES_FRONTALIERES):
        return True
    departement = _extract_departement(brut)
    for ville, attendu in VILLES_FRONTALIERES_AMBIGUES.items():
        if not re.search(rf"\b{re.escape(ville)}\b", commune):
            continue
        if (departement == attendu
                or re.search(rf"\b{attendu}\d{{3}}\b", brut)
                or re.search(rf"\b{NOMS_DEPARTEMENTS[attendu]}\b", normalized)):
            return True
    return False


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
        if _est_frontalier(location, normalized):
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