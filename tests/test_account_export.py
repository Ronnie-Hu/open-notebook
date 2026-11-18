"""The account archive includes only owned files and rejects unsafe links."""

import json
import sqlite3
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest

from open_notebook import account_export
from open_notebook.account_export import _file_paths
from open_notebook.storage import account_file_gate


def test_export_file_selection_includes_admin_legacy_but_no_other_account(
    monkeypatch, tmp_path: Path
):
    admin = uuid4()
    student = uuid4()
    other = uuid4()
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(admin))
    (tmp_path / student.hex).mkdir()
    (tmp_path / student.hex / "own.txt").write_text("student", encoding="utf-8")
    (tmp_path / other.hex).mkdir()
    (tmp_path / other.hex / "private.txt").write_text("other", encoding="utf-8")
    (tmp_path / "legacy.txt").write_text("admin", encoding="utf-8")

    assert [name for _, name in _file_paths(tmp_path, student)] == [
        f"{student.hex}/own.txt"
    ]
    assert [name for _, name in _file_paths(tmp_path, admin)] == ["legacy.txt"]


def test_export_rejects_symlink_inside_account_directory(monkeypatch, tmp_path: Path):
    admin = uuid4()
    student = uuid4()
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(admin))
    own = tmp_path / student.hex
    own.mkdir()
    try:
        (own / "shortcut").symlink_to(tmp_path, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"OS does not allow test symlinks: {error}")
    with pytest.raises(ValueError, match="unsafe path"):
        _file_paths(tmp_path, student)


@pytest.mark.asyncio
async def test_archive_contains_owned_media_checkpoints_and_redacted_job_proof(
    monkeypatch, tmp_path: Path
):
    admin = uuid4()
    student = uuid4()
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(admin))
    monkeypatch.setattr(account_export, "account_mode_enabled", lambda: True)
    monkeypatch.setattr(account_file_gate, "DATA_FOLDER", tmp_path)
    uploads = tmp_path / "uploads"
    podcasts = tmp_path / "podcasts"
    for root in (uploads, podcasts):
        (root / student.hex).mkdir(parents=True)
        (root / student.hex / "own.txt").write_text("student", encoding="utf-8")
        (root / admin.hex).mkdir()
        (root / admin.hex / "private.txt").write_text("admin", encoding="utf-8")
    monkeypatch.setattr(account_export, "UPLOADS_FOLDER", uploads)
    monkeypatch.setattr(account_export, "PODCASTS_FOLDER", podcasts)
    checkpoints = tmp_path / "checkpoints.sqlite"
    with sqlite3.connect(checkpoints) as db:
        db.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint BLOB)")
        db.execute("CREATE TABLE writes (thread_id TEXT, value BLOB)")
        for table, field in (("checkpoints", "checkpoint"), ("writes", "value")):
            db.executemany(
                f"INSERT INTO {table} (thread_id, {field}) VALUES (?, ?)",
                [(f"{student.hex}:chat", b"own"), (f"{admin.hex}:chat", b"secret")],
            )
    monkeypatch.setattr(account_export, "LANGGRAPH_CHECKPOINT_FILE", checkpoints)

    async def records(statement, _vars):
        if statement.startswith("SELECT active"):
            return [{"active": True}]
        return (
            [{"id": "note:own", "owner": f"account:{student.hex}"}]
            if "FROM note " in statement
            else []
        )

    async def jobs(_statement, _vars):
        return [
            {
                "context": {"tagent_account": student.hex, "tagent_proof": "hidden"},
                "result": {"answer": "own"},
            }
        ]

    monkeypatch.setattr(account_export, "repo_query", records)
    monkeypatch.setattr(account_export, "queue_query", jobs)
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        await account_export.write_account_archive(student, archive)
    with ZipFile(output) as archive:
        names = archive.namelist()
        assert f"uploads/{student.hex}/own.txt" in names
        assert f"podcasts/{student.hex}/own.txt" in names
        assert not any(admin.hex in name for name in names)
        assert json.loads(archive.read("records/note.json"))[0]["id"] == "note:own"
        assert "hidden" not in archive.read("records/background-jobs.json").decode()
        checkpoint_rows = json.loads(archive.read("chat-checkpoints/checkpoints.json"))
        assert len(checkpoint_rows) == 1
        assert checkpoint_rows[0]["thread_id"] == f"{student.hex}:chat"
        assert json.loads(archive.read("manifest.json"))["record_counts"]["note"] == 1
