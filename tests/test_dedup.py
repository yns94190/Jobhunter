from app.connectors.base import JobDTO
from app.services.dedup import compute_hash, is_duplicate, normalize


def _dto(title: str, company: str = "Acme", location: str = "Paris") -> JobDTO:
    return JobDTO(
        source_name="test",
        external_id="1",
        title=title,
        url="https://example.org/1",
        company=company,
        location=location,
    )


def test_normalize_supprime_accents_et_ponctuation():
    assert normalize("Téchnicien Réseau (N1) !") == "technicien reseau n1"


def test_normalize_gere_le_vide():
    assert normalize(None) == ""
    assert normalize("") == ""


def test_hash_identique_malgre_la_casse_et_les_accents():
    a = compute_hash("Téchnicien Support", "ACME", "Paris")
    b = compute_hash("technicien support", "acme", "PARIS")
    assert a == b


def test_hash_different_si_societe_differente():
    a = compute_hash("Technicien Support", "Acme", "Paris")
    b = compute_hash("Technicien Support", "Globex", "Paris")
    assert a != b


def test_doublon_flou_detecte():
    existing = [("Technicien support informatique N1", "Acme", "Paris")]
    assert is_duplicate(_dto("Technicien Support Informatique N1"), existing)


def test_ordre_des_mots_ignore():
    existing = [("Support technicien informatique", "Acme", "Paris")]
    assert is_duplicate(_dto("Technicien informatique support"), existing)


def test_societes_differentes_ne_sont_pas_des_doublons():
    existing = [("Technicien support informatique N1", "Globex", "Paris")]
    assert not is_duplicate(_dto("Technicien support informatique N1"), existing)


def test_titres_eloignes_ne_sont_pas_des_doublons():
    existing = [("Chauffeur livreur VL", "Acme", "Paris")]
    assert not is_duplicate(_dto("Administrateur systemes senior"), existing)