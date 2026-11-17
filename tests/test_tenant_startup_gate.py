"""Account mode must reject invalid deployment settings before migration."""

from unittest.mock import AsyncMock

import pytest

from api import main as api_main


@pytest.mark.asyncio
async def test_account_mode_requires_https_before_database_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_PUBLIC_ORIGIN", "http://example.org:8502")
    migration = AsyncMock()
    monkeypatch.setattr(api_main, "_run_database_migrations", migration)

    with pytest.raises(ValueError, match="HTTPS public origin"):
        async with api_main.lifespan(api_main.app):
            pass
    migration.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_mode_requires_encryption_before_database_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_PUBLIC_ORIGIN", "https://example.org:8502")
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_VERIFIER_URL", "http://127.0.0.1:5001")
    monkeypatch.setattr(api_main, "get_secret_from_env", lambda _name: None)
    migration = AsyncMock()
    monkeypatch.setattr(api_main, "_run_database_migrations", migration)

    with pytest.raises(RuntimeError, match="OPEN_NOTEBOOK_ENCRYPTION_KEY"):
        async with api_main.lifespan(api_main.app):
            pass
    migration.assert_not_awaited()
