from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

from .config import settings
from .converter import ConversionError, convert_document
from .cups import CupsError, get_printer, submit_file
from .database import init_database
from .jobs import (
    claim_next_job,
    cleanup_history,
    finish_job,
    remove_job_files,
    sync_cups_history,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("skunk-worker")
running = True


def stop_worker(_signum: int, _frame: object) -> None:
    global running
    running = False


def process_job(job: dict) -> None:
    source = Path(job["source_path"])
    output_dir = settings.output_dir / job["id"]
    cups_ids: list[str] = []
    try:
        printer = get_printer(job["printer_name"])
        if not printer.connected:
            raise CupsError("La impresora está desconectada o su URI es incorrecta")
        pages = convert_document(
            source,
            output_dir,
            fit_mode=job["fit_mode"],
            orientation=job["orientation"],
        )
        # Several legacy Zebra PPDs advertise copy support but only emit one
        # physical label. Submit each copy explicitly so all Zebra models
        # behave consistently.
        for _copy in range(job["copies"]):
            for page in pages:
                cups_ids.append(
                    submit_file(
                        printer.name,
                        page,
                        copies=1,
                    )
                )
        finish_job(
            job["id"],
            status="submitted",
            pages=len(pages),
            cups_job_ids=cups_ids,
        )
        log.info("Trabajo %s enviado: %s", job["id"], ", ".join(cups_ids))
    except (ConversionError, CupsError, OSError) as exc:
        finish_job(job["id"], status="failed", error=str(exc)[:1000])
        log.error("Trabajo %s falló: %s", job["id"], exc)
    except Exception as exc:
        finish_job(job["id"], status="failed", error="Error interno de conversión")
        log.exception("Trabajo %s falló inesperadamente: %s", job["id"], exc)
    finally:
        remove_job_files(job)


def main() -> None:
    settings.prepare_directories()
    init_database()
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    log.info("Worker iniciado")
    next_history_sync = 0.0
    next_cleanup = 0.0
    while running:
        now = time.monotonic()
        if now >= next_history_sync:
            try:
                sync_cups_history()
            except Exception:
                log.exception("No se pudo sincronizar el historial CUPS")
            next_history_sync = now + 10
        if now >= next_cleanup:
            try:
                cleanup_history()
            except Exception:
                log.exception("No se pudo limpiar el historial antiguo")
            next_cleanup = now + 3600
        job = claim_next_job()
        if job is None:
            time.sleep(1.5)
            continue
        process_job(job)
    log.info("Worker detenido")


if __name__ == "__main__":
    main()
