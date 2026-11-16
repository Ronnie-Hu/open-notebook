"""Account mode never silently falls back to the database root identity."""

# Embedded SurrealQL query results have a dynamic SDK union.
# mypy: ignore-errors

from uuid import UUID

import pytest
from surrealdb import AsyncSurreal

from open_notebook.database import repository
from open_notebook.database.tenant_access import (
    account_scope,
    migration_scope,
    sign_record_token,
)

ACCOUNT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")


class FakeDatabase:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def use(self, namespace: str, database: str) -> None:
        self.calls.append(f"use:{namespace}/{database}")

    async def authenticate(self, token: str) -> None:
        assert len(token.split(".")) == 3
        self.calls.append("authenticate")

    async def signin(self, credentials: dict) -> None:
        self.calls.append("signin")

    async def close(self) -> None:
        self.calls.append("close")


@pytest.mark.asyncio
async def test_account_mode_rejects_missing_actor_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setattr(
        repository, "AsyncSurreal", lambda _: pytest.fail("DB should not open")
    )
    with pytest.raises(PermissionError, match="Verified TAgent account"):
        async with repository.db_connection():
            pytest.fail("No database access expected")


@pytest.mark.asyncio
async def test_verified_actor_authenticates_as_record_not_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY", "a" * 32)
    db = FakeDatabase()
    monkeypatch.setattr(repository, "AsyncSurreal", lambda _: db)
    with account_scope(ACCOUNT):
        async with repository.db_connection() as opened:
            assert opened is db
    assert db.calls == ["use:open_notebook/open_notebook", "authenticate", "close"]
    with pytest.raises(PermissionError):
        async with repository.db_connection():
            pytest.fail("Account scope leaked")


@pytest.mark.asyncio
async def test_missing_signing_key_never_uses_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY_FILE", raising=False)
    db = FakeDatabase()
    monkeypatch.setattr(repository, "AsyncSurreal", lambda _: db)
    with account_scope(ACCOUNT):
        with pytest.raises(RuntimeError, match="signing key"):
            async with repository.db_connection():
                pytest.fail("No database access expected")
    assert db.calls == ["close"]


@pytest.mark.asyncio
async def test_only_migration_scope_keeps_system_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    db = FakeDatabase()
    monkeypatch.setattr(repository, "AsyncSurreal", lambda _: db)
    with migration_scope():
        async with repository.db_connection():
            pass
    assert db.calls == ["signin", "use:open_notebook/open_notebook", "close"]
    with pytest.raises(PermissionError):
        async with repository.db_connection():
            pytest.fail("Migration scope leaked")


@pytest.mark.asyncio
async def test_existing_single_user_mode_preserves_root_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    db = FakeDatabase()
    monkeypatch.setattr(repository, "AsyncSurreal", lambda _: db)
    async with repository.db_connection():
        pass
    assert db.calls == ["signin", "use:open_notebook/open_notebook", "close"]


@pytest.mark.asyncio
async def test_locked_async_sdk_accepts_record_identity() -> None:
    """Exercise the use-before-authenticate sequence against the real SDK."""
    db = AsyncSurreal("mem://")
    account = ACCOUNT.hex
    secret = "test-only-signing-key-longer-than-32-bytes"
    try:
        await db.use("tagent_async_probe", "notebook_probe")
        await db.query("DEFINE TABLE account SCHEMALESS")
        await db.query(
            "DEFINE ACCESS tagent_account ON DATABASE TYPE RECORD "
            "SIGNIN (SELECT * FROM account WHERE email = $email "
            "AND crypto::argon2::compare(pass, $pass)) "
            f"WITH JWT ALGORITHM HS512 KEY '{secret}' "
            "DURATION FOR TOKEN 1h, FOR SESSION 1h"
        )
        await db.query(
            "DEFINE TABLE notebook SCHEMALESS PERMISSIONS "
            "FOR select WHERE owner = $auth.id"
        )
        await db.query(
            "CREATE type::thing('account', $id) SET email = 'test@example.test'",
            {"id": account},
        )
        await db.query(
            "CREATE notebook SET owner = type::thing('account', $id), title = 'mine'",
            {"id": account},
        )
        await db.authenticate(
            sign_record_token(
                ACCOUNT,
                namespace="tagent_async_probe",
                database="notebook_probe",
                secret=secret,
            )
        )
        assert [
            row["title"] for row in await db.query("SELECT title FROM notebook")
        ] == ["mine"]
    finally:
        await db.close()
