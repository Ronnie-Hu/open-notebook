"""Chat checkpoint keys remain stable per account and preserve admin history."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from open_notebook.database.tenant_access import account_scope
from open_notebook.graphs.tenant_checkpoint import checkpoint_thread_id
from open_notebook.utils.graph_utils import get_session_message_count

ADMIN = UUID("1de64c8e-f61e-4ab3-abfa-b8bd7a6b34bc")
STUDENT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
OTHER = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")
SESSION = "chat_session:example"


def test_checkpoint_key_is_scoped_and_legacy_admin_keeps_history(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(ADMIN))
    with pytest.raises(PermissionError):
        checkpoint_thread_id(SESSION)
    with account_scope(STUDENT):
        assert checkpoint_thread_id(SESSION) == f"{STUDENT.hex}:{SESSION}"
        assert checkpoint_thread_id(SESSION) == f"{STUDENT.hex}:{SESSION}"
    with account_scope(OTHER):
        assert checkpoint_thread_id(SESSION) == f"{OTHER.hex}:{SESSION}"
    with account_scope(ADMIN, role="admin"):
        assert checkpoint_thread_id(SESSION) == SESSION


@pytest.mark.asyncio
async def test_message_count_reads_account_key_through_thread(monkeypatch) -> None:
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(ADMIN))
    seen: list[str] = []

    class FakeGraph:
        def get_state(self, config):
            seen.append(config["configurable"]["thread_id"])
            return SimpleNamespace(values={"messages": ["hello"]})

    with account_scope(STUDENT):
        assert await get_session_message_count(FakeGraph(), SESSION) == 1
    assert seen == [f"{STUDENT.hex}:{SESSION}"]
    with pytest.raises(PermissionError):
        await get_session_message_count(FakeGraph(), SESSION)


def test_anonymous_mode_uses_original_checkpoint_key(monkeypatch) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    assert checkpoint_thread_id(SESSION) == SESSION
