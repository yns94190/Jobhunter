from app.profile_config import format_experience


def test_format_experience_prefixe_le_domaine():
    ligne = format_experience({
        "domaine": "it", "intitule": "Technicien support", "structure": "Acme",
        "lieu": "Paris", "periode": "2024-2026", "missions": "Support N1/N2",
    })
    assert ligne.startswith("[IT] Technicien support - Acme, Paris, 2024-2026")
    assert ligne.endswith("Support N1/N2")


def test_format_experience_champs_vides_ignores():
    ligne = format_experience({"domaine": "logistique", "intitule": "Livreur", "lieu": "", "missions": "Tournees"})
    # Le lieu vide et la structure absente ne laissent pas de virgules orphelines
    assert ligne == "[LOGISTIQUE] Livreur -  : Tournees"
