"""Podcast files follow the verified account, including worker output."""

from uuid import UUID

import pytest

from commands.podcast_commands import build_episode_output_dir
from open_notebook.database.tenant_access import account_scope
from open_notebook.podcasts import audio_paths

ADMIN = UUID("1de64c8e-f61e-4ab3-abfa-b8bd7a6b34bc")
STUDENT = UUID("db9f0e0c-4d8e-4a84-8b9a-c7576450212d")
OTHER = UUID("5fc55ced-5acd-4fc8-bf17-0eb8fe36a10f")


@pytest.fixture
def account_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", "1")
    monkeypatch.setenv("OPEN_NOTEBOOK_LEGACY_ADMIN_ID", str(ADMIN))
    monkeypatch.setattr(audio_paths, "PODCASTS_FOLDER", str(tmp_path))
    return tmp_path


def test_worker_output_and_audio_reads_are_account_scoped(account_audio) -> None:
    with pytest.raises(PermissionError):
        build_episode_output_dir(str(account_audio))
    with account_scope(STUDENT):
        _, output_dir = build_episode_output_dir(str(account_audio))
        assert output_dir.parent == account_audio / STUDENT.hex / "episodes"
        audio = output_dir / "out.mp3"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"student audio")
        relative = audio_paths.to_relative_audio_path(audio)
        assert relative.startswith(f"{STUDENT.hex}/episodes/")
        assert audio_paths.resolve_contained_audio_path(relative) == audio.resolve()
    with account_scope(OTHER):
        assert audio_paths.resolve_contained_audio_path(relative) is None
        with pytest.raises(ValueError, match="another account"):
            audio_paths.to_relative_audio_path(audio)


def test_only_designated_admin_can_read_legacy_audio(account_audio) -> None:
    legacy = account_audio / "episodes" / "old" / "out.mp3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy audio")
    relative = "episodes/old/out.mp3"
    with account_scope(STUDENT):
        assert audio_paths.resolve_contained_audio_path(relative) is None
    with account_scope(OTHER, role="admin"):
        assert audio_paths.resolve_contained_audio_path(relative) is None
    with account_scope(ADMIN, role="admin"):
        assert audio_paths.resolve_contained_audio_path(relative) == legacy.resolve()
        with pytest.raises(ValueError, match="another account"):
            audio_paths.to_relative_audio_path(legacy)


def test_anonymous_deployment_keeps_original_audio_layout(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE", raising=False)
    _, output_dir = build_episode_output_dir(str(tmp_path))
    assert output_dir.parent == tmp_path / "episodes"
