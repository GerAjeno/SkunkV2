from skunk_pc import admin
from skunk_pc.cups import CommandResult


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
