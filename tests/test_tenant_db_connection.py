"""Account mode never silently falls back to the database root identity."""

from uuid import UUID

import pytest

from open_notebook.database import repository
from open_notebook.database.tenant_access import account_scope, migration_scope

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
