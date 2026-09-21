from __future__ import annotations

import base64
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

# Route laissee ouverte pour les controles de sante de l'hebergeur
PUBLIC_PATHS = {"/health"}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Protege toute l'application par identifiant et mot de passe."""

    async def dispatch(self, request: Request, call_next):
        # Pas de mot de passe configure : acces libre (usage local)
        if not settings.auth_password or request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                user, password = "", ""

            # compare_digest : comparaison a temps constant, contre les attaques par timing
            if secrets.compare_digest(user, settings.auth_user) and secrets.compare_digest(
                password, settings.auth_password
            ):
                return await call_next(request)

        return Response(
            "Authentification requise",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="JobHunter"'},
        )
