import re

from parserium_collector.features.session.crypto import (
    csrf_token,
    generate_pairing_code,
    generate_session_token,
    keyed_digest,
)


def test_keyed_digest_is_deterministic_and_purpose_separated() -> None:
    secret = b"s" * 32

    first = keyed_digest(secret, "pairing", "operator-code")
    second = keyed_digest(secret, "pairing", "operator-code")
    session_digest = keyed_digest(secret, "session", "operator-code")

    assert first == second
    assert first != session_digest
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert "operator-code" not in first


def test_generated_credentials_have_required_entropy_shapes() -> None:
    pairing_code = generate_pairing_code()
    session_token = generate_session_token()

    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", pairing_code)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", session_token)
    assert pairing_code != session_token


def test_csrf_token_is_stable_and_distinct_from_session_token() -> None:
    secret = b"k" * 32
    session_token = "a" * 43

    first = csrf_token(secret, session_token)
    second = csrf_token(secret, session_token)

    assert first == second
    assert first != session_token
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", first)
