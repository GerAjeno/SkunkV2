from pathlib import Path

import skunk_pc.cups as cups
from skunk_pc.cups import (
    CommandResult,
    _ppd_identity,
    discover_usb_printers,
    parse_device_uri,
    parse_lpinfo_devices,
    validate_printer_name,
)


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


def test_discovery_uses_admin_helper_when_cups_forbids_lpinfo(monkeypatch) -> None:
    monkeypatch.setattr(
        cups,
        "run_command",
        lambda _args: CommandResult(1, "", "Forbidden"),
    )
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


def test_validate_printer_name() -> None:
    assert validate_printer_name("Planchetta_1") == "Planchetta_1"


def test_ppd_identifies_broken_zebra_queue(tmp_path: Path) -> None:
    (tmp_path / "Planchetta.ppd").write_text(
        '*Manufacturer: "Zebra"\n'
        '*ModelName: "Zebra EPL2 Label Printer"\n'
        '*NickName: "Zebra EPL2 Label Printer"\n',
        encoding="latin-1",
    )
    identity = _ppd_identity("Planchetta", ppd_dir=tmp_path)
    assert "Zebra EPL2" in identity
