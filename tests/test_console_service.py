from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_console_runs_cmatrix_on_tty1_as_restricted_user() -> None:
    unit = (ROOT / "deploy" / "skunk-console.service").read_text()

    assert "User=skunkpc" in unit
    assert "ExecStart=/usr/bin/cmatrix" in unit
    assert "TTYPath=/dev/tty1" in unit
    assert "StandardInput=tty-force" in unit
    assert "Restart=no" in unit
    assert "OnSuccess=getty@tty1.service" in unit
    assert "OnFailure=getty@tty1.service" in unit
    assert "NoNewPrivileges=true" in unit


def test_install_and_update_manage_console_service() -> None:
    install = (ROOT / "install.sh").read_text()
    update = (ROOT / "update.sh").read_text()

    assert "fonts-dejavu-core colord cmatrix" in install
    assert "skunk-console.service" in install
    assert "enable getty@tty1.service" in install
    assert "disable --now getty@tty1.service" not in install
    assert "apt-get install -y cmatrix" in update
    assert "skunk-console.service" in update
    assert "enable getty@tty1.service" in update
