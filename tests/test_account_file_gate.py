"""A disabled account cannot create a late upload after cleanup starts."""

from uuid import uuid4

import pytest

from api.routers import sources
from open_notebook.storage import account_file_gate


def test_upload_writer_rechecks_account_tombstone_inside_file_lock(
    monkeypatch, tmp_path
):
    account = uuid4()
    monkeypatch.setattr(account_file_gate, "DATA_FOLDER", tmp_path)
    monkeypatch.setattr(sources, "account_mode_enabled", lambda: True)
    monkeypatch.setattr(sources, "current_account", lambda: account)
    upload_dir = tmp_path / "uploads" / account.hex

    first = sources._write_uploaded_file("first.txt", b"before", str(upload_dir))
    assert first.endswith("first.txt")
    with account_file_gate.account_file_gate(account, disabling=True):
        pass
    with pytest.raises(PermissionError, match="disabled"):
        sources._write_uploaded_file("late.txt", b"after", str(upload_dir))
    assert not (upload_dir / "late.txt").exists()
