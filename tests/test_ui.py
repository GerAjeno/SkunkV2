from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "skunk_pc"


def test_add_printer_cancel_buttons_do_not_submit_form() -> None:
    template = (PACKAGE_DIR / "templates" / "index.html").read_text()

    assert template.count('type="button" data-close-add-printer') == 2
    assert '<button class="btn btn-primary" type="submit">Crear impresora</button>' in template


def test_static_cache_is_bumped_for_dialog_fix() -> None:
    service_worker = (PACKAGE_DIR / "static" / "sw.js").read_text()

    assert 'const CACHE = "skunk-pc-static-v3";' in service_worker
