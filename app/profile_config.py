from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

# profile.yaml (donnees reelles) prime ; a defaut, le modele sert de repli
PROFILE_PATHS = (Path("/app/profile.yaml"), Path("/app/profile.example.yaml"))


@lru_cache(maxsize=1)
def load_profile() -> dict:
    """Charge le profil depuis le premier fichier YAML trouve."""
    for path in PROFILE_PATHS:
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            data["_source"] = path.name
            return data
    raise FileNotFoundError("profile.yaml introuvable : copier profile.example.yaml")


def format_experience(exp: dict) -> str:
    """Une experience en une ligne, prefixee de son domaine pour le generateur."""
    domaine = str(exp.get("domaine", "autre")).upper()
    contexte = ", ".join(p for p in (exp.get("structure"), exp.get("lieu"), exp.get("periode")) if p)
    return f"[{domaine}] {exp.get('intitule', '')} - {contexte} : {exp.get('missions', '')}".strip()
