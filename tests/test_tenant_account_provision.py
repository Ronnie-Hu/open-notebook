"""Only a verified UUID is provisioned, using a narrow root query."""

from uuid import UUID

import pytest

from open_notebook.database import account_provision

ACCOUNT = UUID("75449404-fb94-49f6-bd08-c01c702c36bd")


@pytest.mark.asyncio
async def test_provision_creates_only_the_verified_account(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.delenv("SURREAL_USER", raising=False)
    account_provision._recent.clear()
    calls: list[tuple[object, ...]] = []

    class FakeDatabase:
        existing = False

        async def signin(self, credentials):
            calls.append(("signin", credentials["username"]))

        async def use(self, namespace, database):
            calls.append(("use", namespace, database))

        async def query(self, query, params):
            calls.append((query, str(params["account"])))
            if query.startswith("SELECT"):
                return [{"active": True}] if self.existing else []
            self.existing = True
            return [{"id": params["account"], "active": True}]

        async def close(self):
            calls.append(("close",))

    monkeypatch.setattr(account_provision, "AsyncSurreal", lambda _url: FakeDatabase())
    await account_provision.ensure_account_record(ACCOUNT)
    await account_provision.ensure_account_record(ACCOUNT)
    assert calls == [
        ("signin", None),
        ("use", "open_notebook", "open_notebook"),
        ("SELECT active FROM $account;", f"account:{ACCOUNT.hex}"),
        ("CREATE $account SET active = true;", f"account:{ACCOUNT.hex}"),
        ("close",),
    ]


@pytest.mark.asyncio
async def test_provision_never_reactivates_a_disabled_account(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    account_provision._recent.clear()
    queries = []

    class FakeDatabase:
        async def signin(self, _credentials):
            pass

        async def use(self, _namespace, _database):
            pass

        async def query(self, query, _params):
            queries.append(query)
            return [{"active": False}]

        async def close(self):
            pass

    monkeypatch.setattr(account_provision, "AsyncSurreal", lambda _url: FakeDatabase())
    with pytest.raises(PermissionError, match="disabled"):
        await account_provision.ensure_account_record(ACCOUNT)
    assert queries == ["SELECT active FROM $account;"]


@pytest.mark.asyncio
async def test_concurrent_first_login_accepts_another_process_creation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    account_provision._recent.clear()
    queries = []

    class FakeDatabase:
        async def signin(self, _credentials):
            pass

        async def use(self, _namespace, _database):
            pass

        async def query(self, query, _params):
            queries.append(query)
            if query.startswith("CREATE"):
                raise RuntimeError("record was created by another process")
            return [] if len(queries) == 1 else [{"active": True}]

        async def close(self):
            pass

    monkeypatch.setattr(account_provision, "AsyncSurreal", lambda _url: FakeDatabase())
    await account_provision.ensure_account_record(ACCOUNT)
    assert queries == [
        "SELECT active FROM $account;",
        "CREATE $account SET active = true;",
        "SELECT active FROM $account;",
    ]


@pytest.mark.asyncio
async def test_provision_rejects_missing_or_invalid_identity(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    with pytest.raises(ValueError):
        await account_provision.ensure_account_record(UUID(int=0))
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE")
    with pytest.raises(RuntimeError, match="requires account mode"):
        await account_provision.ensure_account_record(ACCOUNT)
