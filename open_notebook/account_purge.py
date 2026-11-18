"""Retryable cleanup for a disabled student account in account mode.

This service is deliberately separate from the public API. The TAgent identity
service must disable and revoke the student first, then invoke it through an
authenticated internal lifecycle route.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from uuid import UUID

from surreal_commands.repository import repo_query as queue_query
from surrealdb import RecordID  # type: ignore

from open_notebook.account_inventory import (
    CHECKPOINT_TABLES,
    PERSONAL_TABLES,
    AccountInventory,
    collect_account_inventory,
)
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


class AccountPurgePending(RuntimeError):
    """A worker is still running an account job; retry cleanup later."""


def _student_id(account_id: UUID) -> UUID:
    if not account_mode_enabled():
        raise RuntimeError("Account purge requires OpenNotebook account mode")
    if not isinstance(account_id, UUID) or account_id.int == 0:
        raise ValueError("A valid account UUID is required")
    if account_id == legacy_admin_id():
        raise PermissionError("The legacy administrator cannot be purged")
    return account_id


async def disable_account_data(account_id: UUID) -> None:
    """Make new record-token reads and writes fail before any cleanup begins."""
    owner = RecordID("account", _student_id(account_id).hex)
    with migration_scope():
        await repo_query(
            "UPSERT $account SET active = false RETURN AFTER", {"account": owner}
        )
    await asyncio.to_thread(_mark_file_disabled, account_id)


def _mark_file_disabled(account_id: UUID) -> None:
    with account_file_gate(account_id, disabling=True):
        pass


def _delete_account_directory(root: str | Path, account_id: UUID) -> None:
    """Unlink exact-account files one by one and remove only empty directories."""
    base = Path(root).resolve()
    target = base / account_id.hex
    if not target.exists() and not target.is_symlink():
        return
    if target.is_symlink() or target.resolve() != target or not target.is_dir():
        raise ValueError("Account storage is not a regular directory")

    files: list[Path] = []
    directories = [target]
    pending = [target]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for item in entries:
                path = Path(item.path)
                if item.is_symlink() or not path.resolve().is_relative_to(target):
                    raise ValueError("Account storage contains an unsafe path")
                if item.is_dir(follow_symlinks=False):
                    directories.append(path)
                    pending.append(path)
                elif item.is_file(follow_symlinks=False):
                    files.append(path)
                else:
                    raise ValueError("Account storage contains a special file")
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Account storage changed during cleanup")
        path.unlink()
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        path.rmdir()


def _delete_checkpoints(db_file: str | Path, account_id: UUID) -> None:
    path = Path(db_file)
    if not path.is_file():
        return
    with sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=5
    ) as db:
        existing = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table in CHECKPOINT_TABLES:
            if table in existing:
                db.execute(
                    f"DELETE FROM {table} WHERE thread_id LIKE ?",
                    (f"{account_id.hex}:%",),
                )


def _delete_account_media(account_id: UUID) -> None:
    with account_file_gate(account_id, disabling=True):
        _delete_account_directory(UPLOADS_FOLDER, account_id)
        _delete_account_directory(PODCASTS_FOLDER, account_id)


async def purge_disabled_account(account_id: UUID) -> AccountInventory:
    """Idempotently remove one disabled student's owned data from every store."""
    owner = RecordID("account", _student_id(account_id).hex)
    with migration_scope():
        rows = await repo_query("SELECT active FROM $account", {"account": owner})
    if not rows or rows[0].get("active") is not False:
        raise PermissionError("OpenNotebook account must be disabled first")

    await queue_query(
        "UPDATE command SET status = 'canceled' "
        "WHERE context.tagent_account = $account AND status = 'new'",
        {"account": account_id.hex},
    )
    jobs = await queue_query(
        "SELECT status FROM command WHERE context.tagent_account = $account",
        {"account": account_id.hex},
    )
    if any(job.get("status") in {"new", "running"} for job in jobs or []):
        raise AccountPurgePending("Account jobs are still running or queued")

    with migration_scope():
        for table in PERSONAL_TABLES:
            await repo_query(f"DELETE {table} WHERE owner = $owner", {"owner": owner})
    await asyncio.to_thread(_delete_account_media, account_id)
    await asyncio.to_thread(_delete_checkpoints, LANGGRAPH_CHECKPOINT_FILE, account_id)
    await queue_query(
        "DELETE command WHERE context.tagent_account = $account",
        {"account": account_id.hex},
    )
    remaining = await collect_account_inventory(account_id)
    if (
        any(remaining.records.values())
        or remaining.uploads.files
        or remaining.podcasts.files
        or any(remaining.checkpoints.values())
        or remaining.jobs
    ):
        raise AccountPurgePending("Account data remains after cleanup")
    return remaining
