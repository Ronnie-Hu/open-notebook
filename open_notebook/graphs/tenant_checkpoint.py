"""Partition shared LangGraph SQLite checkpoint keys by verified account."""

from open_notebook.database.tenant_access import (
    account_mode_enabled,
    current_account,
    current_role,
    legacy_admin_id,
)


def checkpoint_thread_id(session_id: str) -> str:
    if not account_mode_enabled():
        return session_id
    actor = current_account()
    if actor is None:
        raise PermissionError("Verified TAgent account is required for chat")
    # Migration 27 gives the old chat sessions to this administrator. Preserve
    # their original checkpoint keys so those messages remain available.
    if current_role() == "admin" and actor == legacy_admin_id():
        return session_id
    return f"{actor.hex}:{session_id}"
