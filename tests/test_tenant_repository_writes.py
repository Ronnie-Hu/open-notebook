"""The repository assigns ownership from verified server context."""

from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from open_notebook.database import repository
from open_notebook.database.repository import _owned_payload
from open_notebook.database.tenant_access import account_scope

ACCOUNT_ID = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
OTHER_ID = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")


def test_repository_assigns_owner_and_rejects_forgery(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    with pytest.raises(PermissionError, match="Verified TAgent account"):
        _owned_payload({"name": "unscoped"})

    with account_scope(ACCOUNT_ID):
        payload = _owned_payload({"name": "private"})
        assert payload["name"] == "private"
        assert str(payload["owner"]) == f"account:{ACCOUNT_ID.hex}"
        with pytest.raises(PermissionError, match="another account"):
            _owned_payload({"owner": f"account:{OTHER_ID.hex}"})


def test_single_user_repository_payload_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    payload = {"name": "single-user"}
    assert _owned_payload(payload) is payload


@pytest.mark.asyncio
async def test_all_repository_write_entrypoints_attach_verified_owner(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    writes = []

    class FakeConnection:
        async def insert(self, table, data):
            writes.append((str(table), data))
            return []

        async def query(self, query, variables=None):
            writes.append((query, variables or {}))
            return []

    @asynccontextmanager
    async def fake_connection():
        yield FakeConnection()

    monkeypatch.setattr(repository, "db_connection", fake_connection)
    with account_scope(ACCOUNT_ID):
        await repository.repo_create("note", {"content": "one"})
        await repository.repo_insert("note", [{"content": "two"}])
        await repository.repo_relate("note:one", "artifact", "notebook:one")
        await repository.repo_upsert("note", "note:one", {"content": "three"})
        await repository.repo_update("note", "note:one", {"content": "four"})

    owner = f"account:{ACCOUNT_ID.hex}"
    assert len(writes) == 5
    assert str(writes[0][1]["owner"]) == owner
    assert str(writes[1][1][0]["owner"]) == owner
    assert all(str(variables["data"]["owner"]) == owner for _, variables in writes[2:])
