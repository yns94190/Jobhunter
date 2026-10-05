import pytest
from sqlmodel import Session, select

from app.connectors.base import JobDTO
from app.models import Job, Zone
from app.services.contact import extract_contact_email, find_emails, is_valid_email
from app.services.ingest import save_jobs

FT_PHRASE = "Pour postuler, utiliser le lien suivant : https://candidat.francetravail.fr/offres/recherche/detail/123ABC"


# --- Extraction ---


def test_nominal_case():
    text = "Envoyez CV et lettre de motivation à : dupont.recruteur@entreprise-exemple.fr avant le 15/10."
    assert extract_contact_email(text) == "dupont.recruteur@entreprise-exemple.fr"


@pytest.mark.parametrize(
    "ignored",
    ["noreply@entreprise.fr", "no-reply@entreprise.fr", "postmaster@entreprise.fr",
     "webmaster@entreprise.fr", "abuse@entreprise.fr", "NoReply@entreprise.fr"],
)
def test_technical_addresses_ignored(ignored):
    assert extract_contact_email(f"Contact : {ignored}") is None


@pytest.mark.parametrize(
    "platform",
    ["alertes@indeed.com", "jobs-noreply@linkedin.com", "contact@adzuna.fr", "info@jobup.ch",
     "support@jobs.ch", "offres@francetravail.fr", "ale@pole-emploi.fr", "x@mail.indeed.fr"],
)
def test_platform_domains_ignored(platform):
    assert extract_contact_email(f"Postulez via {platform}") is None


def test_recruitment_address_preferred_over_first():
    text = (
        "Pour toute question : contact@entreprise.fr. Les candidatures sont à envoyer à "
        "recrutement@entreprise.fr, et la comptabilité à compta@entreprise.fr."
    )
    assert extract_contact_email(text) == "recrutement@entreprise.fr"


@pytest.mark.parametrize("local", ["rh", "rh.paris", "jobs", "carriere", "candidature", "emploi", "recrutement-idf"])
def test_recruitment_prefixes(local):
    text = f"Contact : direction@entreprise.fr ou {local}@entreprise.fr"
    assert extract_contact_email(text) == f"{local}@entreprise.fr"


def test_first_valid_when_no_recruitment_address():
    text = "Ecrire à commercial@exemple.org ou à direction@exemple.org"
    assert extract_contact_email(text) == "commercial@exemple.org"


def test_noreply_skipped_in_favour_of_next_address():
    assert extract_contact_email("noreply@site.fr puis manager@site.fr") == "manager@site.fr"


@pytest.mark.parametrize(
    "text",
    [None, "", "Aucune adresse ici, postulez sur le site.", "Suivez-nous sur Twitter : @Entreprise",
     "Logo : logo@2x.png", "Tarif 15€@heure", FT_PHRASE],
)
def test_nothing_found(text):
    assert extract_contact_email(text) is None


def test_duplicates_and_trailing_punctuation():
    text = "Ecrire à rh@exemple.fr. Rappel : rh@exemple.fr ! <mailto:rh@exemple.fr>"
    assert find_emails(text) == ["rh@exemple.fr"]


def test_domain_lowercased():
    assert extract_contact_email("CV à candidat.test@Hotmail.Fr") == "candidat.test@hotmail.fr"


def test_is_valid_email():
    assert is_valid_email("rh@exemple.fr")
    assert is_valid_email("  rh@exemple.fr ")
    assert not is_valid_email(FT_PHRASE)
    assert not is_valid_email("Ecrire à rh@exemple.fr")
    assert not is_valid_email(None)
    assert not is_valid_email("")


# --- Route POST /jobs/{id}/detect-contact ---


def _job(session: Session, **kwargs) -> Job:
    defaults = dict(
        title="Technicien", title_normalized="technicien", url="https://example.org/o",
        dedup_hash="hc", zone=Zone.P1_IDF,
    )
    job = Job(**{**defaults, **kwargs})
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_detect_contact_fills_email_from_description(client, session):
    job = _job(session, description="CV à recrutement@exemple-sa.fr", contact_email=None)
    body = client.post(f"/jobs/{job.id}/detect-contact").json()
    assert body == {"found": True, "contact_email": "recrutement@exemple-sa.fr", "source": "description"}
    session.refresh(job)
    assert job.contact_email == "recrutement@exemple-sa.fr"


def test_detect_contact_replaces_non_email_value(client, session):
    job = _job(session, description="Par mail : rh@exemple-sa.fr", contact_email=FT_PHRASE)
    assert client.post(f"/jobs/{job.id}/detect-contact").json()["contact_email"] == "rh@exemple-sa.fr"
    session.refresh(job)
    assert job.contact_email == "rh@exemple-sa.fr"


def test_detect_contact_nothing_found_keeps_value(client, session):
    job = _job(session, description="Postulez sur notre site.", contact_email=FT_PHRASE)
    body = client.post(f"/jobs/{job.id}/detect-contact").json()
    assert body == {"found": False, "contact_email": None, "source": None}
    session.refresh(job)
    assert job.contact_email == FT_PHRASE  # rien n'est inventé ni effacé


def test_detect_contact_keeps_existing_valid_email(client, session):
    job = _job(session, description="autre@exemple.fr", contact_email="rh@exemple.fr")
    body = client.post(f"/jobs/{job.id}/detect-contact").json()
    assert body == {"found": True, "contact_email": "rh@exemple.fr", "source": "existing"}


def test_detect_contact_unknown_job(client):
    assert client.post("/jobs/999/detect-contact").status_code == 404


# --- Ingestion ---


def _dto(i: int, **kwargs) -> JobDTO:
    defaults = dict(
        title=f"Poste {i}", company=f"Societe {i}", location="Paris", url=f"https://example.org/{i}",
        external_id=str(i), source_name="france_travail",
    )
    return JobDTO(**{**defaults, **kwargs})


def test_ingestion_extracts_email_from_description(session):
    save_jobs(session, [
        _dto(1, description="Envoyez votre CV à jobs@boulangerie-exemple.fr"),
        _dto(2, description="Postulez en ligne.", contact_email=FT_PHRASE),
        _dto(3, description="Sinon autre@exemple.fr", contact_email="rh@source.fr"),
        _dto(4, description="Ecrire à rh@dans-le-texte.fr", contact_email=FT_PHRASE),
    ], "france_travail")
    emails = {j.title: j.contact_email for j in session.exec(select(Job)).all()}
    assert emails == {
        "Poste 1": "jobs@boulangerie-exemple.fr",
        "Poste 2": None,                    # phrase de la source écartée, rien d'inventé
        "Poste 3": "rh@source.fr",          # adresse fournie par la source prioritaire
        "Poste 4": "rh@dans-le-texte.fr",
    }


# --- Route admin POST /admin/detect-contacts ---


def _seed_contacts(session: Session) -> dict[str, Job]:
    jobs = {
        "phrase_avec_adresse": _job(session, dedup_hash="a1", description="CV a recrutement@exemple-sa.fr", contact_email=FT_PHRASE),
        "vide_avec_adresse": _job(session, dedup_hash="a2", description="Ecrire a jobs@boulangerie-exemple.fr", contact_email=None),
        "phrase_sans_adresse": _job(session, dedup_hash="a3", description="Postulez en ligne.", contact_email=FT_PHRASE),
        "vide_sans_adresse": _job(session, dedup_hash="a4", description="Suivez-nous : @Entreprise", contact_email=None),
        "deja_valide": _job(session, dedup_hash="a5", description="autre@exemple.fr", contact_email="rh@exemple.fr"),
        "plateforme_seule": _job(session, dedup_hash="a6", description="Via alertes@indeed.com", contact_email=None),
    }
    return jobs


def test_admin_detect_contacts_fills_only_found_addresses(client, session):
    jobs = _seed_contacts(session)
    body = client.post("/admin/detect-contacts").json()

    assert body["dry_run"] is False
    assert (body["already_valid"], body["examined"], body["found"], body["not_found"]) == (1, 5, 2, 3)
    assert sorted(u["job_id"] for u in body["updated"]) == sorted(
        [jobs["phrase_avec_adresse"].id, jobs["vide_avec_adresse"].id]
    )

    for job in jobs.values():
        session.refresh(job)
    assert jobs["phrase_avec_adresse"].contact_email == "recrutement@exemple-sa.fr"
    assert jobs["vide_avec_adresse"].contact_email == "jobs@boulangerie-exemple.fr"
    # Rien d'inventé, rien d'effacé, rien de remplacé
    assert jobs["phrase_sans_adresse"].contact_email == FT_PHRASE
    assert jobs["vide_sans_adresse"].contact_email is None
    assert jobs["plateforme_seule"].contact_email is None
    assert jobs["deja_valide"].contact_email == "rh@exemple.fr"


def test_admin_detect_contacts_dry_run_writes_nothing(client, session):
    jobs = _seed_contacts(session)
    body = client.post("/admin/detect-contacts?dry_run=true").json()
    assert (body["dry_run"], body["found"]) == (True, 2)

    for job in jobs.values():
        session.refresh(job)
    assert jobs["phrase_avec_adresse"].contact_email == FT_PHRASE
    assert jobs["vide_avec_adresse"].contact_email is None


def test_admin_detect_contacts_is_idempotent(client, session):
    _seed_contacts(session)
    client.post("/admin/detect-contacts")
    second = client.post("/admin/detect-contacts").json()
    assert (second["already_valid"], second["examined"], second["found"]) == (3, 3, 0)


def test_admin_detect_contacts_empty_base(client):
    body = client.post("/admin/detect-contacts").json()
    assert (body["examined"], body["found"], body["updated"]) == (0, 0, [])
