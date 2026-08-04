from pathlib import Path
from types import SimpleNamespace

from skunk_pc import worker


def test_process_job_submits_each_copy_explicitly(monkeypatch) -> None:
    submitted: list[tuple[str, Path, int]] = []
    finished: list[dict] = []
    pages = [Path("/tmp/page-1.pdf")]
    job = {
        "id": "job-1",
        "source_path": "/tmp/source.pdf",
        "printer_name": "Gabriela",
        "fit_mode": "contain",
        "orientation": "auto",
        "copies": 5,
        "source_page": None,
    }

    monkeypatch.setattr(
        worker,
        "get_printer",
        lambda _name: SimpleNamespace(name="Gabriela", connected=True),
    )
    monkeypatch.setattr(worker, "convert_document", lambda *args, **kwargs: pages)
    monkeypatch.setattr(
        worker,
        "submit_file",
        lambda printer, page, *, copies: (
            submitted.append((printer, page, copies))
            or f"Gabriela-{len(submitted)}"
        ),
    )
    monkeypatch.setattr(
        worker,
        "finish_job",
        lambda _job_id, **kwargs: finished.append(kwargs),
    )
    monkeypatch.setattr(worker, "remove_job_files", lambda _job: None)

    worker.process_job(job)

    assert submitted == [("Gabriela", pages[0], 1)] * 5
    assert finished == [
        {
            "status": "submitted",
            "pages": 1,
            "cups_job_ids": [
                "Gabriela-1",
                "Gabriela-2",
                "Gabriela-3",
                "Gabriela-4",
                "Gabriela-5",
            ],
        }
    ]


def test_worker_cleans_recovered_jobs_before_claiming_new_ones(monkeypatch) -> None:
    recovered = [
        {"id": "job-interrupted", "source_path": "/tmp/interrupted.pdf"},
    ]
    removed: list[str] = []
    monkeypatch.setattr(worker, "recover_interrupted_jobs", lambda: recovered)
    monkeypatch.setattr(
        worker,
        "remove_job_files",
        lambda job: removed.append(job["id"]),
    )

    count = worker.recover_jobs_after_restart()

    assert count == 1
    assert removed == ["job-interrupted"]
