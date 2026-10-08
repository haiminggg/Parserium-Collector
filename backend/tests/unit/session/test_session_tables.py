from parserium_collector.adapters.database.tables import local_sessions, pairing_codes


def test_pairing_codes_store_only_keyed_digests_and_timestamps() -> None:
    assert pairing_codes.primary_key.columns.keys() == ["id"]
    assert pairing_codes.c.code_digest.type.length == 64
    assert pairing_codes.c.code_digest.unique is True
    assert pairing_codes.c.created_at.type.timezone is True
    assert pairing_codes.c.expires_at.type.timezone is True
    assert pairing_codes.c.expires_at.index is True
    assert pairing_codes.c.used_at.nullable is True
    assert "code" not in pairing_codes.c


def test_local_sessions_store_only_keyed_token_digests_and_timestamps() -> None:
    assert local_sessions.primary_key.columns.keys() == ["token_digest"]
    assert local_sessions.c.token_digest.type.length == 64
    assert local_sessions.c.created_at.type.timezone is True
    assert local_sessions.c.last_seen_at.type.timezone is True
    assert local_sessions.c.last_seen_at.index is True
    assert local_sessions.c.revoked_at.nullable is True
    assert "token" not in local_sessions.c
    assert "csrf_token" not in local_sessions.c
