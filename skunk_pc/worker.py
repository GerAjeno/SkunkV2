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
    recover_interrupted_jobs,
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
            page=job["source_page"],
        )
        # Varios PPD antiguos de Zebra anuncian soporte de copias pero solo
        # emiten una etiqueta física. Cada copia se envía explícitamente para
        # que todos los modelos Zebra se comporten igual.
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
        # Si el trabajo tenía varias copias/páginas, algunas pueden haberse
        # enviado a CUPS antes del fallo. Se conservan sus IDs para que el
        # historial las siga asociando a este trabajo en vez de mostrarlas
        # como impresiones nativas sin origen.
        finish_job(
            job["id"],
            status="failed",
            pages=len(cups_ids),
            cups_job_ids=cups_ids,
            error=str(exc)[:1000],
        )
        log.error("Trabajo %s falló: %s", job["id"], exc)
    except Exception as exc:
        finish_job(
            job["id"],
            status="failed",
            pages=len(cups_ids),
            cups_job_ids=cups_ids,
            error="Error interno de conversión",
        )
        log.exception("Trabajo %s falló inesperadamente: %s", job["id"], exc)
    finally:
        remove_job_files(job)


def recover_jobs_after_restart() -> int:
    recovered = recover_interrupted_jobs()
    for job in recovered:
        try:
            remove_job_files(job)
        except OSError:
            log.exception(
                "No se pudieron limpiar los archivos del trabajo interrumpido %s",
                job["id"],
            )
    if recovered:
        log.warning(
            "%s trabajo(s) interrumpido(s) fueron marcados como fallidos",
            len(recovered),
        )
    return len(recovered)


def main() -> None:
    settings.prepare_directories()
    init_database()
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    recover_jobs_after_restart()
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
