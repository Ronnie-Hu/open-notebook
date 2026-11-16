"""Uploads and file-path inputs stay within the verified account directory."""

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException, UploadFile

from api.routers.sources import (
    _is_source_file_available,
    _resolve_source_file,
    save_uploaded_file,
)
from open_notebook.database.tenant_access import account_scope
from open_notebook.domain.notebook import Asset, Source
from open_notebook.storage import tenant_paths

STUDENT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
OTHER = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")


def test_student_and_admin_path_boundaries(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setattr(tenant_paths, "UPLOADS_FOLDER", str(tmp_path))
    own = tmp_path / STUDENT.hex / "own.pdf"
    other = tmp_path / OTHER.hex / "other.pdf"
    legacy = tmp_path / "legacy.pdf"
    with pytest.raises(PermissionError):
        tenant_paths.upload_folder()
    with account_scope(STUDENT):
        assert tenant_paths.upload_folder() == tmp_path / STUDENT.hex
        assert tenant_paths.validate_upload_path(str(own)) == own
        with pytest.raises(ValueError, match="another account"):
            tenant_paths.validate_upload_path(str(other))
        with pytest.raises(ValueError, match="another account"):
            tenant_paths.validate_upload_path(str(legacy))
    with account_scope(OTHER, role="admin"):
        assert tenant_paths.validate_upload_path(str(legacy)) == legacy
        with pytest.raises(ValueError, match="another account"):
            tenant_paths.validate_upload_path(str(own))


@pytest.mark.asyncio
async def test_new_upload_is_written_under_verified_account(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setattr(tenant_paths, "UPLOADS_FOLDER", str(tmp_path))
    monkeypatch.setattr("api.routers.sources.UPLOADS_FOLDER", str(tmp_path))
    upload = UploadFile(file=BytesIO(b"private data"), filename="report.txt")
    with account_scope(STUDENT):
        stored = await save_uploaded_file(upload)
    assert Path(stored).parent == tmp_path / STUDENT.hex
    assert Path(stored).read_bytes() == b"private data"


def test_single_user_paths_still_use_original_upload_root(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    monkeypatch.setattr(tenant_paths, "UPLOADS_FOLDER", str(tmp_path))
    assert tenant_paths.upload_folder() == tmp_path
    assert tenant_paths.validate_upload_path(str(tmp_path / "legacy.pdf")) == (
        tmp_path / "legacy.pdf"
    )


@pytest.mark.asyncio
async def test_other_account_file_cannot_be_downloaded_or_deleted(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setattr(tenant_paths, "UPLOADS_FOLDER", str(tmp_path))
    monkeypatch.setattr("api.routers.sources.UPLOADS_FOLDER", str(tmp_path))
    other_dir = tmp_path / OTHER.hex
    other_dir.mkdir()
    other_file = other_dir / "secret.pdf"
    other_file.write_bytes(b"another account's document")
    source = Source(
        id="source:other",
        title="Other account's source",
        asset=Asset(file_path=str(other_file)),
    )
    with account_scope(STUDENT):
        assert _is_source_file_available(source) is False
        with patch(
            "api.routers.sources.Source.get", new=AsyncMock(return_value=source)
        ):
            with pytest.raises(HTTPException) as error:
                await _resolve_source_file("source:other")
            assert error.value.status_code == 403
        with pytest.raises(ValueError, match="another account"):
            await source.delete()
    assert other_file.read_bytes() == b"another account's document"


def test_symlink_to_other_account_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setattr(tenant_paths, "UPLOADS_FOLDER", str(tmp_path))
    own_dir = tmp_path / STUDENT.hex
    other_dir = tmp_path / OTHER.hex
    own_dir.mkdir()
    other_dir.mkdir()
    secret = other_dir / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    shortcut = own_dir / "shortcut.txt"
    try:
        shortcut.symlink_to(secret)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symlinks")
        raise
    with account_scope(STUDENT):
        with pytest.raises(ValueError, match="another account"):
            tenant_paths.validate_upload_path(str(shortcut))
