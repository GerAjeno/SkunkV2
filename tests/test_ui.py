from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "skunk_pc"


def test_add_printer_cancel_buttons_do_not_submit_form() -> None:
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()

    assert template.count('type="button" data-close-add-printer') == 2
    assert (
        '<button id="add-printer-submit" class="btn btn-primary" '
        'type="submit">Crear impresora</button>'
    ) in template


def test_static_cache_is_current() -> None:
    service_worker = (PACKAGE_DIR / "static" / "sw.js").read_text()

    assert 'const CACHE = "skunk-pc-static-v6";' in service_worker


def test_add_printer_marks_missing_serials_and_installed_devices() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()

    assert '"S/N no informado"' in javascript
    assert "`YA INSTALADA: ${device.installed_queue}`" in javascript
    assert "option.disabled = Boolean(device.installed_queue)" in javascript


def test_add_printer_submit_shows_progress() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()

    assert 'submit.textContent = "Creando…"' in javascript
    assert 'message.textContent = "Configurando la impresora…"' in javascript


def test_project_icon_is_used_by_pages_and_manifest() -> None:
    index = (PACKAGE_DIR / "templates" / "index.html").read_text()
    login = (PACKAGE_DIR / "templates" / "login.html").read_text()
    manifest = (PACKAGE_DIR / "static" / "manifest.webmanifest").read_text()

    assert 'src="/static/icon-192.png"' in index
    assert 'src="/static/icon-192.png"' in login
    assert '"src": "/static/icon-192.png"' in manifest
    assert '"src": "/static/icon-512.png"' in manifest
