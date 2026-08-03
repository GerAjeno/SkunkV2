from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings
from .schemas import Printer, UsbPrinter


PRINTER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
DEVICE_LINE_RE = re.compile(
    r"^(?:device for|dispositivo para)\s+([^:]+):\s+(.+)$",
    re.IGNORECASE,
)
STATE_LINE_RE = re.compile(
    r"^(?:printer|la impresora)\s+(\S+)\s+(.+)$",
    re.IGNORECASE,
)
USB_CACHE_SECONDS = 10.0
_usb_cache_lock = threading.Lock()
_usb_cache_expires_at = 0.0
_usb_cache: list[UsbPrinter] = []


class CupsError(RuntimeError):
    pass


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_command(args: list[str], *, timeout: int = 20, input_text: str | None = None) -> CommandResult:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CupsError(f"No fue posible ejecutar {args[0]}: {exc}") from exc
    return CommandResult(result.returncode, result.stdout.strip(), result.stderr.strip())


def require_success(result: CommandResult, action: str) -> str:
    if result.returncode != 0:
        detail = result.stderr or result.stdout or "error desconocido"
        raise CupsError(f"{action}: {detail}")
    return result.stdout


def validate_printer_name(name: str) -> str:
    if not PRINTER_NAME_RE.fullmatch(name):
        raise CupsError("Nombre de impresora inválido")
    return name


def parse_lpinfo_devices(output: str) -> list[str]:
    devices: list[str] = []
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and "://" in parts[1]:
            devices.append(parts[1])
    return devices


def parse_device_uri(uri: str) -> UsbPrinter:
    parsed = urlparse(uri)
    query = parse_qs(parsed.query)
    serial = query.get("serial", [""])[0]
    path = unquote(f"{parsed.netloc}{parsed.path}").strip("/")
    manufacturer, _, model = path.partition("/")
    is_zebra = "zebra" in manufacturer.lower() or "ztc" in model.lower()
    return UsbPrinter(
        uri=uri,
        manufacturer=manufacturer or "Desconocido",
        model=model or "Impresora USB",
        serial=serial,
        is_zebra=is_zebra,
    )


def discover_usb_printers(
    *,
    allow_admin_helper: bool = True,
    refresh: bool = False,
) -> list[UsbPrinter]:
    global _usb_cache, _usb_cache_expires_at

    now = time.monotonic()
    if not refresh and now < _usb_cache_expires_at:
        return list(_usb_cache)

    with _usb_cache_lock:
        now = time.monotonic()
        if not refresh and now < _usb_cache_expires_at:
            return list(_usb_cache)

        if allow_admin_helper:
            response = run_admin_helper("devices")
            try:
                items = json.loads(response)
                if not isinstance(items, list):
                    raise ValueError
                devices = [
                    UsbPrinter(
                        uri=str(item["uri"]),
                        manufacturer=str(item["manufacturer"]),
                        model=str(item["model"]),
                        serial=str(item["serial"]),
                        is_zebra=bool(item["is_zebra"]),
                    )
                    for item in items
                    if isinstance(item, dict)
                ]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CupsError("Respuesta de dispositivos inválida") from exc
        else:
            result = run_command(["lpinfo", "-v"])
            require_success(result, "No se pudieron consultar dispositivos CUPS")
            devices = [
                parse_device_uri(uri)
                for uri in parse_lpinfo_devices(result.stdout)
                if uri.startswith("usb://")
            ]

        _usb_cache = devices
        _usb_cache_expires_at = time.monotonic() + USB_CACHE_SECONDS
        return list(devices)


def _parse_options(output: str) -> dict[str, str]:
    options: dict[str, str] = {}
    try:
        fields = shlex.split(output)
    except ValueError:
        fields = output.split()
    for field in fields:
        if "=" in field:
            key, value = field.split("=", 1)
            options[key] = value
    return options


def _parse_dpi(value: str) -> int:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else 203


def _selected_ppd_choice(output: str, option: str) -> str:
    prefix = f"{option}/"
    for line in output.splitlines():
        if not line.startswith(prefix) or ":" not in line:
            continue
        for choice in line.split(":", 1)[1].split():
            if choice.startswith("*"):
                return choice[1:]
    return ""


def _ppd_identity(name: str, *, ppd_dir: Path = Path("/etc/cups/ppd")) -> str:
    if not PRINTER_NAME_RE.fullmatch(name):
        return ""
    path = ppd_dir / f"{name}.ppd"
    try:
        with path.open("r", encoding="latin-1") as handle:
            lines = []
            for _ in range(250):
                line = handle.readline()
                if not line:
                    break
                if line.startswith(("*NickName:", "*ModelName:", "*Manufacturer:")):
                    lines.append(line)
            return " ".join(lines)
    except OSError:
        return ""


def _configured_devices() -> dict[str, str]:
    result = run_command(["lpstat", "-v"])
    if result.returncode != 0:
        return {}
    devices: dict[str, str] = {}
    for line in result.stdout.splitlines():
        match = DEVICE_LINE_RE.match(line.strip())
        if match:
            devices[match.group(1).strip()] = match.group(2).strip()
    return devices


def _states() -> dict[str, tuple[str, str]]:
    result = run_command(["lpstat", "-p"])
    if result.returncode != 0:
        return {}
    states: dict[str, tuple[str, str]] = {}
    for line in result.stdout.splitlines():
        match = STATE_LINE_RE.match(line.strip())
        if not match:
            continue
        name, description = match.group(1), match.group(2)
        lowered = description.lower()
        if any(word in lowered for word in ("disabled", "detenida", "stopped")):
            state = "stopped"
        elif any(word in lowered for word in ("printing", "imprimiendo")):
            state = "printing"
        else:
            state = "idle"
        states[name] = (state, description)
    return states


def list_printers(*, include_non_zebra: bool = False) -> list[Printer]:
    configured = _configured_devices()
    physical_usb = [device.uri for device in discover_usb_printers()]
    states = _states()
    printers: list[Printer] = []

    for name, uri in configured.items():
        option_result = run_command(["lpoptions", "-p", name])
        options = _parse_options(option_result.stdout) if option_result.returncode == 0 else {}
        choices_result = run_command(["lpoptions", "-p", name, "-l"])
        choices = choices_result.stdout if choices_result.returncode == 0 else ""
        description = options.get("printer-info", name)
        make_model = options.get("printer-make-and-model", "")
        ppd_identity = _ppd_identity(name)
        is_zebra = any(
            marker in f"{uri} {description} {make_model} {ppd_identity}".lower()
            for marker in ("zebra", "ztc", "epl", "zpl")
        )
        if not include_non_zebra and not is_zebra:
            continue

        physical_uri: str | None = None
        connected = not uri.startswith("usb://")
        if uri.startswith("usb://"):
            if uri in physical_usb:
                connected = True
                physical_uri = uri
            else:
                queue_serial = parse_qs(urlparse(uri).query).get("serial", [""])[0]
                for candidate in physical_usb:
                    candidate_serial = parse_qs(urlparse(candidate).query).get("serial", [""])[0]
                    if queue_serial and queue_serial == candidate_serial:
                        connected = True
                        physical_uri = candidate
                        break

        state, message = states.get(name, ("unknown", "Estado no disponible"))
        if uri.startswith("usb://") and not connected:
            state = "disconnected"
            message = "La URI configurada no corresponde a un dispositivo USB conectado"

        identity = f"{make_model} {ppd_identity} {uri}".lower()
        language = ("epl2" if "epl" in identity else "zpl") if is_zebra else ""
        media_type = (
            (
                "thermal"
                if _selected_ppd_choice(choices, "MediaType").lower() == "thermal"
                else "direct"
            )
            if is_zebra
            else ""
        )
        printers.append(
            Printer(
                name=name,
                uri=uri,
                description=description,
                make_model=make_model or "Modelo no informado",
                state=state,
                state_message=message,
                connected=connected,
                is_zebra=is_zebra,
                language=language,
                dpi=_parse_dpi(options.get("Resolution", "203dpi")),
                page_size=options.get(
                    "PageSize",
                    options.get(
                        "media",
                        _selected_ppd_choice(choices, "PageSize") or "desconocido",
                    ),
                ),
                media_type=media_type,
                physical_uri=physical_uri,
            )
        )
    return sorted(printers, key=lambda printer: printer.name.casefold())


def get_printer(name: str) -> Printer:
    validate_printer_name(name)
    for printer in list_printers(include_non_zebra=True):
        if printer.name == name:
            return printer
    raise CupsError(f"La impresora {name} no existe")


def cups_running() -> bool:
    return run_command(["lpstat", "-r"]).returncode == 0


def _active_job_lines(output: str) -> list[str]:
    """Return job headers, excluding stale completed jobs exposed by CUPS."""
    jobs: list[str] = []
    current_header = ""
    current_detail: list[str] = []

    def append_current() -> None:
        if not current_header:
            return
        detail = " ".join(current_detail).lower()
        if not any(
            marker in detail
            for marker in (
                "job-completed-with-errors",
                "job-completed-successfully",
                "job-canceled-by-user",
            )
        ):
            jobs.append(current_header)

    for line in output.splitlines():
        if line and not line[0].isspace():
            append_current()
            current_header = line
            current_detail = []
        elif current_header:
            current_detail.append(line.strip())
    append_current()
    return jobs


def _system_timezone(timezone_file: Path = Path("/etc/timezone")) -> tzinfo:
    """Return the configured system timezone, including historical DST rules."""
    try:
        timezone_name = timezone_file.read_text(encoding="utf-8").strip()
        if timezone_name:
            return ZoneInfo(timezone_name)
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    return datetime.now().astimezone().tzinfo or UTC


def _cups_datetime(value: str, *, local_timezone: tzinfo | None = None) -> str:
    """Convert the timezone-less local timestamp printed by lpstat to UTC."""
    value = value.strip()
    timezone = local_timezone or _system_timezone()
    for pattern in (
        "%a %d %b %Y %H:%M:%S",
        "%a %d %b %Y %I:%M:%S %p %Z",
        "%a %b %d %H:%M:%S %Y",
    ):
        try:
            parsed = datetime.strptime(value, pattern).replace(tzinfo=timezone)
            return parsed.astimezone(UTC).isoformat()
        except ValueError:
            continue
    return datetime.now(UTC).isoformat()


def _parse_detailed_jobs(output: str) -> list[dict]:
    jobs: list[dict] = []
    current: dict | None = None
    for line in output.splitlines():
        if line and not line[0].isspace():
            parts = line.split(maxsplit=3)
            if len(parts) < 3 or "-" not in parts[0]:
                current = None
                continue
            printer_name, _, number = parts[0].rpartition("-")
            if not printer_name or not number.isdigit():
                current = None
                continue
            current = {
                "cups_job_id": parts[0],
                "printer_name": printer_name,
                "source_device": f"CUPS nativo · {parts[1]}",
                "original_name": f"Trabajo nativo {parts[0]}",
                "created_at": _cups_datetime(parts[3] if len(parts) == 4 else ""),
                "pages": 0,
                "detail": [],
            }
            jobs.append(current)
        elif current is not None:
            current["detail"].append(line.strip())
    return jobs


def _cups_job_sort_key(job: dict) -> tuple[int, float]:
    numeric_id = str(job.get("cups_job_id", "")).rpartition("-")[2]
    try:
        job_number = int(numeric_id)
    except ValueError:
        job_number = -1
    try:
        created_at = datetime.fromisoformat(str(job.get("created_at", ""))).timestamp()
    except ValueError:
        created_at = 0.0
    return job_number, created_at


def list_cups_jobs(limit: int = 100) -> list[dict]:
    """Read recent jobs accepted directly by CUPS, including native IPP."""
    all_result = run_command(["lpstat", "-W", "all", "-l", "-o"])
    if all_result.returncode != 0:
        return []
    completed_result = run_command(["lpstat", "-W", "completed", "-o"])
    completed_ids = {
        line.split(maxsplit=1)[0]
        for line in completed_result.stdout.splitlines()
        if line.strip()
    } if completed_result.returncode == 0 else set()

    jobs = _parse_detailed_jobs(all_result.stdout)
    for job in jobs:
        detail_lines = job.pop("detail")
        detail = " ".join(detail_lines).strip()
        lowered = detail.lower()
        if (
            "job-completed-with-errors" in lowered
            or "job-stopped" in lowered
            or "filter errors" in lowered
        ):
            job["status"] = "failed"
            status_line = next(
                (
                    item.split(":", 1)[1].strip()
                    for item in detail_lines
                    if item.lower().startswith(("status:", "estado:"))
                    and ":" in item
                ),
                "",
            )
            job["error"] = status_line or "CUPS completó el trabajo con errores"
        elif job["cups_job_id"] in completed_ids or "job-completed-successfully" in lowered:
            job["status"] = "completed"
            job["error"] = None
        elif "printing" in lowered or "imprimiendo" in lowered or "sending data" in lowered:
            job["status"] = "processing"
            job["error"] = None
        else:
            job["status"] = "queued"
            job["error"] = None
    requested_limit = min(max(limit, 1), 500)
    jobs.sort(key=_cups_job_sort_key, reverse=True)
    jobs = jobs[:requested_limit]
    _attach_native_pages(jobs)
    _attach_native_origins(jobs)
    jobs.extend(_native_rejected_jobs())
    jobs.sort(
        key=lambda job: (
            datetime.fromisoformat(str(job.get("created_at", ""))).timestamp()
            if job.get("created_at")
            else 0.0
        ),
        reverse=True,
    )
    return jobs[:requested_limit]


def _native_rejected_jobs() -> list[dict]:
    """Represent IPP requests rejected before CUPS assigned a job number."""
    try:
        response = run_admin_helper("job-failures")
        events = json.loads(response)
    except (CupsError, json.JSONDecodeError):
        return []
    if not isinstance(events, list):
        return []

    jobs: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        printer_name = str(event.get("printer_name") or "").strip()
        client_ip = str(event.get("client_ip") or "").strip()
        created_at = str(event.get("created_at") or "").strip()
        result = str(event.get("result") or "client-error-bad-request").strip()
        try:
            # CUPS access_log records the IPP response size here, not the
            # number of document bytes uploaded by the client.
            int(event.get("bytes", 0))
            datetime.fromisoformat(created_at)
        except (TypeError, ValueError):
            continue
        if not printer_name or not client_ip:
            continue
        fingerprint = hashlib.sha256(
            f"{printer_name}\0{client_ip}\0{created_at}\0{result}".encode()
        ).hexdigest()[:20]
        if result == "client-error-bad-request":
            error = (
                "CUPS rechazó la solicitud IPP del dispositivo por contener "
                "atributos o datos no válidos. Cancela el trabajo bloqueado en "
                "el teléfono antes de crear uno nuevo."
            )
        else:
            error = f"CUPS rechazó el trabajo nativo: {result}"
        jobs.append(
            {
                "cups_job_id": f"IPP-rejected-{fingerprint}",
                "printer_name": printer_name,
                "source_device": f"IP {client_ip}",
                "original_name": "Trabajo nativo rechazado",
                "created_at": created_at,
                "pages": 0,
                "status": "failed",
                "error": error,
            }
        )
    return jobs


def _attach_native_origins(jobs: list[dict]) -> None:
    try:
        response = run_admin_helper("job-origins")
        events = json.loads(response)
    except (CupsError, json.JSONDecodeError):
        return
    if not isinstance(events, list):
        return

    available: list[tuple[dict, datetime]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            event_time = datetime.fromisoformat(str(event["created_at"]))
        except (KeyError, ValueError):
            continue
        available.append((event, event_time))

    used: set[int] = set()
    for job in jobs:
        try:
            job_time = datetime.fromisoformat(job["created_at"])
        except (KeyError, ValueError):
            continue
        candidates = [
            (abs((event_time - job_time).total_seconds()), index, event)
            for index, (event, event_time) in enumerate(available)
            if index not in used
            and event.get("printer_name") == job["printer_name"]
            and abs((event_time - job_time).total_seconds()) <= 120
        ]
        if not candidates:
            continue
        _, index, event = min(candidates, key=lambda item: item[0])
        used.add(index)
        client_ip = str(event.get("client_ip") or "").strip()
        if not client_ip:
            continue
        owner = job["source_device"].removeprefix("CUPS nativo · ").strip()
        if owner.lower() in {"", "unknown", "desconocido"}:
            job["source_device"] = f"IP {client_ip}"
        else:
            job["source_device"] = f"{owner} · IP {client_ip}"

    # Localized `lpstat` dates cannot always be parsed. CUPS preserves the
    # order of jobs and access events, so pair the remaining records by queue
    # and chronological position instead of leaving their origin unknown.
    for printer_name in {job["printer_name"] for job in jobs}:
        remaining_jobs = [
            job for job in jobs
            if job["printer_name"] == printer_name
            and "IP " not in job["source_device"]
        ]
        remaining_events = [
            (index, event, event_time)
            for index, (event, event_time) in enumerate(available)
            if index not in used and event.get("printer_name") == printer_name
        ]
        remaining_jobs.sort(key=lambda item: item.get("created_at", ""))
        remaining_events.sort(key=lambda item: item[2])
        if len(remaining_events) > len(remaining_jobs):
            remaining_events = remaining_events[-len(remaining_jobs):]
        for job, (index, event, _event_time) in zip(remaining_jobs, remaining_events):
            client_ip = str(event.get("client_ip") or "").strip()
            if client_ip:
                owner = job["source_device"].removeprefix("CUPS nativo · ").strip()
                if owner.lower() in {"", "unknown", "desconocido"}:
                    job["source_device"] = f"IP {client_ip}"
                else:
                    job["source_device"] = f"{owner} · IP {client_ip}"
                used.add(index)


def _attach_native_pages(jobs: list[dict]) -> None:
    try:
        response = run_admin_helper("job-pages")
        page_counts = json.loads(response)
    except (CupsError, json.JSONDecodeError):
        page_counts = {}
    if not isinstance(page_counts, dict):
        page_counts = {}
    for job in jobs:
        numeric_id = job["cups_job_id"].rpartition("-")[2].lstrip("0") or "0"
        pages = page_counts.get(numeric_id)
        if isinstance(pages, int) and pages > 0:
            job["pages"] = pages
        elif job["status"] != "failed":
            # Every accepted native print job contains at least one page.
            job["pages"] = 1


def diagnose_printer(printer_name: str) -> str:
    printer = get_printer(printer_name)
    problems: list[str] = []
    details: list[str] = []

    if printer.connected:
        details.append("Conexión: dispositivo disponible")
    else:
        problems.append("La impresora no está conectada o su URI cambió")

    if printer.state == "stopped":
        problems.append(f"CUPS detuvo la impresora: {printer.state_message}")
    elif printer.state == "printing":
        details.append("Estado CUPS: imprimiendo")
    else:
        details.append("Estado CUPS: lista")

    pending_result = run_command(
        ["lpstat", "-W", "not-completed", "-l", "-o", printer.name],
    )
    completed_result = run_command(
        ["lpstat", "-W", "completed", "-o", printer.name],
    )
    completed_ids = {
        line.split(maxsplit=1)[0]
        for line in completed_result.stdout.splitlines()
        if line.strip()
    } if completed_result.returncode == 0 else set()
    pending_jobs = (
        [
            line
            for line in _active_job_lines(pending_result.stdout)
            if line.split(maxsplit=1)[0] not in completed_ids
        ]
        if pending_result.returncode == 0
        else []
    )
    details.append(f"Trabajos pendientes: {len(pending_jobs)}")
    if pending_jobs:
        problems.append(
            f"Hay {len(pending_jobs)} trabajo(s) pendiente(s) en la cola"
        )

    recent_rejections = [
        job
        for job in _native_rejected_jobs()
        if job["printer_name"] == printer.name
        and (
            datetime.now(UTC)
            - datetime.fromisoformat(job["created_at"]).astimezone(UTC)
        ).total_seconds()
        <= 30 * 60
    ]
    if recent_rejections:
        latest = recent_rejections[0]
        problems.append(
            f"CUPS rechazó {len(recent_rejections)} trabajo(s) nativo(s) en los "
            f"últimos 30 minutos desde {latest['source_device']}: {latest['error']}"
        )

    if not printer.is_zebra:
        details.append(f"Resolución: {printer.dpi} DPI")
        details.append(f"Formato de página: {printer.page_size or 'automático'}")
    elif printer.dpi != 203:
        problems.append(f"Resolución configurada incorrectamente: {printer.dpi} DPI")
    else:
        details.append("Resolución: 203 DPI")

    if printer.is_zebra:
        if printer.page_size != "w288h432":
            problems.append(f"Tamaño configurado incorrectamente: {printer.page_size}")
        else:
            details.append("Formato: 4×6")

    heading = (
        f"Se detectaron {len(problems)} problema(s) en {printer.name}:"
        if problems
        else f"No se detectaron problemas de software en {printer.name}."
    )
    lines = [heading]
    lines.extend(f"• {problem}" for problem in problems)
    lines.extend(f"✓ {detail}" for detail in details)
    if printer.uri.startswith("usb://"):
        lines.append(
            "Nota: el USB es unidireccional; atascos, ribbon, sensor y "
            "problemas mecánicos deben revisarse físicamente."
        )
    return "\n".join(lines)


def submit_file(printer_name: str, path: Path, *, copies: int = 1) -> str:
    validate_printer_name(printer_name)
    if not 1 <= copies <= 20:
        raise CupsError("Cantidad de copias inválida")
    if not path.is_file():
        raise CupsError("El archivo convertido no existe")
    args = [
        "lp",
        "-d",
        printer_name,
        "-n",
        str(copies),
        "-o",
        "media=w288h432",
        "-o",
        "PageSize=w288h432",
        "-o",
        "Resolution=203dpi",
        "-o",
        "fit-to-page",
        str(path),
    ]
    result = run_command(args, timeout=30)
    output = require_success(result, "CUPS rechazó el trabajo")
    match = re.search(r"([A-Za-z0-9_-]+-\d+)", output)
    return match.group(1) if match else output


def send_raw(printer_name: str, payload: str) -> None:
    validate_printer_name(printer_name)
    result = run_command(
        ["lp", "-d", printer_name, "-o", "raw"],
        timeout=20,
        input_text=payload,
    )
    require_success(result, "No se pudo enviar el comando a la impresora")


def send_test(printer_name: str) -> None:
    printer = get_printer(printer_name)
    if not printer.is_zebra:
        payload = (
            "SKUNK PC - PAGINA DE PRUEBA\n\n"
            f"Impresora: {printer.name}\n"
            f"Modelo: {printer.make_model}\n"
            f"Hora: {datetime.now(UTC).isoformat(timespec='seconds')}\n"
        )
        result = run_command(["lp", "-d", printer_name], timeout=20, input_text=payload)
        require_success(result, "No se pudo enviar la prueba de impresión")
        return
    if printer.language == "epl2":
        thermal_mode = "O\n" if printer.media_type == "thermal" else "OD\n"
        payload = (
            f"\n{thermal_mode}N\nq812\nQ1218,24\n"
            'A40,40,0,4,1,1,N,"SKUNK PC - PRUEBA EPL2"\n'
            'A40,105,0,3,1,1,N,"ORIGEN: PAGINA WEB"\n'
            f'A40,155,0,3,1,1,N,"IMPRESORA: {printer.name}"\n'
            'A40,205,0,3,1,1,N,"Etiqueta 4x6 - 203 DPI"\n'
            'B40,280,0,1,2,4,100,B,"SKUNK-PC-OK"\nP1\n'
        )
    else:
        payload = (
            "^XA^PW812^LL1218"
            "^FO40,40^A0N,42,42^FDSKUNK PC - PRUEBA ZPL^FS"
            "^FO40,105^A0N,30,30^FDORIGEN: PAGINA WEB^FS"
            f"^FO40,155^A0N,30,30^FDIMPRESORA: {printer.name}^FS"
            "^FO40,205^A0N,30,30^FDEtiqueta 4x6 - 203 DPI^FS"
            "^FO40,280^BY3^BCN,100,Y,N,N^FDSKUNK-PC-OK^FS^XZ"
        )
    send_raw(printer_name, payload)


def calibrate(printer_name: str) -> None:
    printer = get_printer(printer_name)
    if not printer.is_zebra:
        raise CupsError("La calibración solo está disponible para impresoras Zebra")
    send_raw(printer_name, "\njc\n" if printer.language == "epl2" else "~JC\n^XA^JUS^XZ")


DARKNESS_RANGES = {"epl2": (0, 15), "zpl": (0, 30)}


def set_darkness(printer_name: str, level: int) -> None:
    printer = get_printer(printer_name)
    if not printer.is_zebra:
        raise CupsError("La densidad de impresión solo está disponible para impresoras Zebra")
    minimum, maximum = DARKNESS_RANGES[printer.language]
    if not minimum <= level <= maximum:
        raise CupsError(f"La densidad debe estar entre {minimum} y {maximum}")
    if printer.language == "epl2":
        payload = f"\nD{level}\n"
    else:
        payload = f"~SD{level:02d}\n"
    send_raw(printer_name, payload)


def run_admin_helper(action: str, *arguments: str) -> str:
    allowed_actions = {
        "devices",
        "job-origins",
        "job-failures",
        "job-pages",
        "add",
        "repair",
        "delete",
        "rename",
        "purge",
        "configure",
        "enable",
        "reboot",
    }
    if action not in allowed_actions:
        raise CupsError("Acción administrativa inválida")
    request = json.dumps({"action": action, "arguments": list(arguments)}).encode("utf-8")
    if len(request) > 8192:
        raise CupsError("Solicitud administrativa demasiado grande")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(45)
    try:
        client.connect(str(settings.admin_socket))
        client.sendall(request + b"\n")
        response = bytearray()
        while len(response) <= 262144:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    except OSError as exc:
        raise CupsError(f"El servicio administrativo no está disponible: {exc}") from exc
    finally:
        client.close()
    try:
        payload = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CupsError("Respuesta inválida del servicio administrativo") from exc
    if payload.get("ok") is not True:
        raise CupsError(payload.get("error", "Falló la operación administrativa"))
    return str(payload.get("message", ""))


def validate_network_uri(uri: str) -> None:
    parsed = urlparse(uri)
    if parsed.scheme != "socket" or not parsed.hostname:
        raise CupsError("Solo se aceptan impresoras de red socket://IP:9100")
    ipaddress.ip_address(parsed.hostname)
    if parsed.port not in (None, 9100):
        raise CupsError("El puerto de una Zebra de red debe ser 9100")


HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,62})?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62})?)*$"
)
GENERIC_NETWORK_SCHEMES = {"ipp", "ipps", "socket", "lpd"}


def validate_generic_network_uri(uri: str) -> None:
    parsed = urlparse(uri)
    if parsed.scheme not in GENERIC_NETWORK_SCHEMES or not parsed.hostname:
        raise CupsError(
            "Solo se aceptan impresoras de red ipp://, ipps://, socket:// o lpd://"
        )
    hostname = parsed.hostname
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if len(hostname) > 253 or not HOSTNAME_RE.fullmatch(hostname):
            raise CupsError("El host de la impresora de red no es válido") from None
    try:
        port = parsed.port
    except ValueError:
        raise CupsError("El puerto de la impresora de red no es válido") from None
    if port is not None and not 1 <= port <= 65535:
        raise CupsError("El puerto de la impresora de red no es válido")
