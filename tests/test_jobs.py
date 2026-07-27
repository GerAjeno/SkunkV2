from datetime import UTC, datetime, timedelta

from skunk_pc import database, jobs


def _history_database(monkeypatch, tmp_path):
    path = tmp_path / "jobs.db"
    database.init_database(path)
    monkeypatch.setattr(jobs, "connect", lambda: database.connect(path))
    monkeypatch.setattr(jobs, "sync_cups_history", lambda: None)
    monkeypatch.setattr(jobs, "cleanup_history", lambda: None)
    return path


def test_list_all_jobs_merges_native_jobs_and_updates_web_result(
    monkeypatch, tmp_path
) -> None:
    path = _history_database(monkeypatch, tmp_path)
    with database.connect(path) as db:
        db.execute(
            """
            INSERT INTO jobs (
                id, printer_name, original_name, content_type, source_path,
                fit_mode, orientation, copies, status, pages, cups_job_ids,
                created_at, source_device
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "web-1", "Gabriela", "etiqueta.pdf", "application/pdf", "/tmp/x",
                "contain", "auto", 1, "submitted", 1, '["Gabriela-20"]',
                "2026-07-27T17:00:00+00:00",
                "Teléfono/navegador · 10.1.0.225",
            ),
        )
        for item in (
            (
                "Gabriela-20", "Gabriela", "Trabajo nativo Gabriela-20",
                "CUPS nativo · skunkpc", "completed", None,
                "2026-07-27T17:00:02+00:00",
            ),
            (
                "Zima-21", "Zima", "Trabajo nativo Zima-21",
                "android · IP 10.1.0.225", "failed", "Filter failed",
                "2026-07-27T17:01:00+00:00",
            ),
        ):
            db.execute(
                """
                INSERT INTO print_history (
                    id, printer_name, original_name, source_device, status,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*item, item[-1]),
            )
        db.commit()

    result = jobs.list_all_jobs(page=1, page_size=30)

    assert [item["id"] for item in result["jobs"]] == ["cups:Zima-21", "web-1"]
    assert result["jobs"][0]["error"] == "Filter failed"
    assert result["jobs"][1]["status"] == "completed"
    assert result["total"] == 2


def test_list_all_jobs_filters_and_paginates(monkeypatch, tmp_path) -> None:
    path = _history_database(monkeypatch, tmp_path)
    with database.connect(path) as db:
        for number in range(65):
            created_at = (
                datetime(2026, 7, 27, 17, tzinfo=UTC)
                + timedelta(minutes=number)
            ).isoformat()
            db.execute(
                """
                INSERT INTO print_history (
                    id, printer_name, original_name, source_device, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"Zima-{number}",
                    "Zima",
                    f"Trabajo {number}",
                    "IP 10.1.0.225" if number % 2 else "IP 10.1.0.226",
                    "completed" if number % 3 else "failed",
                    created_at,
                    created_at,
                ),
            )
        db.commit()

    result = jobs.list_all_jobs(
        page=2,
        page_size=30,
        printer="Zima",
        status="completed",
        origin="10.1.0.225",
    )

    assert result["page_size"] == 30
    assert result["page"] == 1
    assert result["total"] == 21
    assert len(result["jobs"]) == 21
