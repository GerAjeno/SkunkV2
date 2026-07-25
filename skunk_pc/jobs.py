from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from .config import settings
from .database import connect, transaction, utc_now


def create_job(
    *,
    printer_name: str,
    original_name: str,
    content_type: str,
    source_path: Path,
    fit_mode: str,
    orientation: str,
    copies: int,
) -> dict:
    job_id = str(uuid.uuid4())
    created_at = utc_now()
    with connect() as db:
        db.execute(
            """
            INSERT INTO jobs (
                id, printer_name, original_name, content_type, source_path,
                fit_mode, orientation, copies, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
            """,
            (
                job_id,
                printer_name,
                original_name,
                content_type,
                str(source_path),
                fit_mode,
                orientation,
                copies,
                created_at,
            ),
        )
        db.commit()
    return get_job(job_id)


def get_job(job_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    return dict(row)


def list_jobs(limit: int = 30) -> list[dict]:
    limit = min(max(limit, 1), 100)
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_next_job() -> dict | None:
    with transaction() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        db.execute(
            "UPDATE jobs SET status = 'processing', started_at = ? WHERE id = ?",
            (utc_now(), row["id"]),
        )
        claimed = dict(row)
        claimed["status"] = "processing"
        claimed["started_at"] = utc_now()
        return claimed


def finish_job(
    job_id: str,
    *,
    status: str,
    pages: int = 0,
    cups_job_ids: list[str] | None = None,
    error: str | None = None,
) -> None:
    if status not in {"submitted", "completed", "failed", "cancelled"}:
        raise ValueError("Estado final inválido")
    with connect() as db:
        db.execute(
            """
            UPDATE jobs
            SET status = ?, pages = ?, cups_job_ids = ?, error = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                status,
                pages,
                json.dumps(cups_job_ids or []),
                error,
                utc_now(),
                job_id,
            ),
        )
        db.commit()


def cancel_job(job_id: str) -> bool:
    with connect() as db:
        cursor = db.execute(
            """
            UPDATE jobs SET status = 'cancelled', finished_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (utc_now(), job_id),
        )
        db.commit()
        return cursor.rowcount == 1


def remove_job_files(job: dict) -> None:
    source = Path(job["source_path"])
    source.unlink(missing_ok=True)
    output = settings.output_dir / job["id"]
    if output.is_dir() and output.parent == settings.output_dir:
        shutil.rmtree(output)

