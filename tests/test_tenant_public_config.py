"""The public account-mode configuration must not require database identity."""

from unittest.mock import AsyncMock

import pytest

from api.routers import config


@pytest.mark.asyncio
async def test_public_config_does_not_report_false_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setattr(config, "get_version", lambda: "test")
    monkeypatch.setattr(
        config, "get_latest_version_cached", AsyncMock(return_value=(None, False))
    )
    health = AsyncMock(side_effect=AssertionError("public query reached tenant DB"))
    monkeypatch.setattr(config, "check_database_health", health)

    response = await config.get_config(None)  # type: ignore[arg-type]

    assert response == {"version": "test", "latestVersion": None, "hasUpdate": False}
    health.assert_not_awaited()
