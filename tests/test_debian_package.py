from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_debian_control_declares_runtime_dependencies() -> None:
    control = (ROOT / "packaging/debian/control.in").read_text()

    assert "Package: skunk-pc" in control
    assert "Architecture: amd64" in control
    for dependency in (
        "cups",
        "cups-filters",
        "avahi-daemon",
        "ghostscript",
        "poppler-utils",
        "python3-venv",
        "colord",
        "cmatrix",
    ):
        assert dependency in control


def test_postinst_uses_bundled_wheels_and_preserves_configuration() -> None:
    postinst = (ROOT / "packaging/debian/postinst.in").read_text()

    assert "--no-index" in postinst
    assert "--force-reinstall" in postinst
    assert "/usr/share/skunk-pc/wheels" in postinst
    assert "if [ -s /etc/skunk-pc/skunk.env ]" in postinst
    assert "/var/backups/skunk-pc/deb-migration" in postinst
    assert "/etc/systemd/system/$unit" in postinst
    assert "systemctl reenable" in postinst
    assert "skunk-setup" in postinst


def test_setup_refuses_to_overwrite_existing_configuration() -> None:
    setup = (ROOT / "scripts/skunk-setup").read_text()

    assert "Skunk PC ya está configurado" in setup
    assert "SKUNK_ADMIN_PASSWORD" in setup
    assert "chmod 0640" in setup
    assert "systemctl restart" in setup


def test_build_script_packages_services_and_admin_tools() -> None:
    build = (ROOT / "scripts/build-deb.sh").read_text()

    assert "pip wheel" in build
    assert "requirements.lock" in build
    assert "build-requirements.lock" in build
    assert 'build_venv="$work_dir/build-venv"' in build
    assert "--no-build-isolation" in build
    assert 'export SOURCE_DATE_EPOCH="$source_date_epoch"' in build
    assert "control.tar.xz" in build
    assert "data.tar.xz" in build
    assert "deploy/*.service" in build
    assert '"$package_root/usr/sbin"' in build
    assert "skunk-activate-native-printing" in build
    assert "skunk-setup" in build


def test_runtime_wheels_are_version_locked() -> None:
    lock = (ROOT / "packaging/debian/requirements.lock").read_text().splitlines()

    assert all("==" in requirement for requirement in lock if requirement)
    assert any(requirement.startswith("fastapi==") for requirement in lock)
    assert any(requirement.startswith("pillow==") for requirement in lock)
    assert any(requirement.startswith("pydantic-core==") for requirement in lock)

    build_lock = (
        ROOT / "packaging/debian/build-requirements.lock"
    ).read_text().splitlines()
    assert all("==" in requirement for requirement in build_lock if requirement)
    assert any(requirement.startswith("setuptools==") for requirement in build_lock)
