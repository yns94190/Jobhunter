# JobHunter

Agrégateur personnel d'offres d'emploi : collecte multi-sources, scoring selon le profil, génération de lettres de motivation par IA et suivi des candidatures.

**Le système prépare, l'utilisateur valide.** Aucune candidature n'est jamais envoyée automatiquement.

## Fonctionnalités

- **Collecte multi-sources** : API France Travail (OAuth), Adzuna France et Suisse, alertes mail LinkedIn / Indeed / jobup.ch via IMAP
- **Collecte automatique** toutes les 3 h (APScheduler)
- **Dédoublonnage** à deux niveaux : empreinte exacte et similarité floue (RapidFuzz)
- **Classification** par zone géographique (Île-de-France, frontalier, France, Suisse) et par métier
- **Scoring 0-100** configurable en YAML : mots-clés, zone, contrat, fraîcheur, malus
- **Lettres de motivation** générées par LLM (Groq gratuit ou Anthropic), adaptées au métier et au pays, versionnées
- **Tableau de bord** : accueil, filtres, éditeur de brouillon, statistiques, historique, mode sombre
- **Suivi** des candidatures et rappel de relance après 10 jours
- **Authentification** HTTP Basic pour un déploiement en ligne

## Sources et conformité

Aucun scraping de LinkedIn ou d'Indeed : ces plateformes sont couvertes par leurs alertes e-mail officielles, lues via IMAP. Les API utilisées (France Travail, Adzuna) sont publiques et interrogées au rythme d'une requête toutes les 2 secondes.

## Stack

Python 3.11 · FastAPI · SQLModel · Alembic · SQLite · APScheduler · httpx · BeautifulSoup · imap-tools · Tailwind CSS · Alpine.js · Docker · pytest

## Installation

Prérequis : Docker et Docker Compose.

~~~bash
git clone https://github.com/<ton-compte>/jobhunter.git
cd jobhunter
cp .env.example .env
docker compose up -d --build
~~~

Le tableau de bord est accessible sur http://localhost:8000.

## Configuration

Les clés se renseignent dans `.env`, jamais versionné :

| Variable | Rôle | Obtention |
|---|---|---|
| `FRANCE_TRAVAIL_CLIENT_ID` / `_SECRET` | Offres France Travail | francetravail.io |
| `ADZUNA_APP_ID` / `_KEY` | Offres Adzuna FR et CH | developer.adzuna.com |
| `IMAP_HOST` / `_USER` / `_PASSWORD` | Lecture des alertes mail | mot de passe d'application Gmail |
| `GROQ_API_KEY` | Génération des lettres (gratuit) | console.groq.com |
| `AUTH_PASSWORD` | Protège l'accès en ligne | au choix |

Les pondérations du scoring se règlent dans `scoring.yaml`, sans toucher au code.

## Tests

~~~bash
docker compose run --rm api pytest -q
~~~

Aucun test n'appelle de service externe : toutes les réponses HTTP sont simulées.

## Architecture

~~~
app/
  connectors/   une classe par source, interface commune fetch() -> list[JobDTO]
  services/     dedup, normalisation, scoring, génération, suivi, statistiques
  api/          routes REST
  scheduler.py  collecte planifiée
frontend/       page unique, sans étape de build
tests/
~~~

## Licence

MIT

## Profil candidat

Toutes les données personnelles sont dans `profile.yaml`, jamais versionné :

~~~bash
cp profile.example.yaml profile.yaml
~~~

Chaque expérience porte un `domaine` (`it` ou `logistique`) : le générateur choisit automatiquement les expériences pertinentes selon le type d'offre. Le profil est resynchronisé en base à chaque démarrage.
