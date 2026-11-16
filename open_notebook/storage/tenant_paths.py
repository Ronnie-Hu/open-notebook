"""Keep new uploads in their owner's directory in TAgent account mode."""

from pathlib import Path

from open_notebook.config import UPLOADS_FOLDER
from open_notebook.database.tenant_access import (
    account_mode_enabled,
    current_account,
    current_role,
)


def upload_folder(*, uploads_root: str | None = None) -> Path:
    root = Path(uploads_root or UPLOADS_FOLDER).resolve()
    if not account_mode_enabled():
        return root
    actor = current_account()
    if actor is None:
        raise PermissionError("Verified TAgent account is required for uploads")
    return root / actor.hex


def validate_upload_path(file_path: str, *, uploads_root: str | None = None) -> Path:
    """Resolve symlinks and reject paths outside this account's upload area."""
    root = Path(uploads_root or UPLOADS_FOLDER).resolve()
    target = Path(file_path).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("Invalid file path: must be within the uploads directory")
    if not account_mode_enabled():
        return target
    actor = current_account()
    if actor is None:
        raise PermissionError("Verified TAgent account is required for uploads")
    if target.is_relative_to(root / actor.hex):
        return target
    # Original single-user uploads were written directly in the shared root.
    # Migration 27 gives their source rows to the administrator only.
    if current_role() == "admin" and target.parent == root:
        return target
    raise ValueError("Invalid file path: upload belongs to another account")
