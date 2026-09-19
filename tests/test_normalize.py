from app.models import Metier, Zone
from app.services.normalize import detect_country, detect_metier, detect_zone


# --- Zones ---

def test_departement_idf_donne_p1():
    assert detect_zone("94 - Champigny sur Marne") == Zone.P1_IDF
    assert detect_zone("75 - Paris 8e Arrondissement") == Zone.P1_IDF


def test_departement_frontalier_donne_p2():
    assert detect_zone("74 - Annemasse") == Zone.P2_FRONTALIER
    assert detect_zone("01 - Ferney Voltaire") == Zone.P2_FRONTALIER


def test_ville_frontaliere_sans_departement():
    assert detect_zone("Annemasse") == Zone.P2_FRONTALIER
    assert detect_zone("Saint-Julien-en-Genevois") == Zone.P2_FRONTALIER


def test_reste_de_la_france_donne_p3():
    assert detect_zone("69 - Lyon 3e") == Zone.P3_FRANCE
    assert detect_zone("33 - Bordeaux") == Zone.P3_FRANCE


def test_suisse_donne_p4():
    assert detect_zone("Genève", country="CH") == Zone.P4_SUISSE
    assert detect_zone("Lausanne") == Zone.P4_SUISSE


def test_lieu_vide_donne_unknown():
    assert detect_zone(None) == Zone.UNKNOWN
    assert detect_zone("") == Zone.UNKNOWN


# --- Pays ---

def test_detection_pays():
    assert detect_country("94 - Champigny") == "FR"
    assert detect_country("Genève") == "CH"
    assert detect_country("Nyon") == "CH"


def test_hint_prime_sur_la_detection():
    assert detect_country("Genève", hint="FR") == "FR"


# --- Métiers ---

def test_metier_support():
    assert detect_metier("Technicien support informatique N1") == Metier.SUPPORT
    assert detect_metier("Chargé de hotline H/F") == Metier.SUPPORT


def test_metier_livreur():
    assert detect_metier("Chauffeur livreur VL (H/F)") == Metier.LIVREUR
    assert detect_metier("Coursier en scooter") == Metier.LIVREUR


def test_metier_polyvalent():
    assert detect_metier("Administrateur systeme junior") == Metier.POLYVALENT
    assert detect_metier("Technicien de proximité") == Metier.POLYVALENT


def test_metier_inconnu():
    assert detect_metier("Boulanger pâtissier") == Metier.AUTRE


def test_repli_sur_la_description():
    metier = detect_metier("Poste polyvalent H/F", "Vous assurez le support utilisateur N1.")
    assert metier == Metier.SUPPORT