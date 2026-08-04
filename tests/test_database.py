import os
import sqlite3

import pytest

from skunk_pc import database


def test_connection_context_closes_database_file(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    database.init_database(path)
    connection = database.connect(path)

    with connection as db:
        assert db.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_init_database_migrates_old_jobs_table_to_support_trim_fit_mode(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE jobs (
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
            """
        )
        db.execute(
            """
            INSERT INTO jobs (
                id, printer_name, original_name, content_type, source_path,
                fit_mode, orientation, copies, status, created_at
            ) VALUES ('old-job', 'Gabriela', 'etiqueta.pdf', 'application/pdf', '/tmp/x',
                'contain', 'auto', 1, 'completed', '2026-01-01T00:00:00+00:00')
            """
        )
        db.commit()

    database.init_database(path)

    with database.connect(path) as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "source_page" in columns
        row = db.execute("SELECT * FROM jobs WHERE id = 'old-job'").fetchone()
        assert row["original_name"] == "etiqueta.pdf"
        assert row["source_page"] is None
        db.execute(
            """
            INSERT INTO jobs (
                id, printer_name, original_name, content_type, source_path,
                fit_mode, orientation, copies, status, created_at, source_page
            ) VALUES ('new-job', 'Gabriela', 'guia.pdf', 'application/pdf', '/tmp/y',
                'trim', 'auto', 1, 'queued', '2026-01-01T00:00:00+00:00', 1)
            """
        )
        db.commit()


def test_repeated_contexts_do_not_leak_file_descriptors(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    database.init_database(path)
    before = len(os.listdir("/proc/self/fd"))

    for _ in range(100):
        with database.connect(path) as db:
            db.execute("SELECT 1").fetchone()

    after = len(os.listdir("/proc/self/fd"))
    assert after <= before + 2
