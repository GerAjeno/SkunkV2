from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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


def discover_usb_printers() -> list[UsbPrinter]:
    result = run_command(["lpinfo", "-v"])
    require_success(result, "No se pudieron consultar dispositivos CUPS")
    return [
        parse_device_uri(uri)
        for uri in parse_lpinfo_devices(result.stdout)
        if uri.startswith("usb://")
    ]


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
    physical_devices = parse_lpinfo_devices(run_command(["lpinfo", "-v"]).stdout)
    physical_usb = [uri for uri in physical_devices if uri.startswith("usb://")]
    states = _states()
    printers: list[Printer] = []

    for name, uri in configured.items():
        option_result = run_command(["lpoptions", "-p", name])
        options = _parse_options(option_result.stdout) if option_result.returncode == 0 else {}
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
        language = "epl2" if "epl" in identity else "zpl"
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
                page_size=options.get("PageSize", options.get("media", "desconocido")),
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
    if printer.language == "epl2":
        payload = (
            "\nN\nq812\nQ1218,24\n"
            'A40,40,0,4,1,1,N,"SKUNK PC - PRUEBA EPL2"\n'
            'A40,105,0,3,1,1,N,"Etiqueta 4x6 - 203 DPI"\n'
            'B40,180,0,1,2,4,100,B,"SKUNK-PC-OK"\nP1\n'
        )
    else:
        payload = (
            "^XA^PW812^LL1218"
            "^FO40,40^A0N,42,42^FDSKUNK PC - PRUEBA ZPL^FS"
            "^FO40,105^A0N,30,30^FDEtiqueta 4x6 - 203 DPI^FS"
            "^FO40,180^BY3^BCN,100,Y,N,N^FDSKUNK-PC-OK^FS^XZ"
        )
    send_raw(printer_name, payload)


def calibrate(printer_name: str) -> None:
    printer = get_printer(printer_name)
    send_raw(printer_name, "\njc\n" if printer.language == "epl2" else "~JC\n^XA^JUS^XZ")


def run_admin_helper(action: str, *arguments: str) -> str:
    allowed_actions = {"add", "repair", "delete", "configure", "enable"}
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
        while len(response) <= 65536:
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
