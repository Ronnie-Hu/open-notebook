"""Fail closed when an older account migration lacks deletion-state permissions."""

import re

from open_notebook.account_inventory import PERSONAL_TABLES
from open_notebook.database.repository import db_connection
from open_notebook.database.tenant_access import account_mode_enabled, migration_scope

_PERSONAL_PERMISSIONS = re.compile(
    r"\bPERMISSIONS\s+FOR\s+select\s*,\s*create\s*,\s*update\s*,\s*delete"
    r"\s+WHERE\s+owner\s*=\s*\$auth\.id\s+AND\s+\$auth\.active\s*=\s*true\b",
    re.IGNORECASE,
)


async def assert_account_mode_schema() -> None:
    if not account_mode_enabled():
        return
    with migration_scope():
        async with db_connection() as db:
            definitions = await db.query("INFO FOR DB;")
    if not isinstance(definitions, dict) or not isinstance(
        definitions.get("tables"), dict
    ):
        raise RuntimeError("OpenNotebook account schema cannot be verified")
    tables = definitions["tables"]
    for table in PERSONAL_TABLES:
        definition = tables.get(table)
        if not isinstance(definition, str) or not _PERSONAL_PERMISSIONS.search(
            definition
        ):
            raise RuntimeError(
                f"OpenNotebook {table} lacks active account permissions; "
                "apply a reviewed schema migration or restore a verified backup"
            )
