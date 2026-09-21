from __future__ import annotations

import json
import logging

from anthropic import Anthropic
from groq import Groq
from sqlmodel import Session, select

from app.config import settings
from app.models import Application, Job, JobStatus, Profile
from app.profile_config import load_profile

logger = logging.getLogger(__name__)

MODEL_ANTHROPIC = "claude-sonnet-4-6"
MODEL_GROQ = "openai/gpt-oss-120b"
MAX_TOKENS = 8000
MAX_DESCRIPTION_CHARS = 4000

SYSTEM_PROMPT = """Tu rédiges des candidatures pour le candidat dont le profil est fourni.

Règles absolues :
- Tu réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après, sans balises Markdown.
- Tu n'inventes JAMAIS d'expérience, de diplôme, de chiffre ou de compétence absents du profil fourni.
- Tu recopies les DATES exactement comme elles figurent dans le parcours, sans les abréger ni les modifier.
- Tu n'inventes aucun détail de mission (type de clientèle, fréquence, outil) qui ne soit pas écrit noir sur blanc.
- Tu cites des éléments concrets du parcours (volumes, outils, durées) quand ils servent l'offre.
- Si le candidat ne correspond pas bien au poste, tu le signales par un score_confiance bas.

Format de réponse attendu :
{
  "objet_mail": "string, court et explicite",
  "lettre_motivation": "string, 200 à 250 mots MAXIMUM, paragraphes séparés par \\n\\n",
  "points_cles": ["3 à 5 arguments courts reliant le profil au poste"],
  "score_confiance": 0.0 à 1.0
}

Style de la lettre :\n- Tu écris les nombres en chiffres (600 tickets, 450 postes), jamais en toutes lettres.\n- Tu mets un espace après chaque virgule et entre chaque mot : ne colle JAMAIS deux mots.\n- Tu commences la lettre par \"Madame, Monsieur,\" et tu la termines par une formule de politesse suivie du nom, du téléphone et de l'email.
- Sobre et direct, sans formules pompeuses ni superlatifs.
- Pas de "Je suis passionné par", "dynamique et motivé", "votre entreprise leader".
- Tu relies concrètement une compétence du profil à un besoin de l'offre.\n- Tu sépares chaque paragraphe par une ligne vide (\\n\\n) et tu n'utilises ni espace insécable ni tiret conditionnel.\n- Tu sépares chaque paragraphe par une ligne vide (\\n\\n) et tu n'utilises ni espace insécable ni tiret conditionnel.
- Tu assumes le profil junior : la formation récente et la disponibilité sont des atouts."""

def _variante_ch() -> str:
    """Consignes pour les offres suisses, construites depuis profile.yaml."""
    suisse = load_profile().get("candidature_suisse") or {}
    residence = suisse.get("residence", "en France, en zone frontalière")
    permis = suisse.get("permis", "un permis G (frontalier)")
    return f"""
Cette offre est en Suisse. Adapte la lettre :
- Vouvoiement helvétique, ton plus formel qu'en France.
- Formule d'appel : "Madame, Monsieur," / Formule finale : "Je vous adresse, Madame, Monsieur, mes salutations distinguées."
- Mentionne que le candidat réside {residence} et demandera {permis}.
- Ne mentionne pas une région de résidence française éloignée de la Suisse.
- Pas de "H/F" ni d'abréviations administratives françaises."""


class GeneratorError(RuntimeError):
    """Échec de génération d'une candidature."""


def _build_profile_text(profile: Profile) -> str:
    return f"""Nom : {profile.full_name}
Âge : {profile.age} ans
Localisation : {profile.location}
Formation : {profile.education}
Compétences : {", ".join(profile.skills or [])}
Permis et mobilité : {", ".join(profile.licences or [])}
Téléphone : {profile.phone or ""}
Email : {profile.email or ""}

PARCOURS PROFESSIONNEL (à utiliser, ne rien inventer au-delà) :
""" + "\n".join(profile.sample_letters or [])


def _build_job_text(job: Job) -> str:
    description = (job.description or "")[:MAX_DESCRIPTION_CHARS]
    return f"""Intitulé : {job.title}
Société : {job.company or "non précisée"}
Lieu : {job.location or "non précisé"}
Contrat : {job.contract_type or "non précisé"}
Description :
{description}"""



def _fix_spacing(text: str) -> str:
    """Nettoyage minimal : caracteres parasites et espaces multiples.

    On ne tente PAS de recoller les mots : toute heuristique casse
    des mots corrects ("reseau" -> "rese au"). Le prompt s'en charge.
    """
    import re
    text = text.replace("\u00a0", " ").replace("\u2011", "-")
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _parse_response(raw: str) -> dict:
    """Extrait le JSON de la réponse, en tolérant d'éventuelles balises Markdown."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise GeneratorError(f"reponse non parsable : {exc}") from exc

    for champ in ("objet_mail", "lettre_motivation", "points_cles", "score_confiance"):
        if champ not in data:
            raise GeneratorError(f"champ manquant dans la reponse : {champ}")

    return data



def _call_llm(system: str, profile_text: str, job_text: str) -> tuple[str, str]:
    """Appelle le fournisseur configuré et renvoie (texte brut, nom du modèle)."""
    user_content = (
        f"PROFIL DU CANDIDAT\n{profile_text}\n\n"
        f"OFFRE D'EMPLOI\n{job_text}\n\n"
        f"Rédige la candidature au format JSON demandé."
    )

    if settings.llm_provider == "groq":
        if not settings.groq_api_key:
            raise GeneratorError("GROQ_API_KEY absente")
        client = Groq(api_key=settings.groq_api_key)
        response = client.chat.completions.create(
            model=MODEL_GROQ,
            max_tokens=MAX_TOKENS,
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content, MODEL_GROQ

    if not settings.anthropic_api_key:
        raise GeneratorError("ANTHROPIC_API_KEY absente")
    client = Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=MODEL_ANTHROPIC,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(b.text for b in response.content if b.type == "text"), MODEL_ANTHROPIC


CONSIGNE_IT = """
Ce poste relève de l'informatique. Tu t'appuies UNIQUEMENT sur les éléments du parcours marqués [IT].
Tu ne mentionnes JAMAIS les éléments marqués [LOGISTIQUE] : ils sont hors sujet ici."""

CONSIGNE_NON_IT = """
Ce poste ne relève pas de l'informatique (logistique, livraison, manutention, service).
Tu mets en avant EN PREMIER les éléments du parcours marqués [LOGISTIQUE].
Tu évoques ensuite les éléments marqués [IT] sous l'angle transférable : réception et inventaire de matériel,
préparation et déplacement d'équipements, respect des procédures, relation avec les utilisateurs.
Tu restes SOBRE sur la technique informatique : elle n'est pas le sujet."""


def generate_application(session: Session, job: Job, force: bool = False) -> Application:
    """Génère un brouillon de candidature et l'enregistre, versionné."""
    profile = session.exec(select(Profile)).first()
    if not profile:
        raise GeneratorError("aucun profil en base")

    existing = session.exec(
        select(Application).where(Application.job_id == job.id).order_by(Application.version.desc())
    ).all()

    if existing and not force:
        return existing[0]

    variant = "CH" if job.country == "CH" else "FR"
    metier = job.metier.value if hasattr(job.metier, "value") else str(job.metier)
    consigne_metier = CONSIGNE_IT if metier in ("support", "polyvalent") else CONSIGNE_NON_IT
    system = SYSTEM_PROMPT + consigne_metier + (_variante_ch() if variant == "CH" else "")

    try:
        raw, model_used = _call_llm(system, _build_profile_text(profile), _build_job_text(job))
    except Exception as exc:
        raise GeneratorError(f"appel API echoue : {exc}") from exc

    data = _parse_response(raw)

    for app in existing:
        app.is_current = False
        session.add(app)

    application = Application(
        job_id=job.id,
        version=len(existing) + 1,
        is_current=True,
        subject=data["objet_mail"],
        cover_letter=_fix_spacing(data["lettre_motivation"]),
        key_points=data["points_cles"],
        confidence_score=float(data["score_confiance"]),
        model=model_used,
        variant=variant,
    )
    session.add(application)

    if job.status == JobStatus.NEW:
        job.status = JobStatus.DRAFTED
        session.add(job)

    session.commit()
    session.refresh(application)

    logger.info("Candidature generee pour l'offre %s (v%d)", job.id, application.version)
    return application


def generate_batch(session: Session, min_score: int = 60, limit: int = 5) -> dict:
    """Génère les candidatures des offres au-dessus du seuil, sans brouillon existant."""
    statement = (
        select(Job)
        .where(Job.score >= min_score, Job.status == JobStatus.NEW)
        .order_by(Job.score.desc())
        .limit(limit)
    )
    jobs = session.exec(statement).all()

    generees, erreurs = 0, []
    for job in jobs:
        try:
            generate_application(session, job)
            generees += 1
        except GeneratorError as exc:
            erreurs.append({"job_id": job.id, "erreur": str(exc)})
            logger.warning("Echec de generation pour l'offre %s : %s", job.id, exc)

    return {"generees": generees, "erreurs": erreurs, "candidates": len(jobs)}
