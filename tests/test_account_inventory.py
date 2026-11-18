"""Keep account deletion inventory complete and contained before any purge."""

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from open_notebook import account_inventory as inventory


def test_inventory_tables_match_every_owned_table_in_migration_27():
    migration = (
        Path(__file__).resolve().parents[1]
        / "open_notebook/database/migrations/27.surrealql"
    ).read_text(encoding="utf-8")
    owned = {
        line.split()[4].rstrip(";")
        for line in migration.splitlines()
        if line.startswith("DEFINE FIELD owner ON ")
    }
    assert set(inventory.PERSONAL_TABLES) == owned


def test_file_stats_only_count_the_exact_account_directory(tmp_path: Path):
    account = uuid4()
    own = tmp_path / account.hex
    nested = own / "nested"
    nested.mkdir(parents=True)
    (own / "one.txt").write_bytes(b"one")
    (nested / "two.txt").write_bytes(b"four")
    (tmp_path / "other.txt").write_bytes(b"private")
    assert inventory.account_file_stats(tmp_path, account) == inventory.FileStats(2, 7)
    assert inventory.account_file_stats(tmp_path, uuid4()) == inventory.FileStats()


def test_file_stats_reject_symlinks_in_account_directory(tmp_path: Path):
    account = uuid4()
    own = tmp_path / account.hex
    own.mkdir()
    try:
        (own / "shortcut").symlink_to(tmp_path, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"OS does not allow test symlinks: {error}")
    with pytest.raises(ValueError, match="symlink"):
        inventory.account_file_stats(tmp_path, account)


def test_checkpoint_counts_use_account_prefix_and_do_not_create_a_database(
    tmp_path: Path,
):
    account = uuid4()
    db_path = tmp_path / "checkpoints.sqlite"
    assert inventory.account_checkpoint_counts(db_path, account) == {
        "checkpoints": 0,
        "writes": 0,
    }
    assert not db_path.exists()
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE checkpoints (thread_id TEXT)")
        db.execute("CREATE TABLE writes (thread_id TEXT)")
        for table in inventory.CHECKPOINT_TABLES:
            db.executemany(
                f"INSERT INTO {table} (thread_id) VALUES (?)",
                [(f"{account.hex}:chat:1",), (f"{uuid4().hex}:chat:1",)],
            )
    assert inventory.account_checkpoint_counts(db_path, account) == {
        "checkpoints": 1,
        "writes": 1,
    }


@pytest.mark.asyncio
async def test_inventory_counts_all_stores_without_reading_contents(
    monkeypatch, tmp_path
):
    account = uuid4()
    (tmp_path / "uploads" / account.hex).mkdir(parents=True)
    (tmp_path / "uploads" / account.hex / "document.pdf").write_bytes(b"abc")
    (tmp_path / "podcasts").mkdir()
    monkeypatch.setattr(inventory, "UPLOADS_FOLDER", tmp_path / "uploads")
    monkeypatch.setattr(inventory, "PODCASTS_FOLDER", tmp_path / "podcasts")
    monkeypatch.setattr(
        inventory, "LANGGRAPH_CHECKPOINT_FILE", tmp_path / "none.sqlite"
    )
    monkeypatch.setattr(inventory, "account_mode_enabled", lambda: True)

    calls = []

    async def fake_record_query(statement, variables):
        calls.append((statement, variables))
        return [{"total": 2}]

    async def fake_queue_query(statement, variables):
        assert variables == {"account": account.hex}
        return [{"status": "new"}, {"status": "completed"}]

    monkeypatch.setattr(inventory, "repo_query", fake_record_query)
    monkeypatch.setattr(inventory, "queue_query", fake_queue_query)
    result = await inventory.collect_account_inventory(account)
    assert len(calls) == len(inventory.PERSONAL_TABLES)
    assert all("WHERE owner = $owner" in statement for statement, _ in calls)
    assert all(
        str(variables["owner"]) == f"account:{account.hex}" for _, variables in calls
    )
    assert all(count == 2 for count in result.records.values())
    assert result.uploads == inventory.FileStats(1, 3)
    assert result.podcasts == inventory.FileStats()
    assert result.jobs == {"new": 1, "completed": 1}
    assert result.as_dict()["account_id"] == str(account)
