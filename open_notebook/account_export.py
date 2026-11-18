"""Build a per-account OpenNotebook data archive without reading other owners."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from surreal_commands.repository import repo_query as queue_query
from surrealdb import RecordID  # type: ignore

from open_notebook.account_inventory import CHECKPOINT_TABLES, PERSONAL_TABLES
from open_notebook.config import (
    LANGGRAPH_CHECKPOINT_FILE,
    PODCASTS_FOLDER,
    UPLOADS_FOLDER,
)
from open_notebook.database.repository import repo_query
from open_notebook.database.tenant_access import (
    account_mode_enabled,
    legacy_admin_id,
    migration_scope,
)
from open_notebook.storage.account_file_gate import account_file_gate

_ACCOUNT_DIRECTORY = re.compile(r"^[0-9a-f]{32}$")


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, RecordID):
        return str(value)
    raise TypeError(f"Cannot export value of type {type(value).__name__}")


def _write_json(archive: ZipFile, name: str, data) -> None:
    archive.writestr(
        name,
        json.dumps(data, ensure_ascii=False, default=_json_value).encode("utf-8"),
    )


def _file_paths(root: str | Path, account_id: UUID) -> list[tuple[Path, str]]:
    base = Path(root).resolve()
    target = base / account_id.hex
    roots: list[Path] = []
    if target.exists() or target.is_symlink():
        if target.is_symlink() or target.resolve() != target or not target.is_dir():
            raise ValueError("Account storage is not a regular directory")
        roots.append(target)
    if account_id == legacy_admin_id() and base.is_dir():
        for child in base.iterdir():
            if child == target:
                continue
            if child.is_dir() and _ACCOUNT_DIRECTORY.fullmatch(child.name):
                continue
            roots.append(child)

    result: list[tuple[Path, str]] = []
    pending = roots[:]
    while pending:
        path = pending.pop()
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise ValueError("Account storage contains an unsafe path")
        if path.is_dir():
            with os.scandir(path) as entries:
                pending.extend(Path(item.path) for item in entries)
        elif path.is_file():
            result.append((path, path.relative_to(base).as_posix()))
        else:
            raise ValueError("Account storage contains a special file")
    return sorted(result, key=lambda item: item[1])


def _checkpoint_rows(db_file: str | Path, account_id: UUID) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {table: [] for table in CHECKPOINT_TABLES}
    path = Path(db_file)
    if not path.is_file():
        return result
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        existing = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table in CHECKPOINT_TABLES:
            if table in existing:
                result[table] = [
                    dict(row)
                    for row in db.execute(
                        f"SELECT * FROM {table} WHERE thread_id LIKE ?",
                        (f"{account_id.hex}:%",),
                    )
                ]
    return result


def _write_media_files(archive: ZipFile, account_id: UUID) -> None:
    with account_file_gate(account_id):
        for root, label in ((UPLOADS_FOLDER, "uploads"), (PODCASTS_FOLDER, "podcasts")):
            for path, relative in _file_paths(root, account_id):
                archive.write(path, f"{label}/{relative}", compress_type=ZIP_DEFLATED)


async def write_account_archive(account_id: UUID, archive: ZipFile) -> None:
    """Write owned records and files into an already-open ZIP file."""
    if (
        not account_mode_enabled()
        or not isinstance(account_id, UUID)
        or account_id.int == 0
    ):
        raise ValueError("A valid account-mode UUID is required")
    owner = RecordID("account", account_id.hex)
    with migration_scope():
        account = await repo_query("SELECT active FROM $account", {"account": owner})
        if not account or account[0].get("active") is not True:
            raise PermissionError("Only an active account can export its data")
        counts: dict[str, int] = {}
        for table in PERSONAL_TABLES:
            rows = await repo_query(
                f"SELECT * FROM {table} WHERE owner = $owner", {"owner": owner}
            )
            counts[table] = len(rows)
            _write_json(archive, f"records/{table}.json", rows)

    jobs = await queue_query(
        "SELECT * FROM command WHERE context.tagent_account = $account",
        {"account": account_id.hex},
    )
    for job in jobs or []:
        context = job.get("context")
        if isinstance(context, dict):
            context.pop("tagent_proof", None)
    _write_json(archive, "records/background-jobs.json", jobs or [])
    checkpoints = await asyncio.to_thread(
        _checkpoint_rows, LANGGRAPH_CHECKPOINT_FILE, account_id
    )
    for table, rows in checkpoints.items():
        _write_json(archive, f"chat-checkpoints/{table}.json", rows)
    await asyncio.to_thread(_write_media_files, archive, account_id)
    _write_json(
        archive,
        "manifest.json",
        {
            "format": "open-notebook-account-export-v1",
            "account_id": str(account_id),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "record_counts": counts,
            "checkpoint_counts": {
                key: len(value) for key, value in checkpoints.items()
            },
            "job_count": len(jobs or []),
        },
    )
