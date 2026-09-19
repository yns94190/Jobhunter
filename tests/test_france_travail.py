import httpx
import pytest
import respx

from app.connectors.france_travail import API_URL, TOKEN_URL, FranceTravailConnector

OFFRE_BRUTE = {
    "id": "190QXYZ",
    "intitule": "Technicien support informatique N1",
    "description": "Support utilisateurs, Windows, Active Directory.",
    "dateCreation": "2026-09-15T09:30:00.000Z",
    "lieuTravail": {"libelle": "94 - CHAMPIGNY SUR MARNE"},
    "entreprise": {"nom": "Acme Services"},
    "typeContrat": "CDI",
    "contact": {"courriel": "rh@acme.test"},
    "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/190QXYZ"},
}


@pytest.fixture
def connector():
    return FranceTravailConnector(client_id="test_id", client_secret="test_secret")


@respx.mock
@pytest.mark.asyncio
async def test_fetch_retourne_des_dto(connector):
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fake_token", "expires_in": 1500})
    )
    # 204 par défaut : une seule page de résultats, puis plus rien
    respx.get(API_URL).mock(
        side_effect=[httpx.Response(200, json={"resultats": [OFFRE_BRUTE]})]
        + [httpx.Response(204)] * 200
    )

    jobs = await connector.fetch()

    assert len(jobs) >= 1
    job = jobs[0]
    assert job.source_name == "france_travail"
    assert job.external_id == "190QXYZ"
    assert job.company == "Acme Services"
    assert job.contract_type == "CDI"
    assert job.contact_email == "rh@acme.test"
    assert job.country == "FR"
    assert job.published_at is not None


@respx.mock
@pytest.mark.asyncio
async def test_token_est_mis_en_cache(connector):
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fake_token", "expires_in": 1500})
    )
    respx.get(API_URL).mock(return_value=httpx.Response(204))

    await connector.fetch()
    await connector.fetch()

    assert route.call_count == 1  # le second appel réutilise le token


@pytest.mark.asyncio
async def test_sans_identifiants_pas_de_collecte():
    vide = FranceTravailConnector(client_id=None, client_secret=None)
    assert await vide.fetch() == []


def test_mapping_lieu_et_url(connector):
    dto = connector._to_dto(OFFRE_BRUTE)
    assert dto.location == "94 - CHAMPIGNY SUR MARNE"
    assert dto.url.startswith("https://")


def test_url_de_repli_si_absente(connector):
    offre = {**OFFRE_BRUTE, "origineOffre": {}}
    dto = connector._to_dto(offre)
    assert "190QXYZ" in dto.url