# JobHunter

[![CI/CD](https://github.com/yns94190/Jobhunter/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/yns94190/Jobhunter/actions/workflows/ci-cd.yml)

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
| `SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` | Envoi des candidatures par mail | mot de passe d'application Gmail |
| `SMTP_FROM` | Adresse affichée en `From` et `Reply-To` | ton adresse de candidature |
| `ATTACHMENTS_DIR` | Dossier des pièces jointes (défaut `data/attachments`) | — |

Les pondérations du scoring se règlent dans `scoring.yaml`, sans toucher au code.

### Envoi des candidatures par mail

Le mail part du compte `SMTP_USER` (enveloppe SMTP) avec l'en-tête `From` et `Reply-To` = `SMTP_FROM` (à défaut, `SMTP_USER`). Exemple Gmail :

~~~env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587          # STARTTLS ; 465 = TLS direct, aussi géré
SMTP_USER=compte.denvoi@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx   # mot de passe d'application, pas le mot de passe du compte
SMTP_FROM=adresse.de.candidature@gmail.com
~~~

Si `SMTP_FROM` diffère de `SMTP_USER`, Gmail réécrit le `From` sauf si l'adresse est déclarée comme alias vérifié (Paramètres › Comptes › « Envoyer des e-mails en tant que »). Le plus simple : `SMTP_USER` = `SMTP_FROM`.

Si une variable manque, l'API répond 400 en nommant les variables absentes, sans rien envoyer.

**Aucun envoi automatique.** L'envoi ne part que depuis la modale « Candidature » : destinataire (pré-rempli avec l'adresse de l'offre, modifiable), choix des pièces jointes, clic sur « Envoyer par mail » puis confirmation. Les corrections non enregistrées du brouillon sont sauvegardées juste avant l'envoi. Ensuite l'offre passe en « postulé », `sent_at` est renseigné et le changement est tracé dans l'historique. Aucune route ni tâche planifiée n'envoie de candidature, et un test le vérifie.

### Pièces jointes

Section « Mes documents » de la barre latérale : téléverser CV et lettres, ★ pour les joindre par défaut (cochés d'office dans la modale).

- 5 Mo maximum par fichier, extensions `pdf`, `docx`, `odt`, `png`, `jpg` uniquement ; le contenu doit correspondre à l'extension (signature des premiers octets)
- nom de fichier assaini (accents, espaces et chemins retirés), stocké sous un nom unique dans `data/attachments/`, hors git et monté en volume
- API : `POST /attachments` (multipart, champ `file`), `GET /attachments`, `PATCH /attachments/{id}` (`{"is_default": true|false}`, sans corps = bascule), `DELETE /attachments/{id}`

### Offres déjà traitées

Les offres postulées, relancées, en entretien, refusées ou ignorées sont masquées des onglets France / Frontalier / Suisse / Toutes. Elles restent visibles dans « Candidatures » et via le filtre de statut. Côté API : `GET /jobs?include_closed=true` les réaffiche ; `?status=...` filtre comme avant.

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

Chaque expérience porte un `domaine` (`it` ou `logistique`) : le générateur choisit automatiquement les expériences pertinentes selon le type d'offre. Le profil est resynchronisé en base à chaque démarrage. En production, `./sync-profile.sh` envoie le profil au serveur.
