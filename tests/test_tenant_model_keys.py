"""Per-account mode cannot publish one student's key into process globals."""

import os

import pytest

from open_notebook.ai.key_provider import provision_provider_keys
from open_notebook.exceptions import ConfigurationError


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
