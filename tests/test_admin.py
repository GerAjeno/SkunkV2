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


def test_reboot_is_scheduled_without_accepting_arguments(monkeypatch) -> None:
    commands: list[tuple[list[str], int | None]] = []

    def fake_run_command(arguments, timeout=None, **_kwargs):
        commands.append((arguments, timeout))
        return CommandResult(0, "", "")

    monkeypatch.setattr(admin, "run_command", fake_run_command)

    message = admin.dispatch("reboot", [])

    assert commands == [
        (
            [
                "systemd-run",
                "--unit=skunk-pc-reboot",
                "--on-active=2s",
                "--collect",
                "systemctl",
                "reboot",
            ],
            10,
        )
    ]
    assert message == "Reinicio del servidor programado"


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


def test_delete_cancels_jobs_removes_queue_and_verifies_result(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run_command(arguments, **_kwargs):
        commands.append(arguments)
        if arguments == ["lpstat", "-p", "Gabriela"] and len(commands) == 4:
            return CommandResult(1, "", "no existe")
        return CommandResult(0, "", "")

    monkeypatch.setattr(admin, "run_command", fake_run_command)

    message = admin.dispatch("delete", ["Gabriela"])

    assert commands == [
        ["lpstat", "-p", "Gabriela"],
        ["cancel", "-a", "-x", "Gabriela"],
        ["lpadmin", "-x", "Gabriela"],
        ["lpstat", "-p", "Gabriela"],
    ]
    assert message == "Impresora Gabriela y su configuración fueron eliminadas"


def test_delete_fails_when_cups_keeps_queue(monkeypatch) -> None:
    monkeypatch.setattr(
        admin,
        "run_command",
        lambda _arguments, **_kwargs: CommandResult(0, "", ""),
    )

    try:
        admin.dispatch("delete", ["Gabriela"])
    except Exception as exc:
        assert str(exc) == "CUPS mantuvo la cola después de solicitar su eliminación"
    else:
        raise AssertionError("La eliminación debía fallar si la cola seguía presente")


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


def test_rename_creates_new_queue_then_removes_old(monkeypatch) -> None:
    commands: list[list[str]] = []
    configured: list[tuple[str, str, str, str]] = []

    def fake_run_command(arguments, **_kwargs):
        commands.append(arguments)
        if arguments == ["lpstat", "-p", "Zima"]:
            return CommandResult(1, "", "no existe")
        if arguments == ["lpstat", "-p", "Gabriela"] and len(commands) > 4:
            return CommandResult(1, "", "no existe")
        return CommandResult(0, "", "")

    monkeypatch.setattr(admin, "run_command", fake_run_command)
    monkeypatch.setattr(
        admin,
        "_configure",
        lambda *args: configured.append(args),
    )

    message = admin.dispatch(
        "rename",
        ["Gabriela", "Zima", "usb://Zebra/TLP2844", "epl2", "thermal"],
    )

    assert configured == [("Zima", "usb://Zebra/TLP2844", "epl2", "thermal")]
    assert ["cancel", "-a", "-x", "Gabriela"] in commands
    assert ["lpadmin", "-x", "Gabriela"] in commands
    assert message == "Impresora Gabriela renombrada como Zima"


def test_native_job_pages_reads_retained_pdf_metadata(monkeypatch, tmp_path) -> None:
    (tmp_path / "d00026-001").write_bytes(
        b"%PDF /Type /Pages /Count 3 "
        b"/Type /Page /Type /Page /Type /Page"
    )

    assert admin._native_job_pages(tmp_path) == {"26": 3}
