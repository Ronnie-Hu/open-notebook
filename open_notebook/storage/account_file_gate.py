"""Coordinate in-flight uploads with account disablement and media cleanup."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID

from filelock import FileLock

from open_notebook.config import DATA_FOLDER


def _paths(account_id: UUID) -> tuple[Path, Path]:
    if not isinstance(account_id, UUID) or account_id.int == 0:
        raise ValueError("A valid account UUID is required")
    root = Path(DATA_FOLDER).resolve() / "account-lifecycle"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{account_id.hex}.lock", root / f"{account_id.hex}.disabled"


@contextmanager
def account_file_gate(account_id: UUID, *, disabling: bool = False) -> Iterator[None]:
    """Hold the same process-shared lock for file creation and removal."""
    lock_path, disabled_path = _paths(account_id)
    with FileLock(str(lock_path), timeout=30):
        if disabling:
            disabled_path.touch(exist_ok=True)
        elif disabled_path.exists():
            raise PermissionError("Account has been disabled for file writes")
        yield
