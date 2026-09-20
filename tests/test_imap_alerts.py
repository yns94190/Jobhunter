from datetime import datetime, timezone

from app.connectors.imap_alerts import ImapAlertsConnector, _clean_url, _is_job_url

HTML_LINKEDIN = """
<html><body>
<table>
  <tr><td>
    <a href="https://www.linkedin.com/jobs/view/4123456789/?trk=eml-alert">
      Technicien Support IT - Genève
    </a>
    <div>Groupe ANSAM</div>
    <div>Genève, Genève, Suisse</div>
  </td></tr>
  <tr><td>
    <a href="https://www.linkedin.com/jobs/view/4987654321/">Technicien de proximité</a>
    <div>Darest Informatic</div>
    <div>Lausanne, 1000</div>
  </td></tr>
  <tr><td>
    <a href="https://www.linkedin.com/comm/psettings/email-unsubscribe">Se désabonner</a>
  </td></tr>
</table>
</body></html>
"""

HTML_INDEED = """
<html><body>
<div>
  <a href="https://fr.indeed.com/rc/clk?jk=a1b2c3d4e5f6&fccid=xyz">
    Technicien support informatique H/F
  </a>
  <div>Acme Services</div>
  <div>94000 Créteil</div>
</div>
<div><a href="https://fr.indeed.com/account/settings">Mes préférences</a></div>
</body></html>
"""


def _connector():
    return ImapAlertsConnector(host="imap.test", user="u", password="p")


# --- Nettoyage des URL ---

def test_clean_url_retire_le_tracking():
    url = _clean_url("https://www.linkedin.com/jobs/view/123/?trk=eml-alert&refId=abc")
    assert "trk" not in url
    assert "/jobs/view/123/" in url


def test_clean_url_garde_l_identifiant_indeed():
    url = _clean_url("https://fr.indeed.com/rc/clk?jk=abc123&fccid=zzz")
    assert "jk=abc123" in url
    assert "fccid" not in url


def test_clean_url_deroule_une_redirection():
    url = _clean_url("https://tracker.test/r?url=https%3A%2F%2Ffr.indeed.com%2Fviewjob%3Fjk%3Dxyz")
    assert url.startswith("https://fr.indeed.com/viewjob")


# --- Filtrage des liens ---

def test_liens_de_desabonnement_rejetes():
    assert not _is_job_url("https://www.linkedin.com/comm/psettings/email-unsubscribe", "linkedin")
    assert not _is_job_url("https://fr.indeed.com/account/settings", "indeed")


def test_liens_d_offre_acceptes():
    assert _is_job_url("https://www.linkedin.com/jobs/view/123/", "linkedin")
    assert _is_job_url("https://fr.indeed.com/rc/clk?jk=abc", "indeed")


# --- Parsing ---

def test_parse_linkedin():
    jobs = _connector().parse_email(HTML_LINKEDIN, "linkedin", datetime.now(timezone.utc))

    assert len(jobs) == 2  # le lien de désabonnement est écarté
    premier = jobs[0]
    assert premier.title == "Technicien Support IT - Genève"
    assert premier.external_id == "4123456789"
    assert premier.source_name == "imap_linkedin"
    assert premier.published_at is not None


def test_parse_indeed():
    jobs = _connector().parse_email(HTML_INDEED, "indeed")

    assert len(jobs) == 1
    assert jobs[0].external_id == "a1b2c3d4e5f6"
    assert jobs[0].source_name == "imap_indeed"


def test_pas_de_doublon_dans_un_meme_mail():
    html = HTML_LINKEDIN + HTML_LINKEDIN
    jobs = _connector().parse_email(html, "linkedin")
    assert len(jobs) == 2


def test_html_vide_ne_plante_pas():
    assert _connector().parse_email("", "linkedin") == []
    assert _connector().parse_email("<html></html>", "linkedin") == []


# --- Détection de la source ---

def test_detection_de_l_expediteur():
    c = _connector()
    assert c._detect_source("jobs-listings@linkedin.com") == "linkedin"
    assert c._detect_source("alert@indeedemail.com") == "indeed"
    assert c._detect_source("noreply@jobup.ch") == "jobup"
    assert c._detect_source("spam@random.test") is None


def test_pays_suisse_pour_jobup():
    html = '<div><a href="https://www.jobup.ch/fr/emploi/detail/12345/">Informaticien support</a></div>'
    jobs = _connector().parse_email(html, "jobup")
    assert jobs[0].country == "CH"