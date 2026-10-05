from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlmodel import Session

from app.config import settings
from app.models import Attachment

logger = logging.getLogger(__name__)

MAX_SIZE = 5 * 1024 * 1024  # 5 Mo par fichier

# Extension autorisée -> type MIME enregistré (on ne fait pas confiance au navigateur)
ALLOWED_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "png": "image/png",
    "jpg": "image/jpeg",
}

# Signature des premiers octets : un .exe renommé en .pdf est refusé
MAGIC_BYTES = {
    "pdf": (b"%PDF",),
    "docx": (b"PK\x03\x04",),
    "odt": (b"PK\x03\x04",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
}


class AttachmentError(ValueError):
    """Document refusé ; status_code est renvoyé tel quel par l'API."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def storage_dir() -> Path:
    directory = Path(settings.attachments_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_filename(raw: str | None) -> str:
    """Ne garde que le nom de base, sans accents ni caractères spéciaux.

    "../../etc/passwd" -> "passwd", "CV Été 2026.pdf" -> "CV_Ete_2026.pdf"
    """
    # Les navigateurs Windows peuvent envoyer un chemin complet avec des antislashs
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name[:120]


def _resolve(attachment: Attachment) -> Path:
    """Chemin absolu du fichier, garanti à l'intérieur du dossier de stockage."""
    base = storage_dir().resolve()
    target = (base / attachment.path).resolve()
    if base not in target.parents:
        raise AttachmentError("chemin de fichier invalide")
    return target


def read_bytes(attachment: Attachment) -> bytes:
    try:
        return _resolve(attachment).read_bytes()
    except FileNotFoundError as exc:
        raise AttachmentError(f"fichier manquant : {attachment.filename}", 404) from exc


async def save_upload(session: Session, upload: UploadFile) -> Attachment:
    """Valide puis enregistre un document téléversé."""
    filename = sanitize_filename(upload.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not filename or extension not in ALLOWED_TYPES:
        raise AttachmentError(
            "extension refusée : " + ", ".join(ALLOWED_TYPES) + " uniquement", 415
        )

    # On lit un octet de plus que la limite pour détecter un fichier trop gros
    content = await upload.read(MAX_SIZE + 1)
    if len(content) > MAX_SIZE:
        raise AttachmentError("fichier trop volumineux (5 Mo maximum)", 413)
    if not content:
        raise AttachmentError("fichier vide")
    if not content.startswith(MAGIC_BYTES[extension]):
        raise AttachmentError(f"le contenu ne correspond pas à un fichier .{extension}", 415)

    # Nom de stockage unique : deux "CV.pdf" ne s'écrasent pas
    stored_name = f"{uuid.uuid4().hex}_{filename}"
    (storage_dir() / stored_name).write_bytes(content)

    attachment = Attachment(
        filename=filename,
        content_type=ALLOWED_TYPES[extension],
        size=len(content),
        path=stored_name,
    )
    session.add(attachment)
    session.commit()
    session.refresh(attachment)
    logger.info("Document enregistre : %s (%d octets)", filename, len(content))
    return attachment


def delete_attachment(session: Session, attachment: Attachment) -> None:
    """Supprime l'entrée en base et le fichier sur disque."""
    try:
        _resolve(attachment).unlink(missing_ok=True)
    except AttachmentError:
        logger.warning("Chemin suspect ignore a la suppression : %s", attachment.path)
    session.delete(attachment)
    session.commit()
