from __future__ import annotations

import json
import os
import signal
import socket
from pathlib import Path

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


def _configure(name: str, uri: str, language: str) -> None:
    validate_printer_name(name)
    _validate_uri(uri)
    if language not in {"epl2", "zpl"}:
        raise CupsError("Lenguaje de impresora inválido")
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
        "MediaType=Direct",
    ]
    if uri.startswith("usb://"):
        arguments += ["-o", "usb-unidirectional-default=true"]
    require_success(run_command(arguments, timeout=40), "CUPS no pudo configurar la cola")
    require_success(run_command(["cupsaccept", name]), "CUPS no acepta trabajos")
    require_success(run_command(["cupsenable", name]), "CUPS no pudo habilitar la cola")


def dispatch(action: str, arguments: list[str]) -> str:
    if action == "devices":
        if arguments:
            raise CupsError("La consulta de dispositivos no acepta parámetros")
        devices = discover_usb_printers(allow_admin_helper=False)
        return json.dumps([device.as_dict() for device in devices])
    if action in {"add", "repair"}:
        if len(arguments) != 3:
            raise CupsError("Parámetros administrativos incompletos")
        name, uri, language = arguments
        _configure(name, uri, language)
        return (
            f"Impresora {name} configurada en 4×6, 203 DPI, "
            f"{language.upper()} y URI estable"
        )
    if action == "delete":
        if len(arguments) != 1:
            raise CupsError("Parámetros administrativos incompletos")
        name = validate_printer_name(arguments[0])
        require_success(run_command(["lpadmin", "-x", name]), "No se pudo eliminar la cola")
        return f"Cola {name} eliminada"
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
