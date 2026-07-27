from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_iso_autoinstall_is_guided_and_installs_local_package():
    data = yaml.safe_load((ROOT / "packaging/iso/user-data").read_text())
    config = data["autoinstall"]

    assert config["version"] == 1
    assert config["interactive-sections"] == ["*"]
    commands = "\n".join(config["late-commands"])
    assert "/cdrom/skunk/skunk-pc.deb" in commands
    assert "apt-get install -y /tmp/skunk-pc.deb" in commands
    assert "identity:" not in (ROOT / "packaging/iso/user-data").read_text()
    assert "password:" not in (ROOT / "packaging/iso/user-data").read_text()
    assert "storage:" not in (ROOT / "packaging/iso/user-data").read_text()


def test_iso_build_verifies_base_and_replays_boot_metadata():
    script = (ROOT / "scripts/build-iso.sh").read_text()

    assert "expected_base_sha256=" in script
    assert "-boot_image any replay" in script
    assert "autoinstall ds=nocloud" in script
    assert r"ds=nocloud\\;s=" not in script
    assert r"ds=nocloud\;s=" in script
    assert "skunk-pc-server-${version}-ubuntu-26.04-amd64.iso" in script


def test_first_boot_instructions_do_not_contain_site_secrets():
    instructions = (ROOT / "packaging/iso/PRIMER-INICIO.txt").read_text()

    assert "sudo skunk-setup" in instructions
    assert "skunk-activate-native-printing --check" in instructions
    assert "OneTime PC" not in instructions
    assert "Lasgarzas" not in instructions
