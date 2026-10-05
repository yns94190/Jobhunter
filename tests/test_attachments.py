from pathlib import Path

import pytest

from app.config import settings
from app.services import attachments as attachments_service
from app.services.attachments import sanitize_filename

PDF = b"%PDF-1.4\n% faux pdf de test\n"


@pytest.fixture(autouse=True)
def attachments_dir(tmp_path, monkeypatch) -> Path:
    """Chaque test écrit dans un dossier temporaire, jamais dans data/."""
    directory = tmp_path / "attachments"
    monkeypatch.setattr(settings, "attachments_dir", str(directory))
    return directory


def _upload(client, name="CV.pdf", content=PDF, content_type="application/pdf"):
    return client.post("/attachments", files={"file": (name, content, content_type)})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CV.pdf", "CV.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\windows\\cv.pdf", "cv.pdf"),
        ("C:\\Users\\moi\\Lettre Été.docx", "Lettre_Ete.docx"),
        ("..", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_upload_list_and_file_stored(client, attachments_dir):
    response = _upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "CV.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["size"] == len(PDF)
    assert body["is_default"] is False

    stored = attachments_dir / body["path"]
    assert stored.read_bytes() == PDF
    assert stored.parent == attachments_dir

    listing = client.get("/attachments").json()
    assert [a["id"] for a in listing] == [body["id"]]


def test_upload_path_traversal_stays_in_storage_dir(client, attachments_dir):
    response = _upload(client, name="../../../evil.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "evil.pdf"
    assert (attachments_dir / body["path"]).exists()
    assert "/" not in body["path"] and ".." not in body["path"]


@pytest.mark.parametrize("name", ["script.exe", "page.html", "CV.doc", "sans_extension", "archive.pdf.zip"])
def test_upload_rejects_forbidden_extensions(client, name):
    response = _upload(client, name=name)
    assert response.status_code == 415


def test_upload_rejects_content_not_matching_extension(client):
    response = _upload(client, name="CV.pdf", content=b"MZ\x90\x00 executable")
    assert response.status_code == 415


def test_upload_rejects_too_large_file(client, attachments_dir):
    too_big = PDF + b"0" * (attachments_service.MAX_SIZE)
    response = _upload(client, content=too_big)
    assert response.status_code == 413
    assert not attachments_dir.exists() or not any(attachments_dir.iterdir())


def test_upload_accepts_file_at_size_limit(client):
    content = PDF + b"0" * (attachments_service.MAX_SIZE - len(PDF))
    assert _upload(client, content=content).status_code == 201


def test_upload_rejects_empty_file(client):
    assert _upload(client, content=b"").status_code == 400


def test_upload_accepts_png_jpg_docx_odt(client):
    assert _upload(client, "photo.png", b"\x89PNG\r\n\x1a\n...", "image/png").status_code == 201
    assert _upload(client, "photo.jpg", b"\xff\xd8\xff\xe0...", "image/jpeg").status_code == 201
    assert _upload(client, "lettre.docx", b"PK\x03\x04...", "application/octet-stream").status_code == 201
    assert _upload(client, "lettre.odt", b"PK\x03\x04...", "application/octet-stream").status_code == 201


def test_patch_toggles_and_sets_default(client):
    attachment_id = _upload(client).json()["id"]

    assert client.patch(f"/attachments/{attachment_id}").json()["is_default"] is True
    assert client.patch(f"/attachments/{attachment_id}").json()["is_default"] is False
    response = client.patch(f"/attachments/{attachment_id}", json={"is_default": True})
    assert response.json()["is_default"] is True
    response = client.patch(f"/attachments/{attachment_id}", json={"is_default": True})
    assert response.json()["is_default"] is True


def test_delete_removes_row_and_file(client, attachments_dir):
    body = _upload(client).json()
    stored = attachments_dir / body["path"]

    assert client.delete(f"/attachments/{body['id']}").status_code == 204
    assert not stored.exists()
    assert client.get("/attachments").json() == []


def test_unknown_attachment_returns_404(client):
    assert client.patch("/attachments/999").status_code == 404
    assert client.delete("/attachments/999").status_code == 404
