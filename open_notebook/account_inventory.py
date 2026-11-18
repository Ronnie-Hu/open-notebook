"""Read-only inventory of every account-owned OpenNotebook storage class."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from surreal_commands.repository import repo_query as queue_query
from surrealdb import RecordID  # type: ignore

from open_notebook.config import (
    LANGGRAPH_CHECKPOINT_FILE,
    PODCASTS_FOLDER,
    UPLOADS_FOLDER,
)
from open_notebook.database.repository import repo_query
from open_notebook.database.tenant_access import account_mode_enabled, migration_scope

# Keep this list synchronized with migration 27's `DEFINE FIELD owner ON` lines.
# An explicit list makes newly added personal tables an auditable schema change.
PERSONAL_TABLES = (
    "notebook",
    "source",
    "source_embedding",
    "source_insight",
    "note",
    "reference",
    "artifact",
    "chat_session",
    "refers_to",
    "transformation",
    "episode_profile",
    "speaker_profile",
    "episode",
    "credential",
    "model",
    "open_notebook",
    "podcast_config",
)
CHECKPOINT_TABLES = ("checkpoints", "writes")


@dataclass(frozen=True)
class FileStats:
    files: int = 0
    bytes: int = 0


@dataclass(frozen=True)
class AccountInventory:
    account_id: str
    records: dict[str, int]
    uploads: FileStats
    podcasts: FileStats
    checkpoints: dict[str, int]
    jobs: dict[str, int]

    def as_dict(self) -> dict:
        return asdict(self)


def account_file_stats(root: str | Path, account_id: UUID) -> FileStats:
    """Count only the exact account directory; reject symlinks and special files."""
    base = Path(root).resolve()
    target = base / account_id.hex
    if not target.exists() and not target.is_symlink():
        return FileStats()
    if target.is_symlink() or target.resolve() != target or not target.is_dir():
        raise ValueError("Account storage is not a regular directory")
    files = size = 0
    pending = [target]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for item in entries:
                if item.is_symlink():
                    raise ValueError("Account storage contains a symlink")
                if not Path(item.path).resolve().is_relative_to(target):
                    raise ValueError("Account storage escapes its directory")
                if item.is_dir(follow_symlinks=False):
                    pending.append(Path(item.path))
                elif item.is_file(follow_symlinks=False):
                    files += 1
                    size += item.stat(follow_symlinks=False).st_size
                else:
                    raise ValueError("Account storage contains a special file")
    return FileStats(files=files, bytes=size)


def account_checkpoint_counts(db_file: str | Path, account_id: UUID) -> dict[str, int]:
    """Count account-prefixed LangGraph rows without creating a SQLite file."""
    path = Path(db_file)
    counts = {table: 0 for table in CHECKPOINT_TABLES}
    if not path.is_file():
        return counts
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
        existing = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table in CHECKPOINT_TABLES:
            if table in existing:
                counts[table] = int(
                    db.execute(
                        f"SELECT count(*) FROM {table} WHERE thread_id LIKE ?",
                        (f"{account_id.hex}:%",),
                    ).fetchone()[0]
                )
    return counts


async def collect_account_inventory(account_id: UUID) -> AccountInventory:
    """Use a bound owner ID so inventory still works after account disablement."""
    if not account_mode_enabled():
        raise RuntimeError("Account inventory requires OpenNotebook account mode")
    if not isinstance(account_id, UUID) or account_id.int == 0:
        raise ValueError("A valid account UUID is required")
    with migration_scope():
        records: dict[str, int] = {}
        for table in PERSONAL_TABLES:
            rows = await repo_query(
                f"SELECT count() AS total FROM {table} WHERE owner = $owner GROUP ALL",
                {"owner": RecordID("account", account_id.hex)},
            )
            records[table] = int(rows[0]["total"]) if rows else 0
    jobs: dict[str, int] = {}
    rows = await queue_query(
        "SELECT status FROM command WHERE context.tagent_account = $account",
        {"account": account_id.hex},
    )
    for row in rows or []:
        status = str(row.get("status") or "unknown")
        jobs[status] = jobs.get(status, 0) + 1
    return AccountInventory(
        account_id=str(account_id),
        records=records,
        uploads=account_file_stats(UPLOADS_FOLDER, account_id),
        podcasts=account_file_stats(PODCASTS_FOLDER, account_id),
        checkpoints=account_checkpoint_counts(LANGGRAPH_CHECKPOINT_FILE, account_id),
        jobs=jobs,
    )
