import sqlite3

import pytest

from data.sqlite_snapshot import snapshot_database


def test_snapshot_includes_committed_wal_and_restores_to_new_database(tmp_path):
    source, backup, restored = [tmp_path / name for name in ("live.sqlite3", "backup.sqlite3", "restored.sqlite3")]
    # Keep the WAL connection open: copying only the .sqlite3 file is insufficient.
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO records VALUES (1, 'retained')")
        connection.commit()
        report = snapshot_database(source, backup)
        assert report["quick_check"] == "ok"
        assert backup.stat().st_mode & 0o777 == 0o600
        assert not backup.with_name(backup.name + "-wal").exists()
    snapshot_database(backup, restored)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM records").fetchall() == [("retained",)]


def test_snapshot_never_overwrites_existing_destination(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records(id INTEGER)")
    target.write_text("preserve")
    with pytest.raises(FileExistsError):
        snapshot_database(source, target)
    assert target.read_text() == "preserve"
    with pytest.raises(FileExistsError):
        snapshot_database(source, source)


def test_invalid_source_removes_only_new_destination(tmp_path):
    source, target = tmp_path / "not-sqlite", tmp_path / "new.sqlite3"
    source.write_text("not a database")
    with pytest.raises(sqlite3.DatabaseError):
        snapshot_database(source, target)
    assert source.read_text() == "not a database"
    assert not target.exists()
