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

    assert 'const CACHE = "skunk-pc-static-v20";' in service_worker


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


def test_periodic_refreshes_do_not_overlap() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()

    assert "let refreshInProgress = false;" in javascript
    assert "if (refreshInProgress) return;" in javascript


def test_print_form_survives_async_submission() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()

    assert "const form = event.currentTarget;" in javascript
    assert "form.reset();" in javascript
    assert "event.currentTarget.reset();" not in javascript
    assert 'message.className = "form-message error";' in javascript


def test_job_time_uses_24_hour_clock() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()

    assert 'hourCycle: "h23"' in javascript
    assert 'second: "2-digit"' in javascript


def test_job_history_has_filters_page_sizes_and_navigation() -> None:
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()

    assert 'id="jobs-page-size"' in template
    assert '<option value="30">30</option>' in template
    assert '<option value="50">50</option>' in template
    assert '<option value="100">100</option>' in template
    assert 'id="jobs-date-from"' in template
    assert 'id="jobs-printer"' in template
    assert 'id="jobs-origin"' in template
    assert 'id="jobs-result"' in template
    assert 'id="jobs-previous"' in template
    assert 'id="jobs-next"' in template
    assert 'page_size: $("#jobs-page-size").value' in javascript


def test_mobile_history_uses_cards_and_collapsible_filters() -> None:
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()
    stylesheet = (PACKAGE_DIR / "static" / "app.css").read_text()

    assert 'id="mobile-filters-toggle"' in template
    assert 'aria-controls="job-filters"' in template
    assert 'data-label="Trabajo"' in javascript
    assert 'data-label="Origen"' in javascript
    assert 'data-label="Resultado"' in javascript
    assert 'classList.toggle("mobile-open")' in javascript
    assert "content: attr(data-label)" in stylesheet
    assert ".job-filters:not(.mobile-open)" in stylesheet


def test_printer_management_actions_are_available() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()

    assert 'data-action="diagnose"' in javascript
    assert 'data-action="purge"' in javascript
    assert 'data-action="delete"' in javascript
    assert 'data-action="rename"' in javascript
    assert "Se cancelarán todos los trabajos pendientes" in javascript
    assert "todos sus trabajos, su cola" in javascript
    assert "configuración de CUPS" in javascript
    assert 'id="diagnostic-dialog"' in template
    assert 'id="rename-printer-dialog"' in template


def test_history_can_be_exported_to_excel() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()

    assert 'id="jobs-export"' in template
    assert "/api/jobs/export.xlsx" in javascript
    assert 'query.delete("page")' in javascript


def test_printer_actions_use_visible_confirmation_and_progress() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()

    assert 'id="confirmation-dialog"' in template
    assert 'id="confirmation-accept"' in template
    assert 'confirmLabel: "Eliminar definitivamente"' in javascript
    assert 'delete: "Eliminando…"' in javascript
    assert 'button.textContent = "Esperando confirmación…";' in javascript
    assert "window.confirm" not in javascript


def test_reboot_button_blocks_page_and_reconnects_after_three_minutes() -> None:
    javascript = (PACKAGE_DIR / "static" / "app.js").read_text()
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()
    stylesheet = (PACKAGE_DIR / "static" / "app.css").read_text()
    backend = (PACKAGE_DIR / "main.py").read_text()

    assert template.index('id="reboot-button"') < template.index('id="theme-toggle"')
    assert 'id="reboot-overlay"' in template
    assert 'id="reboot-countdown"' in template
    assert "const REBOOT_DURATION_MS = 3 * 60 * 1000;" in javascript
    assert 'api("/api/system/reboot", { method: "POST" })' in javascript
    assert (
        javascript.index("const button = event.currentTarget;")
        < javascript.index("const accepted = await askConfirmation({")
    )
    assert "showRebootOverlay(deadline);" in javascript
    assert 'fetch("/api/status"' in javascript
    assert "window.location.reload();" in javascript
    assert ".reboot-overlay" in stylesheet
    assert '@app.post("/api/system/reboot")' in backend
    assert 'run_admin_helper, "reboot"' in backend
