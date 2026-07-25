from pathlib import Path

import pytest

import skunk_pc.cups as cups
from skunk_pc.cups import (
    CommandResult,
    _ppd_identity,
    discover_usb_printers,
    parse_device_uri,
    parse_lpinfo_devices,
    send_test,
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


def test_parse_zebra_uri() -> None:
    device = parse_device_uri(
        "usb://Zebra%20Technologies/ZTC%20GC420t%20(EPL)?serial=54J170200124"
    )
    assert device.manufacturer == "Zebra Technologies"
    assert device.model == "ZTC GC420t (EPL)"
    assert device.serial == "54J170200124"
    assert device.is_zebra is True


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
    )
    payloads = []
    monkeypatch.setattr(cups, "get_printer", lambda _name: printer)
    monkeypatch.setattr(cups, "send_raw", lambda _name, payload: payloads.append(payload))

    send_test("Zebra_01")

    assert "ORIGEN: PAGINA WEB" in payloads[0]
    assert "IMPRESORA: Zebra_01" in payloads[0]


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


def test_ppd_identifies_broken_zebra_queue(tmp_path: Path) -> None:
    (tmp_path / "Planchetta.ppd").write_text(
        '*Manufacturer: "Zebra"\n'
        '*ModelName: "Zebra EPL2 Label Printer"\n'
        '*NickName: "Zebra EPL2 Label Printer"\n',
        encoding="latin-1",
    )
    identity = _ppd_identity("Planchetta", ppd_dir=tmp_path)
    assert "Zebra EPL2" in identity
