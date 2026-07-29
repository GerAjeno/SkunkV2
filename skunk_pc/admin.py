from __future__ import annotations

import json
import os
import signal
import socket
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from .cups import (
    CupsError,
    discover_usb_printers,
    require_success,
    run_command,
    validate_network_uri,
    validate_printer_name,
)


SOCKET_PATH = Path(os.getenv("SKUNK_ADMIN_SOCKET", "/run/skunk-pc/admin.sock"))
running = True
ACCESS_LOG = Path("/var/log/cups/access_log")
SPOOL_DIR = Path("/var/spool/cups")
_PAGE_CACHE: dict[str, tuple[int, int, int]] = {}
PRINT_JOB_RE = re.compile(
    r'^(?P<host>\S+)\s+.*?\[(?P<time>[^\]]+)\]\s+'
    r'"POST\s+/printers/(?P<printer>[^?\s]+).*?"\s+\d+\s+\d+\s+'
    r'Print-Job\s+'
)


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _validate_uri(uri: str) -> str:
    if len(uri) > 500:
        raise CupsError("URI demasiado larga")
    if uri.startswith("usb://"):
        available = {
            device.uri
            for device in discover_usb_printers(
                allow_admin_helper=False,
                refresh=True,
            )
            if device.is_zebra
        }
        if uri not in available:
            raise CupsError("La URI no corresponde a una Zebra USB conectada")
        return uri
    validate_network_uri(uri)
    return uri


def _native_job_origins(path: Path = ACCESS_LOG) -> list[dict[str, str]]:
    """Read recent native Print-Job clients without exposing the full log."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 2 * 1024 * 1024))
            if size > 2 * 1024 * 1024:
                handle.readline()
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []

    events: list[dict[str, str]] = []
    for line in lines:
        match = PRINT_JOB_RE.match(line)
        if not match:
            continue
        try:
            created_at = datetime.strptime(
                match.group("time"),
                "%d/%b/%Y:%H:%M:%S %z",
            ).isoformat()
        except ValueError:
            continue
        events.append(
            {
                "printer_name": unquote(match.group("printer")),
                "client_ip": match.group("host"),
                "created_at": created_at,
            }
        )
    return events[-300:]


def _native_job_pages(path: Path = SPOOL_DIR) -> dict[str, int]:
    """Return page counts for retained native job documents."""
    pages: dict[str, int] = {}
    try:
        documents = list(path.glob("d[0-9][0-9][0-9][0-9][0-9]-*"))
    except OSError:
        return pages
    for document in documents[-500:]:
        match = re.match(r"d0*(\d+)-", document.name)
        if not match:
            continue
        try:
            stat = document.stat()
            cache_key = str(document)
            cached = _PAGE_CACHE.get(cache_key)
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                count = cached[2]
            elif stat.st_size <= 64 * 1024 * 1024:
                content = document.read_bytes()
                count = len(re.findall(rb"/Type\s*/Page\b", content))
                _PAGE_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, count)
            else:
                count = 0
        except OSError:
            continue
        if count:
            pages[match.group(1)] = max(
                pages.get(match.group(1), 0),
                count,
            )
    return pages


def _configure(name: str, uri: str, language: str, media_type: str) -> None:
    validate_printer_name(name)
    _validate_uri(uri)
    if language not in {"epl2", "zpl"}:
        raise CupsError("Lenguaje de impresora inválido")
    if media_type not in {"thermal", "direct"}:
        raise CupsError("Método térmico inválido")
    cups_media_type = "Thermal" if media_type == "thermal" else "Direct"
    driver = (
        "drv:///sample.drv/zebraep2.ppd"
        if language == "epl2"
        else "drv:///sample.drv/zebra.ppd"
    )
    arguments = [
        "lpadmin",
        "-p",
        name,
        "-v",
        uri,
        "-E",
        "-m",
        driver,
        "-D",
        name,
        "-L",
        "Skunk PC",
        "-o",
        "printer-is-shared=true",
        "-o",
        "printer-error-policy=retry-job",
        "-o",
        "PageSize=w288h432",
        "-o",
        "media=w288h432",
        "-o",
        "Resolution=203dpi",
        "-o",
        f"MediaType={cups_media_type}",
    ]
    if uri.startswith("usb://"):
        arguments += ["-o", "usb-unidirectional-default=true"]
    require_success(run_command(arguments, timeout=40), "CUPS no pudo configurar la cola")
    require_success(run_command(["cupsaccept", name]), "CUPS no acepta trabajos")
    require_success(run_command(["cupsenable", name]), "CUPS no pudo habilitar la cola")


def dispatch(action: str, arguments: list[str]) -> str:
    if action == "job-origins":
        if arguments:
            raise CupsError("La consulta de orígenes no acepta parámetros")
        return json.dumps(_native_job_origins())
    if action == "job-pages":
        if arguments:
            raise CupsError("La consulta de páginas no acepta parámetros")
        return json.dumps(_native_job_pages())
    if action == "devices":
        if arguments:
            raise CupsError("La consulta de dispositivos no acepta parámetros")
        devices = discover_usb_printers(allow_admin_helper=False)
        return json.dumps([device.as_dict() for device in devices])
    if action in {"add", "repair"}:
        if len(arguments) != 4:
            raise CupsError("Parámetros administrativos incompletos")
        name, uri, language, media_type = arguments
        _configure(name, uri, language, media_type)
        cups_media_type = "Thermal" if media_type == "thermal" else "Direct"
        return (
            f"Impresora {name} configurada en 4×6, 203 DPI, "
            f"{language.upper()}, {cups_media_type} y URI estable"
        )
    if action == "delete":
        if len(arguments) != 1:
            raise CupsError("Parámetros administrativos incompletos")
        name = validate_printer_name(arguments[0])
        require_success(
            run_command(["lpstat", "-p", name]),
            "La impresora no existe",
        )
        # Remove pending and retained jobs before deleting the destination.
        # `cancel` may report that there are no jobs, which is harmless here.
        run_command(["cancel", "-a", "-x", name])
        require_success(run_command(["lpadmin", "-x", name]), "No se pudo eliminar la cola")
        if run_command(["lpstat", "-p", name]).returncode == 0:
            raise CupsError("CUPS mantuvo la cola después de solicitar su eliminación")
        return f"Impresora {name} y su configuración fueron eliminadas"
    if action == "rename":
        if len(arguments) != 5:
            raise CupsError("Parámetros administrativos incompletos")
        old_name, new_name, uri, language, media_type = arguments
        old_name = validate_printer_name(old_name)
        new_name = validate_printer_name(new_name)
        if old_name == new_name:
            raise CupsError("El nombre nuevo debe ser diferente")
        require_success(run_command(["lpstat", "-p", old_name]), "La impresora no existe")
        if run_command(["lpstat", "-p", new_name]).returncode == 0:
            raise CupsError("Ya existe una impresora con ese nombre")

        _configure(new_name, uri, language, media_type)
        try:
            run_command(["cancel", "-a", "-x", old_name])
            require_success(
                run_command(["lpadmin", "-x", old_name]),
                "No se pudo retirar la cola con el nombre anterior",
            )
            if run_command(["lpstat", "-p", old_name]).returncode == 0:
                raise CupsError("CUPS mantuvo la cola con el nombre anterior")
        except Exception:
            run_command(["lpadmin", "-x", new_name])
            raise
        return f"Impresora {old_name} renombrada como {new_name}"
    if action == "purge":
        if len(arguments) != 1:
            raise CupsError("Parámetros administrativos incompletos")
        name = validate_printer_name(arguments[0])
        require_success(
            run_command(["lpstat", "-p", name]),
            "La impresora no existe",
        )
        require_success(
            run_command(["cancel", "-a", "-x", name]),
            "No se pudieron cancelar los trabajos",
        )
        return f"Trabajos y registros fallidos de {name} eliminados"
    if action == "configure":
        if len(arguments) != 1:
            raise CupsError("Parámetros administrativos incompletos")
        name = validate_printer_name(arguments[0])
        result = run_command(
            [
                "lpadmin",
                "-p",
                name,
                "-o",
                "PageSize=w288h432",
                "-o",
                "media=w288h432",
                "-o",
                "Resolution=203dpi",
                "-o",
                "MediaType=Direct",
            ]
        )
        require_success(result, "No se pudo fijar el tamaño 4×6")
        return f"Cola {name} configurada en 4×6"
    if action == "enable":
        if len(arguments) != 1:
            raise CupsError("Parámetros administrativos incompletos")
        name = validate_printer_name(arguments[0])
        require_success(run_command(["cupsaccept", name]), "No se pudo aceptar trabajos")
        require_success(run_command(["cupsenable", name]), "No se pudo habilitar la cola")
        return f"Cola {name} habilitada"
    raise CupsError("Acción administrativa no permitida")


def handle(connection: socket.socket) -> None:
    data = bytearray()
    while len(data) <= 8192:
        chunk = connection.recv(2048)
        if not chunk or b"\n" in chunk:
            data.extend(chunk.split(b"\n", 1)[0])
            break
        data.extend(chunk)
    try:
        request = json.loads(data.decode("utf-8"))
        action = request.get("action")
        arguments = request.get("arguments")
        if not isinstance(action, str) or not isinstance(arguments, list):
            raise CupsError("Solicitud administrativa inválida")
        if not all(isinstance(item, str) for item in arguments):
            raise CupsError("Argumentos administrativos inválidos")
        message = dispatch(action, arguments)
        response = {"ok": True, "message": message}
    except (CupsError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        response = {"ok": False, "error": str(exc)}
    connection.sendall(json.dumps(response).encode("utf-8"))


def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o660)
    server.listen(8)
    server.settimeout(1.0)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while running:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            with connection:
                handle(connection)
    finally:
        server.close()
        SOCKET_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
