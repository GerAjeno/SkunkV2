from pathlib import Path


INSTALL_SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"


def test_install_reloads_dbus_after_installing_colord() -> None:
    contents = INSTALL_SCRIPT.read_text()

    install_position = contents.index("apt-get install -y")
    colord_position = contents.index("colord", install_position)
    reload_position = contents.index(
        "systemctl reload dbus.service", colord_position
    )

    assert install_position < colord_position < reload_position
