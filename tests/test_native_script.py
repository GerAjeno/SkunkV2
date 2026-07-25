from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "activate-native-printing.sh"
)


def test_native_activation_has_safe_check_and_rollback() -> None:
    contents = SCRIPT.read_text()

    assert "--check" in contents
    assert "cupsd -t" in contents
    assert "trap rollback ERR" in contents
    assert "media-default" in contents
    assert "media-ready" in contents
    assert "restore_service_state cups.service" in contents
    assert 'print "Port 631"' in contents
    assert 'print "  Allow @LOCAL"' in contents
    assert "cupsctl --share-printers" not in contents
    assert (
        'candidate="$(mktemp /etc/cups/cupsd.conf.skunk-candidate.XXXXXX)"'
        in contents
    )
    assert 'candidate="$(mktemp)"' not in contents


def test_native_activation_limits_firewall_rules_to_detected_subnet() -> None:
    contents = SCRIPT.read_text()

    assert 'ufw allow from "$subnet" to any port 631 proto tcp' in contents
    assert 'ufw allow from "$subnet" to any port 5353 proto udp' in contents
