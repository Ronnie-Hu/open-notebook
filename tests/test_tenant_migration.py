"""The account migration owns legacy rows and enforces database isolation."""

# The embedded SDK exposes a broad dynamic SurrealQL result union.
# mypy: ignore-errors

from uuid import UUID

import pytest
from surrealdb import Surreal

from open_notebook.database import async_migrate
from open_notebook.database.async_migrate import (
    AsyncMigrationManager,
    _account_migration,
)
from open_notebook.database.tenant_access import sign_record_token

ADMIN = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")
STUDENT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
SECRET = "a" * 48


def _configured(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY", SECRET)


def test_tenant_migration_requires_valid_inputs(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.delenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", raising=False)
    with pytest.raises(ValueError, match="administrator"):
        _account_migration()
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(ADMIN))
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY", "too-short")
    with pytest.raises(ValueError, match="signing key"):
        _account_migration()


@pytest.mark.asyncio
async def test_anonymous_mode_refuses_a_tenant_database(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    manager = AsyncMigrationManager()

    async def tenant_version() -> int:
        return 27

    monkeypatch.setattr(manager, "get_current_version", tenant_version)
    with pytest.raises(RuntimeError, match="newer than this deployment mode"):
        await manager.needs_migration()


@pytest.mark.asyncio
async def test_migration_error_does_not_advance_version(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    class FailedConnection:
        async def query(self, _sql):
            return "schema failed"

    @asynccontextmanager
    async def failed_connection():
        yield FailedConnection()

    advanced = False

    async def bump():
        nonlocal advanced
        advanced = True

    monkeypatch.setattr(async_migrate, "db_connection", failed_connection)
    monkeypatch.setattr(async_migrate, "bump_version", bump)
    with pytest.raises(RuntimeError, match="schema failed"):
        await async_migrate.AsyncMigration("invalid").run()
    assert not advanced


def test_tenant_migration_blocks_legacy_rows_for_student(monkeypatch) -> None:
    _configured(monkeypatch)
    migration = _account_migration()
    assert "@LEGACY_ADMIN_HEX@" not in migration.sql
    assert "@TAGENT_SIGNING_KEY@" not in migration.sql
    assert len(AsyncMigrationManager().up_migrations) == 27

    db = Surreal("mem://")
    db.connect()
    try:
        db.use("tenant_migration", "tenant_migration")
        for table in (
            "notebook",
            "source",
            "source_embedding",
            "source_insight",
            "note",
            "transformation",
            "episode_profile",
            "speaker_profile",
            "episode",
            "credential",
        ):
            db.query(f"DEFINE TABLE {table} SCHEMAFULL")
        for table in ("chat_session", "podcast_config"):
            db.query(f"DEFINE TABLE {table} SCHEMALESS")
        for table, source, target in (
            ("reference", "source", "notebook"),
            ("artifact", "note", "notebook"),
            ("refers_to", "chat_session", "notebook"),
        ):
            db.query(f"DEFINE TABLE {table} TYPE RELATION FROM {source} TO {target}")
        db.query("DEFINE FIELD name ON notebook TYPE string")
        db.query("CREATE notebook:legacy SET name = 'admin notebook'")
        result = db.query(migration.sql)
        assert not isinstance(result, str), result

        db.query("CREATE account:" + STUDENT.hex + " SET active = true")
        db.authenticate(
            sign_record_token(
                STUDENT,
                namespace="tenant_migration",
                database="tenant_migration",
                secret=SECRET,
            )
        )
        assert db.query("SELECT * FROM notebook") == []
        created = db.query(
            "CREATE notebook SET name = 'student notebook', "
            "owner = type::thing('account', $student)",
            {"student": STUDENT.hex},
        )
        assert len(created) == 1, created
        db.authenticate(
            sign_record_token(
                ADMIN,
                namespace="tenant_migration",
                database="tenant_migration",
                secret=SECRET,
            )
        )
        assert [row["name"] for row in db.query("SELECT * FROM notebook")] == [
            "admin notebook"
        ]
    finally:
        db.close()
