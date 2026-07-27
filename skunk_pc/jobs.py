from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import settings
from .cups import list_cups_jobs
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
    source_device: str = "Página web",
) -> dict:
    job_id = str(uuid.uuid4())
    created_at = utc_now()
    with connect() as db:
        db.execute(
            """
            INSERT INTO jobs (
                id, printer_name, original_name, content_type, source_path,
                fit_mode, orientation, copies, status, created_at, source_device
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
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
                source_device[:200],
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


def list_all_jobs(limit: int = 30) -> list[dict]:
    """Combine jobs created in the web UI with native IPP/CUPS jobs."""
    limit = min(max(limit, 1), 100)
    web_jobs = list_jobs(100)
    cups_jobs = list_cups_jobs(200)
    cups_by_id = {job["cups_job_id"]: job for job in cups_jobs}
    represented_ids: set[str] = set()

    for job in web_jobs:
        try:
            cups_ids = json.loads(job.get("cups_job_ids") or "[]")
        except (TypeError, json.JSONDecodeError):
            cups_ids = []
        represented_ids.update(cups_ids)
        states = [cups_by_id[item] for item in cups_ids if item in cups_by_id]
        if states:
            if any(item["status"] == "failed" for item in states):
                failed = next(item for item in states if item["status"] == "failed")
                job["status"] = "failed"
                job["error"] = failed.get("error")
            elif all(item["status"] == "completed" for item in states):
                job["status"] = "completed"
            elif any(item["status"] == "processing" for item in states):
                job["status"] = "processing"
        job["source"] = "web"

    native_jobs = [
        {
            "id": f"cups:{job['cups_job_id']}",
            "original_name": job["original_name"],
            "printer_name": job["printer_name"],
            "status": job["status"],
            "pages": job.get("pages", 0),
            "created_at": job["created_at"],
            "error": job.get("error"),
            "source_device": job["source_device"],
            "source": "native",
        }
        for job in cups_jobs
        if job["cups_job_id"] not in represented_ids
    ]
    combined = web_jobs + native_jobs
    combined.sort(
        key=lambda job: _sortable_datetime(job.get("created_at")),
        reverse=True,
    )
    return combined[:limit]


def _sortable_datetime(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


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
