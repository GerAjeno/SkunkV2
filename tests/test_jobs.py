from skunk_pc import jobs


def test_list_all_jobs_merges_native_jobs_and_updates_web_result(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs,
        "list_jobs",
        lambda _limit: [
            {
                "id": "web-1",
                "original_name": "etiqueta.pdf",
                "printer_name": "Gabriela",
                "status": "submitted",
                "pages": 1,
                "cups_job_ids": '["Gabriela-20"]',
                "created_at": "2026-07-27T17:00:00+00:00",
                "source_device": "Teléfono/navegador · 10.1.0.225",
                "error": None,
            }
        ],
    )
    monkeypatch.setattr(
        jobs,
        "list_cups_jobs",
        lambda _limit: [
            {
                "cups_job_id": "Gabriela-20",
                "original_name": "Trabajo nativo Gabriela-20",
                "printer_name": "Gabriela",
                "status": "completed",
                "pages": 0,
                "created_at": "2026-07-27T17:00:02+00:00",
                "source_device": "CUPS nativo · skunkpc",
                "error": None,
            },
            {
                "cups_job_id": "Zima-21",
                "original_name": "Trabajo nativo Zima-21",
                "printer_name": "Zima",
                "status": "failed",
                "pages": 0,
                "created_at": "2026-07-27T17:01:00+00:00",
                "source_device": "CUPS nativo · android",
                "error": "Filter failed",
            },
        ],
    )

    combined = jobs.list_all_jobs(30)

    assert [item["id"] for item in combined] == ["cups:Zima-21", "web-1"]
    assert combined[0]["error"] == "Filter failed"
    assert combined[1]["status"] == "completed"
