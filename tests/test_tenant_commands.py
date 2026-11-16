"""Background jobs cannot switch or omit the verified TAgent account."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from langchain_core.runnables import RunnableLambda
from surreal_commands import CommandInput, ExecutionContext
from surreal_commands.core.executor import CommandExecutor

from open_notebook.database.tenant_access import (
    account_scope,
    current_account,
    current_role,
)
from open_notebook.database.tenant_commands import (
    _job_context,
    execute_command_sync,
    get_command_status,
    submit_command,
    tenant_command_handler,
)

STUDENT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
OTHER = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")


class SampleInput(CommandInput):
    value: int


def command_input(context: dict | None) -> SampleInput:
    return SampleInput(
        value=7,
        execution_context=ExecutionContext(
            command_id="command:test",
            execution_started_at=datetime.now(),
            app_name="open_notebook",
            command_name="sample",
            user_context=context,
        ),
    )


@pytest.fixture
def account_mode(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY", "t" * 48)


def test_submission_stamps_only_verified_account(account_mode) -> None:
    with patch(
        "open_notebook.database.tenant_commands.queue_submit_command",
        return_value="command:queued",
    ) as queued:
        with pytest.raises(PermissionError):
            submit_command("open_notebook", "sample", {"value": 7})
        with account_scope(STUDENT):
            assert (
                submit_command("open_notebook", "sample", {"value": 7})
                == "command:queued"
            )
            with pytest.raises(PermissionError):
                submit_command(
                    "open_notebook",
                    "sample",
                    {"value": 7},
                    {"tagent_account": OTHER.hex},
                )
    context = queued.call_args.args[3]
    assert context["tagent_account"] == STUDENT.hex
    assert context["tagent_role"] == "student"
    assert len(context["tagent_proof"]) == 64


@pytest.mark.asyncio
async def test_synchronous_queue_call_keeps_account_across_thread(account_mode) -> None:
    with patch(
        "open_notebook.database.tenant_commands.queue_execute_command_sync",
        return_value="completed",
    ) as queued:
        with account_scope(STUDENT):
            result = await asyncio.to_thread(
                execute_command_sync, "open_notebook", "sample", {"value": 7}
            )
    assert result == "completed"
    assert queued.call_args.args[3]["tagent_account"] == STUDENT.hex


@pytest.mark.asyncio
async def test_worker_binds_signed_owner_and_rejects_tampering(account_mode) -> None:
    observed: list[tuple[UUID | None, str | None]] = []

    @tenant_command_handler
    async def handler(input_data: SampleInput) -> int:
        observed.append((current_account(), current_role()))
        return input_data.value

    with account_scope(STUDENT):
        signed = _job_context("open_notebook", "sample", None)
    assert signed is not None
    assert await handler(command_input(signed)) == 7
    assert observed == [(STUDENT, "student")]
    assert current_account() is None

    forged = {**signed, "tagent_account": OTHER.hex}
    with pytest.raises(PermissionError, match="proof"):
        await handler(command_input(forged))
    with pytest.raises(PermissionError, match="missing"):
        await handler(command_input(None))
    assert observed == [(STUDENT, "student")]


def test_anonymous_jobs_keep_existing_queue_contract(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    assert _job_context("open_notebook", "sample", {"source": "existing"}) == {
        "source": "existing"
    }


@pytest.mark.asyncio
async def test_job_status_is_visible_only_to_its_owner(account_mode) -> None:
    with account_scope(STUDENT):
        signed = _job_context("open_notebook", "sample", None)
    assert signed is not None
    with (
        patch(
            "open_notebook.database.tenant_commands.queue_repo_query",
            new_callable=AsyncMock,
            return_value=[
                {"app": "open_notebook", "name": "sample", "context": signed}
            ],
        ),
        patch(
            "open_notebook.database.tenant_commands.queue_get_command_status",
            new_callable=AsyncMock,
            return_value="completed",
        ) as status,
    ):
        with account_scope(OTHER):
            with pytest.raises(ValueError, match="not found"):
                await get_command_status("command:test")
        status.assert_not_awaited()
        with account_scope(STUDENT):
            assert await get_command_status("command:test") == "completed"
        status.assert_awaited_once_with("command:test")


@pytest.mark.asyncio
async def test_signed_job_cannot_run_under_anonymous_worker(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_DB_SIGNING_KEY", "t" * 48)
    with account_scope(STUDENT):
        signed = _job_context("open_notebook", "sample", None)
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE")

    @tenant_command_handler
    async def handler(input_data: SampleInput) -> int:
        return input_data.value

    with pytest.raises(RuntimeError, match="anonymous worker"):
        await handler(command_input(signed))


@pytest.mark.asyncio
async def test_queue_executor_injects_signed_context_before_handler(
    account_mode,
) -> None:
    @tenant_command_handler
    async def handler(input_data: SampleInput) -> str:
        assert current_account() == STUDENT
        return str(input_data.value)

    with account_scope(STUDENT):
        signed = _job_context("open_notebook", "sample", None)
    runnable = RunnableLambda(handler)
    executor = CommandExecutor({"open_notebook.sample": runnable})
    parsed = executor.parse_input(runnable, {"value": 7})
    context = command_input(signed).execution_context
    assert await executor.execute_async("open_notebook.sample", parsed, context) == "7"
