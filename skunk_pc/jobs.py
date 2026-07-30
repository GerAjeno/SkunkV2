from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
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


def sync_cups_history() -> None:
    cups_jobs = list_cups_jobs(500)
    if not cups_jobs:
        return
    now = utc_now()
    with connect() as db:
        for job in cups_jobs:
            db.execute(
                """
                INSERT INTO print_history (
                    id, printer_name, original_name, source_device, status,
                    pages, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    printer_name = excluded.printer_name,
                    original_name = excluded.original_name,
                    source_device = excluded.source_device,
                    status = excluded.status,
                    pages = excluded.pages,
                    error = excluded.error,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    job["cups_job_id"],
                    job["printer_name"],
                    job["original_name"],
                    job["source_device"],
                    job["status"],
                    job.get("pages", 0),
                    job.get("error"),
                    job["created_at"],
                    now,
                ),
            )
        db.commit()


def cleanup_history() -> None:
    cutoff = (
        datetime.now(UTC) - timedelta(hours=max(settings.job_retention_hours, 1))
    ).isoformat()
    with connect() as db:
        db.execute("DELETE FROM print_history WHERE created_at < ?", (cutoff,))
        db.execute(
            """
            DELETE FROM jobs
            WHERE created_at < ?
              AND status NOT IN ('queued', 'processing')
            """,
            (cutoff,),
        )
        db.commit()


def list_all_jobs(
    *,
    page: int = 1,
    page_size: int = 30,
    printer: str = "",
    status: str = "",
    origin: str = "",
    created_after: str = "",
    created_before: str = "",
) -> dict:
    """Return a filtered page combining web and persisted native jobs."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    combined = filtered_jobs(
        printer=printer,
        status=status,
        origin=origin,
        created_after=created_after,
        created_before=created_before,
    )
    total = len(combined)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    start = (page - 1) * page_size
    return {
        "jobs": combined[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
    }


def filtered_jobs(
    *,
    printer: str = "",
    status: str = "",
    origin: str = "",
    created_after: str = "",
    created_before: str = "",
) -> list[dict]:
    """Return all filtered history rows, newest first."""
    sync_cups_history()
    cleanup_history()
    with connect() as db:
        web_jobs = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
        ]
        cups_jobs = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM print_history ORDER BY created_at DESC"
            ).fetchall()
        ]
    cups_by_id = {job["id"]: job for job in cups_jobs}
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

    native_jobs = []
    for job in cups_jobs:
        if job["id"] in represented_ids:
            continue
        native_jobs.append(
            {
                "id": f"cups:{job['id']}",
                "original_name": job["original_name"],
                "printer_name": job["printer_name"],
                "status": job["status"],
                "pages": job.get("pages", 0),
                "created_at": job["created_at"],
                "error": job.get("error"),
                "source_device": job["source_device"],
                "source": "native",
            }
        )
    combined = web_jobs + native_jobs
    printer_filter = printer.casefold().strip()
    status_filter = status.casefold().strip()
    origin_filter = origin.casefold().strip()
    combined = [
        job
        for job in combined
        if (not printer_filter or job["printer_name"].casefold() == printer_filter)
        and (not status_filter or job["status"].casefold() == status_filter)
        and (
            not origin_filter
            or origin_filter in (job.get("source_device") or "").casefold()
        )
        and (not created_after or job["created_at"] >= created_after)
        and (not created_before or job["created_at"] < created_before)
    ]
    combined.sort(
        key=lambda job: _sortable_datetime(job.get("created_at")),
        reverse=True,
    )
    return combined


def _sortable_datetime(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def recover_interrupted_jobs() -> list[dict]:
    """Fail jobs owned by the previous worker process after a restart.

    Skunk PC runs a single systemd-managed worker. Therefore, any row still
    marked as processing before the new worker begins claiming jobs belongs to
    the process that just stopped. Retrying it could duplicate labels if CUPS
    accepted part of the submission before the interruption.
    """
    finished_at = utc_now()
    with transaction() as db:
        rows = db.execute(
            "SELECT * FROM jobs WHERE status = 'processing' ORDER BY created_at"
        ).fetchall()
        if not rows:
            return []
        db.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error = ?,
                finished_at = ?
            WHERE status = 'processing'
            """,
            (
                "Trabajo interrumpido por el reinicio del servidor; "
                "no se reenvió para evitar etiquetas duplicadas",
                finished_at,
            ),
        )
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
