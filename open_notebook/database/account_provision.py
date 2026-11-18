"""Provision the minimal SurrealDB record for a verified TAgent account."""

import asyncio
import os
import time
from typing import Any
from uuid import UUID

from surrealdb import AsyncSurreal, RecordID  # type: ignore

from open_notebook.database.repository import (
    get_database_name,
    get_database_namespace,
    get_database_password,
    get_database_url,
)
from open_notebook.database.tenant_access import account_mode_enabled

_recent: dict[UUID, float] = {}
_lock = asyncio.Lock()
_RECHECK_SECONDS = 60.0


async def ensure_account_record(account_id: UUID) -> None:
    """Create an exact account record only after TAgent verified the session.

    Record-token authentication requires this row to exist. Never accept an ID
    from a request body or header; the API middleware passes the verified ID.
    """
    if not account_mode_enabled():
        raise RuntimeError("TAgent account provisioning requires account mode")
    if not isinstance(account_id, UUID) or account_id.int == 0:
        raise ValueError("A verified account UUID is required")
    now = time.monotonic()
    if _recent.get(account_id, 0.0) > now:
        return
    async with _lock:
        now = time.monotonic()
        if _recent.get(account_id, 0.0) > now:
            return
        db = AsyncSurreal(get_database_url())
        try:
            await db.signin(
                {
                    "username": os.environ.get("SURREAL_USER"),
                    "password": get_database_password(),
                }
            )
            await db.use(get_database_namespace(), get_database_name())
            params: dict[str, Any] = {"account": RecordID("account", account_id.hex)}

            async def active_row() -> bool | None:
                rows: Any = await db.query("SELECT active FROM $account;", params)
                if not isinstance(rows, list):
                    raise RuntimeError("Account record lookup failed")
                if not rows:
                    return None
                if not isinstance(rows[0], dict):
                    raise RuntimeError("Account record lookup failed")
                return rows[0].get("active") is True

            active = await active_row()
            if active is False:
                raise PermissionError("OpenNotebook account is disabled")
            if active is None:
                # CREATE fails for an existing ID. Concurrent API processes may
                # race to create the same first-login row; re-read in that case.
                try:
                    created = await db.query(
                        "CREATE $account SET active = true;", params
                    )
                except Exception:
                    created = None
                if not created or isinstance(created, str):
                    if await active_row() is not True:
                        raise RuntimeError("Account record provisioning failed")
        finally:
            await db.close()
        _recent[account_id] = time.monotonic() + _RECHECK_SECONDS
