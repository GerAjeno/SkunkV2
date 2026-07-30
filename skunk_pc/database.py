from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .config import settings


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    printer_name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    fit_mode TEXT NOT NULL CHECK (fit_mode IN ('contain', 'cover')),
    orientation TEXT NOT NULL CHECK (orientation IN ('auto', 'portrait', 'landscape')),
    copies INTEGER NOT NULL CHECK (copies BETWEEN 1 AND 20),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'processing', 'submitted', 'completed', 'failed', 'cancelled')
    ),
    pages INTEGER NOT NULL DEFAULT 0,
    cups_job_ids TEXT NOT NULL DEFAULT '',
    source_device TEXT NOT NULL DEFAULT 'Página web',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created
ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS print_history (
    id TEXT PRIMARY KEY,
    printer_name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    source_device TEXT NOT NULL,
    status TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_print_history_created
ON print_history(created_at);
"""


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also releases the file."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(
        path or settings.database_path,
        timeout=15,
        factory=ClosingConnection,
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def init_database(path: Path | None = None) -> None:
    db_path = path or settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as db:
        db.executescript(SCHEMA)
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "source_device" not in columns:
            db.execute(
                "ALTER TABLE jobs ADD COLUMN source_device TEXT "
                "NOT NULL DEFAULT 'Página web'"
            )


@contextmanager
def transaction(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
