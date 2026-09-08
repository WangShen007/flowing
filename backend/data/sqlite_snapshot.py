"""Copy a consistent SQLite snapshot to a NEW private file; never overwrite.

This copies one database, including committed WAL transactions. To back up the
application and graph databases as a coordinated set, stop writers first. Secrets,
uploaded files and CLI profiles must be backed up separately and securely.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def snapshot_database(source: Path, destination: Path) -> dict[str, object]:
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source must be an existing SQLite file")
    destination = destination.absolute()
    if not destination.parent.is_dir():
        raise ValueError("Destination directory must already exist")
    # Exclusive creation rejects existing files and symlinks, including the source.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(destination)) as writer:
                reader.backup(writer)
                writer.execute("PRAGMA journal_mode=DELETE")
                result = writer.execute("PRAGMA quick_check").fetchall()
                if result != [("ok",)]:
                    raise ValueError("Snapshot failed SQLite integrity validation")
        digest = hashlib.sha256()
        with destination.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"path": str(destination), "bytes": destination.stat().st_size,
                "sha256": digest.hexdigest(), "quick_check": "ok"}
    except BaseException:
        # Only the newly created destination belongs to this invocation.
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot_database(args.source, args.destination)))


if __name__ == "__main__":
    main()
