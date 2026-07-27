from skunk_pc import admin
from skunk_pc.cups import CommandResult
from skunk_pc.admin import _native_job_origins


def test_add_response_includes_selected_media_type(monkeypatch) -> None:
    monkeypatch.setattr(admin, "_configure", lambda *args: None)

    message = admin.dispatch(
        "add",
        ["Gabriela", "usb://Zebra/TLP2844", "epl2", "thermal"],
    )

    assert message == (
        "Impresora Gabriela configurada en 4×6, 203 DPI, "
        "EPL2, Thermal y URI estable"
    )


def test_purge_cancels_all_jobs_without_deleting_printer(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run_command(arguments, **_kwargs):
        commands.append(arguments)
        return CommandResult(0, "", "")

    monkeypatch.setattr(admin, "run_command", fake_run_command)

    message = admin.dispatch("purge", ["Gabriela"])

    assert commands == [
        ["lpstat", "-p", "Gabriela"],
        ["cancel", "-a", "-x", "Gabriela"],
    ]
    assert message == "Trabajos y registros fallidos de Gabriela eliminados"


def test_native_job_origins_extracts_ip_printer_and_time(tmp_path) -> None:
    access_log = tmp_path / "access_log"
    access_log.write_text(
        '10.1.0.225 - - [27/Jul/2026:18:00:04 +0000] '
        '"POST /printers/Zima HTTP/1.1" 200 4696 Print-Job successful-ok\n'
        'localhost - root [27/Jul/2026:18:00:18 +0000] '
        '"POST / HTTP/1.1" 200 1816 CUPS-Get-Devices -\n'
    )

    assert _native_job_origins(access_log) == [
        {
            "printer_name": "Zima",
            "client_ip": "10.1.0.225",
            "created_at": "2026-07-27T18:00:04+00:00",
        }
    ]
