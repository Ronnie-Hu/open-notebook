"""Opt-in tenant isolation check against a real SurrealDB 2 server.

Set RUN_SURREAL2_TENANT_PROBE=1 and SURREAL2_TENANT_PROBE_URL/PASSWORD to run.
The test creates a unique namespace and database on that local test server.
"""

import os
from uuid import UUID, uuid4

import pytest
from surrealdb import RecordID

from open_notebook.account_inventory import collect_account_inventory
from open_notebook.database.account_schema import assert_account_mode_schema
from open_notebook.database.async_migrate import AsyncMigrationManager
from open_notebook.database.repository import db_connection
from open_notebook.database.tenant_access import account_scope, migration_scope

ADMIN = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")
STUDENT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_SURREAL2_TENANT_PROBE") != "1",
    reason="Requires an isolated SurrealDB 2 test server",
)
async def test_real_surreal2_migration_and_account_isolation(monkeypatch) -> None:
    url = os.environ["SURREAL2_TENANT_PROBE_URL"]
    password = os.environ["SURREAL2_TENANT_PROBE_PASSWORD"]
    namespace = f"tenant_probe_{uuid4().hex}"
    monkeypatch.setenv("SURREAL_URL", url)
    monkeypatch.setenv("SURREAL_USER", "root")
    monkeypatch.setenv("SURREAL_PASSWORD", password)
    monkeypatch.setenv("SURREAL_NAMESPACE", namespace)
    monkeypatch.setenv("SURREAL_DATABASE", "notebook")
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY", "a" * 48)

    manager = AsyncMigrationManager()
    with migration_scope():
        for migration in manager.up_migrations[:-1]:
            await migration.run()
        async with db_connection() as db:
            await db.query("CREATE notebook:legacy SET name = 'admin notebook';")
            await db.query(
                "CREATE note:legacy SET title = 'quokka admin note', "
                "content = 'quokka admin secret', embedding = [1.0, 0.0];"
            )
        await manager.up_migrations[-1].run()
        async with db_connection() as db:
            await db.query(f"UPSERT account:{STUDENT.hex} SET active = true;")
    await assert_account_mode_schema()

    with account_scope(STUDENT):
        async with db_connection() as db:
            assert await db.query("SELECT * FROM notebook;") == []
            created = await db.query(
                "CREATE notebook SET name = 'student notebook', "
                "owner = type::thing('account', $student);",
                {"student": STUDENT.hex},
            )
            assert len(created) == 1, created
            assert await db.query("SELECT * FROM notebook:legacy;") == []
            own_note = await db.query(
                "CREATE note:student SET title = 'quokka student note', "
                "content = 'quokka student secret', embedding = [1.0, 0.0], "
                "owner = type::thing('account', $student);",
                {"student": STUDENT.hex},
            )
            assert len(own_note) == 1, own_note
            # Database policy must reject owner spoofing and guessed record IDs,
            # even when a caller bypasses the Python repository helpers.
            forged = await db.query(
                "CREATE note:forged SET title = 'forged', "
                "content = 'forged', embedding = [1.0, 0.0], "
                "owner = type::thing('account', $admin);",
                {"admin": ADMIN.hex},
            )
            assert forged == []
            assert await db.query("UPDATE note:legacy SET content = 'changed';") == []
            assert await db.query("DELETE note:legacy;") == []
            text_hits = await db.query(
                "RETURN fn::text_search('quokka', 10, false, true, NONE);"
            )
            assert "student" in str(text_hits), text_hits
            assert "admin" not in str(text_hits), text_hits
            vector_hits = await db.query(
                "RETURN fn::vector_search([1.0, 0.0], 10, false, true, 0.5, NONE);"
            )
            assert "student" in str(vector_hits), vector_hits
            assert "admin" not in str(vector_hits), vector_hits

    with account_scope(ADMIN, role="admin"):
        async with db_connection() as db:
            rows = await db.query("SELECT name FROM notebook;")
            assert [row["name"] for row in rows] == ["admin notebook"]
            assert await db.query("SELECT * FROM note:student;") == []
            legacy = await db.query("SELECT content FROM note:legacy;")
            assert legacy[0]["content"] == "quokka admin secret"
            assert await db.query("SELECT * FROM note:forged;") == []

    # The same record-token boundary powers the read-only deletion inventory.
    student_inventory = await collect_account_inventory(STUDENT)
    assert student_inventory.records["notebook"] == 1
    assert student_inventory.records["note"] == 1
    admin_inventory = await collect_account_inventory(ADMIN)
    assert admin_inventory.records["notebook"] == 1
    assert admin_inventory.records["note"] == 1

    with migration_scope():
        async with db_connection() as db:
            changed = await db.query(
                "UPDATE $account SET active = false;",
                {"account": RecordID("account", STUDENT.hex)},
            )
            assert changed, changed
    with account_scope(STUDENT):
        async with db_connection() as db:
            assert await db.query("SELECT * FROM notebook;") == []
            assert (
                await db.query(
                    "CREATE notebook SET name = 'after disable', "
                    "owner = type::thing('account', $student);",
                    {"student": STUDENT.hex},
                )
                == []
            )
    disabled_inventory = await collect_account_inventory(STUDENT)
    assert disabled_inventory.records["notebook"] == 1
    assert disabled_inventory.records["note"] == 1

    with migration_scope():
        async with db_connection() as db:
            await db.query(
                "DEFINE TABLE OVERWRITE notebook SCHEMAFULL PERMISSIONS "
                "FOR select, create, update, delete WHERE owner = $auth.id;"
            )
    with pytest.raises(RuntimeError, match="lacks active account permissions"):
        await assert_account_mode_schema()

    with migration_scope():
        async with db_connection() as db:
            await db.query(
                "DEFINE TABLE OVERWRITE notebook SCHEMAFULL PERMISSIONS "
                "FOR select WHERE owner = $auth.id AND $auth.active = true "
                "FOR create, update, delete WHERE owner = $auth.id;"
            )
    with pytest.raises(RuntimeError, match="lacks active account permissions"):
        await assert_account_mode_schema()
