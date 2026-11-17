"""Per-account mode cannot publish one student's key into process globals."""

import os
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from api import credentials_service
from api.routers import credentials as credentials_router
from open_notebook.ai.key_provider import provision_provider_keys
from open_notebook.ai.models import (
    Model,
    ModelManager,
    resolve_anthropic_compatible_config,
    validate_personal_model_config,
)
from open_notebook.database.tenant_access import account_scope
from open_notebook.exceptions import ConfigurationError

STUDENT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")


@pytest.mark.asyncio
async def test_account_mode_rejects_process_wide_key_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "existing-server-value")
    before = dict(os.environ)
    with pytest.raises(ConfigurationError, match="personal model credential"):
        await provision_provider_keys("openai")
    assert dict(os.environ) == before


@pytest.mark.asyncio
async def test_student_cannot_see_or_import_server_model_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-key")
    monkeypatch.setattr(
        credentials_service.Credential,
        "get_by_provider",
        AsyncMock(return_value=[]),
    )
    with account_scope(STUDENT):
        assert (await credentials_service.get_env_status())["openai"] is False
        status = await credentials_service.get_provider_status()
        assert status["configured"]["openai"] is False
        assert status["source"]["openai"] == "none"
        with pytest.raises(PermissionError, match="server model keys"):
            await credentials_service.migrate_from_env()
        with pytest.raises(HTTPException) as error:
            await credentials_router.migrate_from_env()
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_student_model_needs_personal_key_and_external_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_API_KEY", "server-only-key")
    monkeypatch.setenv("ANTHROPIC_COMPATIBLE_BASE_URL", "https://example.com")
    with account_scope(STUDENT):
        with pytest.raises(ConfigurationError, match="personal model API key"):
            await validate_personal_model_config({}, "openai")
        with pytest.raises(ConfigurationError, match="external HTTPS base URL"):
            await validate_personal_model_config({}, "ollama")
        with pytest.raises(ConfigurationError, match="external HTTPS"):
            await validate_personal_model_config(
                {"api_key": "personal", "base_url": "http://127.0.0.1:11434"},
                "openai_compatible",
            )
        with pytest.raises(ConfigurationError, match="base URL and API key"):
            await resolve_anthropic_compatible_config({})


@pytest.mark.asyncio
async def test_student_unlinked_model_cannot_use_server_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "server-only-key")
    model = Model(name="gpt-4", provider="openai", type="language")
    monkeypatch.setattr(Model, "get", AsyncMock(return_value=model))
    with account_scope(STUDENT):
        with pytest.raises(ConfigurationError, match="personal model credential"):
            await ModelManager().get_model("model:unlinked")
