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


def test_repeated_contexts_do_not_leak_file_descriptors(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    database.init_database(path)
    before = len(os.listdir("/proc/self/fd"))

    for _ in range(100):
        with database.connect(path) as db:
            db.execute("SELECT 1").fetchone()

    after = len(os.listdir("/proc/self/fd"))
    assert after <= before + 2
