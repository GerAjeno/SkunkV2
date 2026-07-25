from skunk_pc.auth import hash_password, verify_password


def test_password_round_trip() -> None:
    encoded = hash_password("contraseña-segura-123")
    assert verify_password("contraseña-segura-123", encoded)
    assert not verify_password("otra-contraseña", encoded)

