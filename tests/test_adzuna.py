import httpx
import pytest
import respx

from app.connectors.adzuna import BASE_URL, AdzunaConnector

OFFRE_BRUTE = {
    "id": 4567890,
    "title": "Technicien support informatique",
    "description": "Support N1/N2 pour une PME genevoise.",
    "created": "2026-09-18T14:00:00Z",
    "redirect_url": "https://www.adzuna.ch/details/4567890",
    "company": {"display_name": "Helvetia IT"},
    "location": {"display_name": "Genève, Genève"},
    "contract_type": "permanent",
}


@pytest.fixture
def connector_fr():
    return AdzunaConnector(country="fr", app_id="test_id", app_key="test_key")


@pytest.fixture
def connector_ch():
    return AdzunaConnector(country="ch", app_id="test_id", app_key="test_key")


def test_nom_depend_du_pays(connector_fr, connector_ch):
    assert connector_fr.name == "adzuna_fr"
    assert connector_ch.name == "adzuna_ch"


def test_requetes_differentes_par_pays(connector_fr, connector_ch):
    assert connector_fr._queries != connector_ch._queries


@respx.mock
@pytest.mark.asyncio
async def test_fetch_retourne_des_dto(connector_ch):
    respx.get(url__startswith=f"{BASE_URL}/ch/search/").mock(
        return_value=httpx.Response(200, json={"results": [OFFRE_BRUTE]})
    )

    jobs = await connector_ch.fetch()

    assert len(jobs) >= 1
    job = jobs[0]
    assert job.source_name == "adzuna_ch"
    assert job.country == "CH"
    assert job.company == "Helvetia IT"
    assert job.external_id == "4567890"
    assert job.published_at is not None


@pytest.mark.asyncio
async def test_sans_identifiants_pas_de_collecte():
    vide = AdzunaConnector(country="fr", app_id=None, app_key=None)
    assert await vide.fetch() == []


def test_mapping_des_champs(connector_ch):
    dto = connector_ch._to_dto(OFFRE_BRUTE)
    assert dto.location == "Genève, Genève"
    assert dto.contract_type == "permanent"
    assert dto.url.startswith("https://")


def test_offre_incomplete_ne_plante_pas(connector_fr):
    dto = connector_fr._to_dto({"id": 1, "title": "Test"})
    assert dto.company is None
    assert dto.published_at is None