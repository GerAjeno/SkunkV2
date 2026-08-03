from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import skunk_pc.cups as cups
from skunk_pc.cups import (
    CommandResult,
    _attach_native_origins,
    _ppd_identity,
    list_cups_jobs,
    _selected_ppd_choice,
    calibrate,
    diagnose_printer,
    discover_usb_printers,
    parse_device_uri,
    parse_lpinfo_devices,
    send_test,
    set_darkness,
    validate_generic_network_uri,
    validate_printer_name,
)
from skunk_pc.schemas import Printer


def test_parse_lpinfo_devices() -> None:
    output = """
network ipp
direct usb://Zebra%20Technologies/ZTC%20GC420t%20(EPL)?serial=ABC123
network socket
"""
    assert parse_lpinfo_devices(output) == [
        "usb://Zebra%20Technologies/ZTC%20GC420t%20(EPL)?serial=ABC123"
    ]


def test_cups_local_datetime_is_converted_to_utc() -> None:
    converted = cups._cups_datetime(
        "Thu 30 Jul 2026 18:17:47",
        local_timezone=ZoneInfo("America/Santiago"),
    )

    assert converted == "2026-07-30T22:17:47+00:00"


def test_parse_zebra_uri() -> None:
    device = parse_device_uri(
        "usb://Zebra%20Technologies/ZTC%20GC420t%20(EPL)?serial=54J170200124"
    )
    assert device.manufacturer == "Zebra Technologies"
    assert device.model == "ZTC GC420t (EPL)"
    assert device.serial == "54J170200124"
    assert device.is_zebra is True


def test_selected_ppd_choice() -> None:
    output = "MediaType/Media Type: Saved *Thermal Direct"

    assert _selected_ppd_choice(output, "MediaType") == "Thermal"


def test_unprivileged_discovery_uses_only_admin_helper(monkeypatch) -> None:
    monkeypatch.setattr(cups, "_usb_cache_expires_at", 0.0)
    monkeypatch.setattr(cups, "run_command", lambda _args: pytest.fail("lpinfo was called"))
    monkeypatch.setattr(
        cups,
        "run_admin_helper",
        lambda action: (
            '[{"uri":"usb://Zebra/TLP2844?serial=41J114402245",'
            '"manufacturer":"Zebra","model":"TLP2844",'
            '"serial":"41J114402245","is_zebra":true}]'
            if action == "devices"
            else ""
        ),
    )

    devices = discover_usb_printers()

    assert len(devices) == 1
    assert devices[0].model == "TLP2844"
    assert devices[0].serial == "41J114402245"


def test_discovery_reuses_recent_result(monkeypatch) -> None:
    calls = 0

    def fake_run_command(_args):
        nonlocal calls
        calls += 1
        return CommandResult(
            0,
            "direct usb://Zebra/TLP2844?serial=41J114402245",
            "",
        )

    monkeypatch.setattr(cups, "_usb_cache_expires_at", 0.0)
    monkeypatch.setattr(cups, "run_command", fake_run_command)

    first = discover_usb_printers(allow_admin_helper=False)
    second = discover_usb_printers(allow_admin_helper=False)

    assert first == second
    assert calls == 1


def test_validate_printer_name() -> None:
    assert validate_printer_name("Planchetta_1") == "Planchetta_1"


def test_web_test_label_contains_origin_and_epl2_printer_name(monkeypatch) -> None:
    printer = Printer(
        name="Zebra_01",
        uri="usb://Zebra/TLP2844?serial=ABC",
        description="Zebra_01",
        make_model="Zebra EPL2",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="epl2",
        dpi=203,
        page_size="w288h432",
        media_type="thermal",
    )
    payloads = []
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)
    monkeypatch.setattr(cups, "send_raw", lambda _name, payload: payloads.append(payload))

    send_test("Zebra_01")

    assert "ORIGEN: PAGINA WEB" in payloads[0]
    assert "IMPRESORA: Zebra_01" in payloads[0]
    assert payloads[0].startswith("\nO\n")


def test_web_test_label_contains_origin_and_zpl_printer_name(monkeypatch) -> None:
    printer = Printer(
        name="Zebra_02",
        uri="socket://10.1.0.90:9100",
        description="Zebra_02",
        make_model="Zebra ZPL",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="zpl",
        dpi=203,
        page_size="w288h432",
    )
    payloads = []
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)
    monkeypatch.setattr(cups, "send_raw", lambda _name, payload: payloads.append(payload))

    send_test("Zebra_02")

    assert "ORIGEN: PAGINA WEB" in payloads[0]
    assert "IMPRESORA: Zebra_02" in payloads[0]


def test_send_test_prints_plain_page_for_generic_printer(monkeypatch) -> None:
    printer = Printer(
        name="Oficina_1",
        uri="ipp://192.168.1.50/ipp/print",
        description="Oficina_1",
        make_model="HP LaserJet",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=False,
        language="",
        dpi=600,
        page_size="Letter",
        media_type="",
    )
    calls = []
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)

    def fake_run_command(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return CommandResult(0, "", "")

    monkeypatch.setattr(cups, "run_command", fake_run_command)

    send_test("Oficina_1")

    arguments, kwargs = calls[0]
    assert arguments == ["lp", "-d", "Oficina_1"]
    assert "Oficina_1" in kwargs["input_text"]
    assert "-o" not in arguments


def test_set_darkness_sends_epl2_density_command(monkeypatch) -> None:
    printer = Printer(
        name="Zebra_01",
        uri="usb://Zebra/TLP2844?serial=ABC",
        description="Zebra_01",
        make_model="Zebra EPL2",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="epl2",
        dpi=203,
        page_size="w288h432",
        media_type="thermal",
    )
    payloads = []
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)
    monkeypatch.setattr(cups, "send_raw", lambda _name, payload: payloads.append(payload))

    set_darkness("Zebra_01", 12)

    assert payloads[0] == "\nD12\n"


def test_set_darkness_sends_zpl_darkness_command(monkeypatch) -> None:
    printer = Printer(
        name="Zebra_02",
        uri="socket://10.1.0.90:9100",
        description="Zebra_02",
        make_model="Zebra ZPL",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="zpl",
        dpi=203,
        page_size="w288h432",
    )
    payloads = []
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)
    monkeypatch.setattr(cups, "send_raw", lambda _name, payload: payloads.append(payload))

    set_darkness("Zebra_02", 5)

    assert payloads[0] == "~SD05\n"


def test_set_darkness_rejects_out_of_range_level(monkeypatch) -> None:
    printer = Printer(
        name="Zebra_01",
        uri="usb://Zebra/TLP2844?serial=ABC",
        description="Zebra_01",
        make_model="Zebra EPL2",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="epl2",
        dpi=203,
        page_size="w288h432",
        media_type="thermal",
    )
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)

    with pytest.raises(cups.CupsError, match="entre 0 y 15"):
        set_darkness("Zebra_01", 20)


def test_set_darkness_rejects_generic_printer(monkeypatch) -> None:
    printer = Printer(
        name="Oficina_1",
        uri="ipp://192.168.1.50/ipp/print",
        description="Oficina_1",
        make_model="HP LaserJet",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=False,
        language="",
        dpi=600,
        page_size="Letter",
        media_type="",
    )
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)

    with pytest.raises(cups.CupsError, match="solo está disponible para impresoras Zebra"):
        set_darkness("Oficina_1", 10)


def test_calibrate_rejects_generic_printer(monkeypatch) -> None:
    printer = Printer(
        name="Oficina_1",
        uri="ipp://192.168.1.50/ipp/print",
        description="Oficina_1",
        make_model="HP LaserJet",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=False,
        language="",
        dpi=600,
        page_size="Letter",
        media_type="",
    )
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)

    with pytest.raises(cups.CupsError, match="solo está disponible para impresoras Zebra"):
        calibrate("Oficina_1")


def test_diagnose_reports_generic_printer_specs_without_4x6_checks(monkeypatch) -> None:
    printer = Printer(
        name="Oficina_1",
        uri="ipp://192.168.1.50/ipp/print",
        description="Oficina_1",
        make_model="HP LaserJet",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=False,
        language="",
        dpi=600,
        page_size="Letter",
        media_type="",
    )
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)
    monkeypatch.setattr(cups, "run_command", lambda *_args, **_kwargs: CommandResult(0, "", ""))

    message = diagnose_printer("Oficina_1")

    assert "Resolución: 600 DPI" in message
    assert "Formato de página: Letter" in message
    assert "Resolución configurada incorrectamente" not in message
    assert "Tamaño configurado incorrectamente" not in message
    assert "No se detectaron problemas" in message


@pytest.mark.parametrize(
    "uri",
    [
        "ipp://192.168.1.50/ipp/print",
        "ipps://impresora.local:443/ipp/print",
        "socket://192.168.1.60:9101",
        "lpd://192.168.1.70/cola",
    ],
)
def test_validate_generic_network_uri_accepts_supported_schemes(uri: str) -> None:
    validate_generic_network_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "http://192.168.1.50/print",
        "ipp:///ipp/print",
        "socket://192.168.1.60:70000",
        "socket://192.168.1.60:0",
        "ipp://192.168.1.50:99999/print",
    ],
)
def test_validate_generic_network_uri_rejects_invalid_uris(uri: str) -> None:
    with pytest.raises(cups.CupsError):
        validate_generic_network_uri(uri)


def test_ppd_identifies_broken_zebra_queue(tmp_path: Path) -> None:
    (tmp_path / "Planchetta.ppd").write_text(
        '*Manufacturer: "Zebra"\n'
        '*ModelName: "Zebra EPL2 Label Printer"\n'
        '*NickName: "Zebra EPL2 Label Printer"\n',
        encoding="latin-1",
    )
    identity = _ppd_identity("Planchetta", ppd_dir=tmp_path)
    assert "Zebra EPL2" in identity


def test_diagnose_printer_reports_pending_jobs_and_usb_limit(monkeypatch) -> None:
    printer = Printer(
        name="Gabriela",
        uri="usb://Zebra/TLP2844?serial=0.0",
        description="Gabriela",
        make_model="Zebra EPL2",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="epl2",
        dpi=203,
        page_size="w288h432",
        media_type="thermal",
    )
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)
    def fake_run_command(arguments):
        if "not-completed" in arguments:
            return CommandResult(0, "Gabriela-20 user 1024 today", "")
        return CommandResult(0, "", "")

    monkeypatch.setattr(cups, "run_command", fake_run_command)

    message = diagnose_printer("Gabriela")

    assert "Hay 1 trabajo(s) pendiente(s)" in message
    assert "Conexión: dispositivo disponible" in message
    assert "Resolución: 203 DPI" in message
    assert "Formato: 4×6" in message
    assert "USB es unidireccional" in message


def test_diagnose_ignores_completed_job_reported_as_not_completed(monkeypatch) -> None:
    printer = Printer(
        name="Planchetta",
        uri="usb://Zebra/TLP2844?serial=ABC",
        description="Planchetta",
        make_model="Zebra EPL2",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="epl2",
        dpi=203,
        page_size="w288h432",
        media_type="thermal",
    )
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)

    def fake_run_command(arguments):
        if "not-completed" in arguments or "completed" in arguments:
            return CommandResult(0, "Planchetta-4 user 1024 today", "")
        return CommandResult(0, "", "")

    monkeypatch.setattr(cups, "run_command", fake_run_command)

    message = diagnose_printer("Planchetta")

    assert "No se detectaron problemas de software" in message
    assert "Trabajos pendientes: 0" in message


def test_list_cups_jobs_reports_native_origin_and_error(monkeypatch) -> None:
    def fake_run_command(arguments):
        if "all" in arguments:
            return CommandResult(
                0,
                "Zima-12 android-phone 18432 Mon 27 Jul 2026 17:30:00\n"
                "\tStatus: No pages were found.\n"
                "\tAlerts: job-completed-with-errors\n"
                "\tqueued for Zima\n"
                "Zima-13 iphone 2048 Mon 27 Jul 2026 17:31:00\n"
                "\tAlerts: job-completed-successfully\n",
                "",
            )
        return CommandResult(
            0,
            "Zima-12 android-phone 18432 Mon 27 Jul 2026 17:30:00\n"
            "Zima-13 iphone 2048 Mon 27 Jul 2026 17:31:00",
            "",
        )

    monkeypatch.setattr(cups, "run_command", fake_run_command)
    monkeypatch.setattr(
        cups,
        "run_admin_helper",
        lambda action, *_args: "{}" if action == "job-pages" else "[]",
    )

    jobs = list_cups_jobs()
    jobs_by_id = {job["cups_job_id"]: job for job in jobs}

    assert [job["cups_job_id"] for job in jobs] == ["Zima-13", "Zima-12"]
    assert jobs_by_id["Zima-12"]["source_device"] == "CUPS nativo · android-phone"
    assert jobs_by_id["Zima-12"]["printer_name"] == "Zima"
    assert jobs_by_id["Zima-12"]["status"] == "failed"
    assert jobs_by_id["Zima-12"]["error"] == "No pages were found."
    assert jobs_by_id["Zima-13"]["status"] == "completed"
    assert jobs_by_id["Zima-13"]["pages"] == 1


def test_list_cups_jobs_keeps_500_highest_job_numbers(monkeypatch) -> None:
    all_jobs = "\n".join(
        f"Zima-{number} owner 1024 Mon 27 Jul 2026 17:30:00"
        for number in range(1, 601)
    )

    def fake_run_command(arguments):
        if "all" in arguments:
            return CommandResult(0, all_jobs, "")
        return CommandResult(0, all_jobs, "")

    monkeypatch.setattr(cups, "run_command", fake_run_command)
    monkeypatch.setattr(
        cups,
        "run_admin_helper",
        lambda action, *_args: "{}" if action == "job-pages" else "[]",
    )

    recent = list_cups_jobs(500)

    assert len(recent) == 500
    assert recent[0]["cups_job_id"] == "Zima-600"
    assert recent[-1]["cups_job_id"] == "Zima-101"
    assert "Zima-100" not in {job["cups_job_id"] for job in recent}


def test_attach_native_origins_replaces_unknown_with_client_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        cups,
        "run_admin_helper",
        lambda *_args: (
            '[{"printer_name":"Zima","client_ip":"10.1.0.225",'
            '"created_at":"2026-07-27T18:00:04+00:00"}]'
        ),
    )
    native_jobs = [
        {
            "printer_name": "Zima",
            "source_device": "CUPS nativo · unknown",
            "created_at": "2026-07-27T18:00:04+00:00",
        }
    ]

    _attach_native_origins(native_jobs)

    assert native_jobs[0]["source_device"] == "IP 10.1.0.225"


def test_job_origins_is_an_allowed_admin_action() -> None:
    with pytest.raises(cups.CupsError, match="servicio administrativo"):
        cups.run_admin_helper("job-origins")


def test_job_pages_is_an_allowed_admin_action() -> None:
    with pytest.raises(cups.CupsError, match="servicio administrativo"):
        cups.run_admin_helper("job-pages")


def test_job_failures_is_an_allowed_admin_action() -> None:
    with pytest.raises(cups.CupsError, match="servicio administrativo"):
        cups.run_admin_helper("job-failures")


def test_native_rejection_is_reported_as_failed_job(monkeypatch) -> None:
    monkeypatch.setattr(
        cups,
        "run_admin_helper",
        lambda action, *_args: (
            '[{"printer_name":"Estacion-1","client_ip":"192.168.1.199",'
            '"created_at":"2026-07-30T18:40:09-04:00","bytes":527,'
            '"result":"client-error-bad-request"}]'
            if action == "job-failures"
            else "{}" if action == "job-pages" else "[]"
        ),
    )
    monkeypatch.setattr(
        cups,
        "run_command",
        lambda _arguments: CommandResult(0, "", ""),
    )

    jobs = list_cups_jobs()

    assert jobs[0]["printer_name"] == "Estacion-1"
    assert jobs[0]["source_device"] == "IP 192.168.1.199"
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["pages"] == 0
    assert "atributos o datos no válidos" in jobs[0]["error"]


def test_diagnose_ignores_failed_job_left_in_not_completed(monkeypatch) -> None:
    printer = Printer(
        name="Planchetta",
        uri="usb://Zebra/TLP2844?serial=ABC",
        description="Planchetta",
        make_model="Zebra EPL2",
        state="idle",
        state_message="idle",
        connected=True,
        is_zebra=True,
        language="epl2",
        dpi=203,
        page_size="w288h432",
        media_type="thermal",
    )
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)

    def fake_run_command(arguments):
        if "not-completed" in arguments:
            return CommandResult(
                0,
                "Planchetta-4 user 1024 today\n"
                "\tAlerts: job-completed-with-errors\n"
                "\tqueued for Planchetta",
                "",
            )
        return CommandResult(0, "", "")

    monkeypatch.setattr(cups, "run_command", fake_run_command)

    message = diagnose_printer("Planchetta")

    assert "No se detectaron problemas de software" in message
    assert "Trabajos pendientes: 0" in message
