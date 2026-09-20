from datetime import datetime, timedelta, timezone

from app.models import Job, Zone
from app.services.scoring import compute_score, load_config


def _job(**kwargs) -> Job:
    defaults = dict(
        title="Technicien support informatique",
        title_normalized="technicien support informatique",
        company="Acme",
        location="94 - Champigny",
        zone=Zone.P1_IDF,
        contract_type="CDI",
        url="https://example.org/1",
        dedup_hash="h1",
        published_at=datetime.now(timezone.utc),
    )
    return Job(**{**defaults, **kwargs})


def test_offre_ideale_score_haut():
    score, detail = compute_score(_job())
    assert score >= 80
    assert detail["mot_trouve"].startswith("fort:")


def test_mot_cle_faible_score_moins():
    fort, _ = compute_score(_job(title="Technicien support informatique"))
    faible, _ = compute_score(_job(title="Poste en informatique"))
    assert faible < fort


def test_titre_hors_cible_score_bas():
    score, detail = compute_score(_job(title="Boulanger patissier"))
    assert detail["mot_trouve"] == "aucun"
    assert score < 60


def test_zone_influence_le_score():
    idf, _ = compute_score(_job(zone=Zone.P1_IDF))
    suisse, _ = compute_score(_job(zone=Zone.P4_SUISSE))
    assert idf > suisse
    assert idf - suisse == 20


def test_cdi_mieux_que_interim():
    cdi, _ = compute_score(_job(contract_type="CDI"))
    interim, _ = compute_score(_job(contract_type="MIS"))
    assert cdi > interim


def test_offre_ancienne_perd_des_points():
    recente, _ = compute_score(_job())
    ancienne, _ = compute_score(
        _job(published_at=datetime.now(timezone.utc) - timedelta(days=20))
    )
    assert recente > ancienne


def test_malus_experience_et_diplome():
    sans, _ = compute_score(_job(description="Poste ouvert aux debutants."))
    avec, detail = compute_score(_job(description="Bac+5 exige, 5 ans d experience minimum."))
    assert avec < sans
    assert len(detail["motifs_malus"]) >= 1


def test_malus_permis_lourd():
    _, detail = compute_score(_job(title="Chauffeur livreur SPL", description="Permis CE exige"))
    assert "spl" in detail["motifs_malus"] or "permis ce" in detail["motifs_malus"]


def test_malus_plafonne():
    _, detail = compute_score(
        _job(description="Bac+5 master ingenieur diplome senior confirme 5 ans permis CE SPL")
    )
    assert detail["malus"] <= load_config()["malus"]["poids_max"]


def test_score_borne_entre_0_et_100():
    for job in (_job(), _job(title="xyz", description="permis CE SPL bac+5 senior")):
        score, _ = compute_score(job)
        assert 0 <= score <= 100


def test_detail_contient_toutes_les_composantes():
    _, detail = compute_score(_job())
    for cle in ("mots_cles", "zone", "contrat", "fraicheur", "malus", "total"):
        assert cle in detail