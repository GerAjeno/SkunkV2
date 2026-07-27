from __future__ import annotations

import asyncio
import os
import socket
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    new_csrf_token,
    require_authenticated,
    require_csrf,
    verify_password,
)
from .config import settings
from .converter import SUPPORTED_SUFFIXES
from .cups import (
    CupsError,
    calibrate,
    cups_running,
    discover_usb_printers,
    get_printer,
    list_printers,
    run_admin_helper,
    send_test,
    validate_network_uri,
    validate_printer_name,
)
from .database import init_database
from .jobs import cancel_job, create_job, list_jobs


PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_runtime()
    settings.prepare_directories()
    init_database()
    yield


app = FastAPI(
    title="Skunk PC",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "dev-only-session-secret-change-me",
    session_cookie="skunk_session",
    max_age=8 * 60 * 60,
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")


class AddPrinterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    uri: str = Field(min_length=8, max_length=500)
    language: str = Field(pattern="^(epl2|zpl)$")
    media_type: str = Field(default="direct", pattern="^(thermal|direct)$")


def _server_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _auth_or_redirect(request: Request) -> RedirectResponse | None:
    if settings.dev_auth_bypass or request.session.get("authenticated") is True:
        return None
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@app.exception_handler(CupsError)
async def cups_error_handler(_request: Request, exc: CupsError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"ok": False, "detail": str(exc)},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if settings.dev_auth_bypass or request.session.get("authenticated"):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if verify_password(password, settings.password_hash):
        request.session.clear()
        request.session["authenticated"] = True
        request.session["csrf"] = new_csrf_token()
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": "Contraseña incorrecta"},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.post("/logout")
async def logout(request: Request):
    require_csrf(request)
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    redirect = _auth_or_redirect(request)
    if redirect:
        return redirect
    if "csrf" not in request.session:
        request.session["csrf"] = new_csrf_token()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "csrf_token": request.session["csrf"],
            "server_ip": _server_ip(),
            "port": settings.port,
            "max_upload_mb": settings.max_upload_mb,
        },
    )


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        PACKAGE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/api/status")
async def api_status(request: Request):
    require_authenticated(request)
    all_printers = await asyncio.to_thread(list_printers, include_non_zebra=True)
    zebra_printers = [printer for printer in all_printers if printer.is_zebra]
    return {
        "ok": True,
        "cups": cups_running(),
        "server_ip": _server_ip(),
        "port": settings.port,
        "zebra_count": len(zebra_printers),
        "foreign_count": len(all_printers) - len(zebra_printers),
        "ready_count": sum(printer.connected for printer in zebra_printers),
    }


@app.get("/api/printers")
async def api_printers(request: Request):
    require_authenticated(request)
    printers = await asyncio.to_thread(list_printers)
    return {"ok": True, "printers": [printer.as_dict() for printer in printers]}


def _device_snapshot() -> list[dict]:
    configured_uris: dict[str, str] = {}
    for printer in list_printers():
        configured_uris[printer.uri] = printer.name
        if printer.physical_uri:
            configured_uris[printer.physical_uri] = printer.name

    devices = []
    for device in discover_usb_printers():
        if not device.is_zebra:
            continue
        item = device.as_dict()
        item["serial_available"] = device.serial.strip().lower() not in {
            "",
            "0",
            "0.0",
            "unknown",
        }
        item["installed_queue"] = configured_uris.get(device.uri)
        devices.append(item)
    return devices


@app.get("/api/devices")
async def api_devices(request: Request):
    require_authenticated(request)
    devices = await asyncio.to_thread(_device_snapshot)
    return {"ok": True, "devices": devices}


@app.get("/api/jobs")
async def api_jobs(request: Request, limit: int = 30):
    require_authenticated(request)
    jobs = await asyncio.to_thread(list_jobs, limit)
    return {"ok": True, "jobs": jobs}


async def _save_upload(upload: UploadFile) -> Path:
    original_name = Path(upload.filename or "documento").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Formato no compatible",
        )
    destination = settings.upload_dir / f"{uuid.uuid4()}{suffix}"
    maximum = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > maximum:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"El archivo supera {settings.max_upload_mb} MB",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    if written == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="El archivo está vacío")
    return destination


@app.post("/api/jobs")
async def api_create_job(
    request: Request,
    printer_name: str = Form(...),
    fit_mode: str = Form("contain"),
    orientation: str = Form("auto"),
    copies: int = Form(1),
    document: UploadFile = File(...),
):
    require_csrf(request)
    printer = await asyncio.to_thread(
        get_printer,
        validate_printer_name(printer_name),
    )
    if not printer.is_zebra:
        raise HTTPException(status_code=400, detail="La cola no corresponde a una Zebra")
    if not printer.connected:
        raise HTTPException(status_code=409, detail="La impresora está desconectada")
    if fit_mode not in {"contain", "cover"}:
        raise HTTPException(status_code=400, detail="Ajuste inválido")
    if orientation not in {"auto", "portrait", "landscape"}:
        raise HTTPException(status_code=400, detail="Orientación inválida")
    if not 1 <= copies <= 20:
        raise HTTPException(status_code=400, detail="Copias inválidas")

    source = await _save_upload(document)
    try:
        job = await asyncio.to_thread(
            create_job,
            printer_name=printer.name,
            original_name=Path(document.filename or "documento").name,
            content_type=document.content_type or "application/octet-stream",
            source_path=source,
            fit_mode=fit_mode,
            orientation=orientation,
            copies=copies,
        )
    except Exception:
        source.unlink(missing_ok=True)
        raise
    return JSONResponse(status_code=202, content={"ok": True, "job": job})


@app.post("/api/jobs/{job_id}/cancel")
async def api_cancel_job(job_id: str, request: Request):
    require_csrf(request)
    if not await asyncio.to_thread(cancel_job, job_id):
        raise HTTPException(status_code=409, detail="El trabajo ya está en procesamiento")
    return {"ok": True}


@app.post("/api/printers/{printer_name}/test")
async def api_test_printer(printer_name: str, request: Request):
    require_csrf(request)
    await asyncio.to_thread(send_test, printer_name)
    return {"ok": True, "message": "Etiqueta de prueba enviada"}


@app.post("/api/printers/{printer_name}/calibrate")
async def api_calibrate_printer(printer_name: str, request: Request):
    require_csrf(request)
    await asyncio.to_thread(calibrate, printer_name)
    return {"ok": True, "message": "Calibración enviada"}


@app.post("/api/printers/{printer_name}/repair")
async def api_repair_printer(printer_name: str, request: Request):
    require_csrf(request)
    printer = await asyncio.to_thread(get_printer, printer_name)
    devices = await asyncio.to_thread(discover_usb_printers)
    devices = [device for device in devices if device.is_zebra]
    if printer.physical_uri:
        uri = printer.physical_uri
    elif len(devices) == 1:
        uri = devices[0].uri
    else:
        raise HTTPException(
            status_code=409,
            detail="No se pudo determinar automáticamente el dispositivo físico",
        )
    output = await asyncio.to_thread(
        run_admin_helper,
        "repair",
        printer.name,
        uri,
        printer.language,
        printer.media_type,
    )
    return {"ok": True, "message": output or "URI reparada y cola configurada en 4x6"}


@app.post("/api/printers")
async def api_add_printer(payload: AddPrinterRequest, request: Request):
    require_csrf(request)
    validate_printer_name(payload.name)
    if payload.uri.startswith("usb://"):
        devices = await asyncio.to_thread(discover_usb_printers)
        available = {device.uri for device in devices if device.is_zebra}
        if payload.uri not in available:
            raise HTTPException(status_code=409, detail="El dispositivo USB no está conectado")
    else:
        validate_network_uri(payload.uri)
    output = await asyncio.to_thread(
        run_admin_helper,
        "add",
        payload.name,
        payload.uri,
        payload.language,
        payload.media_type,
    )
    return {"ok": True, "message": output or "Impresora agregada"}


@app.delete("/api/printers/{printer_name}")
async def api_delete_printer(printer_name: str, request: Request):
    require_csrf(request)
    validate_printer_name(printer_name)
    output = await asyncio.to_thread(run_admin_helper, "delete", printer_name)
    return {"ok": True, "message": output or "Impresora eliminada"}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "skunk_pc.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
