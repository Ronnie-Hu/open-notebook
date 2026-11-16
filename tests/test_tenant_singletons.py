"""Account-scoped settings retain only the legacy administrator's old rows."""

from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from open_notebook.ai.models import DefaultModels
from open_notebook.database.tenant_access import (
    account_scope,
    scoped_singleton_record_id,
)
from open_notebook.domain.content_settings import ContentSettings
from open_notebook.domain.provider_config import ProviderConfig
from open_notebook.domain.transformation import DefaultPrompts

ADMIN = UUID("1de64c8e-f61e-4ab3-abfa-b8bd7a6b34bc")
STUDENT_A = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
STUDENT_B = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")


@pytest.fixture
def account_mode(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(ADMIN))


@pytest.mark.parametrize(
    "model",
    [DefaultModels, ContentSettings, DefaultPrompts, ProviderConfig],
)
def test_all_fixed_settings_have_distinct_account_records(account_mode, model) -> None:
    with pytest.raises(PermissionError):
        model.effective_record_id()
    with account_scope(ADMIN, role="admin"):
        assert model.effective_record_id() == model.record_id
    with account_scope(STUDENT_A):
        first = model.effective_record_id()
    with account_scope(STUDENT_B):
        second = model.effective_record_id()
    assert first == f"{model.record_id}_{STUDENT_A.hex}"
    assert second == f"{model.record_id}_{STUDENT_B.hex}"
    assert first != second


@pytest.mark.asyncio
async def test_content_settings_instances_and_updates_do_not_cross_accounts(
    account_mode,
) -> None:
    seen: list[str] = []

    async def fake_query(_query, variables):
        record_id = str(variables["record_id"])
        seen.append(record_id)
        return [{"default_embedding_option": "always"}]

    with (
        patch("open_notebook.domain.base.repo_query", side_effect=fake_query),
        patch(
            "open_notebook.domain.base.repo_upsert", new_callable=AsyncMock
        ) as upsert,
    ):
        with account_scope(STUDENT_A):
            first = cast(ContentSettings, await ContentSettings.get_instance())
            first.default_embedding_option = "never"
            await first.update()
        with account_scope(STUDENT_B):
            second = cast(ContentSettings, await ContentSettings.get_instance())
        assert first is not second
        assert first.default_embedding_option == "always"
        assert second.default_embedding_option == "always"
        assert upsert.await_args is not None
        assert str(upsert.await_args.args[1]) == (
            f"{ContentSettings.record_id}_{STUDENT_A.hex}"
        )
    assert seen == [
        f"{ContentSettings.record_id}_{STUDENT_A.hex}",
        f"{ContentSettings.record_id}_{STUDENT_A.hex}",
        f"{ContentSettings.record_id}_{STUDENT_B.hex}",
    ]


@pytest.mark.asyncio
async def test_model_defaults_and_provider_config_use_scoped_rows(account_mode) -> None:
    with (
        patch(
            "open_notebook.ai.models.repo_query", new_callable=AsyncMock
        ) as models_query,
        patch(
            "open_notebook.domain.provider_config.repo_query", new_callable=AsyncMock
        ) as provider_query,
        patch(
            "open_notebook.domain.provider_config.repo_upsert", new_callable=AsyncMock
        ) as provider_upsert,
    ):
        models_query.return_value = []
        provider_query.return_value = []
        with account_scope(STUDENT_A):
            await DefaultModels.get_instance()
            config = await ProviderConfig.get_instance()
            await config.save()
        assert models_query.await_args is not None
        assert provider_query.await_args is not None
        assert provider_upsert.await_args is not None
        assert str(models_query.await_args.args[1]["record_id"]) == (
            f"{DefaultModels.record_id}_{STUDENT_A.hex}"
        )
        assert str(provider_query.await_args.args[1]["record_id"]) == (
            f"{ProviderConfig.record_id}_{STUDENT_A.hex}"
        )
        assert str(provider_upsert.await_args.args[1]) == (
            f"{ProviderConfig.record_id}_{STUDENT_A.hex}"
        )


def test_single_user_mode_keeps_existing_fixed_records(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    assert scoped_singleton_record_id(DefaultModels.record_id) == (
        "open_notebook:default_models"
    )
