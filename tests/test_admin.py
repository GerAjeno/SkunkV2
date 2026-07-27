from skunk_pc import admin


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
