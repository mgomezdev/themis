from app.services.api_key_service import generate_key, hash_key, PREFIX_LEN


def test_generate_key_shape():
    raw, prefix = generate_key()
    assert raw.startswith("thm_")
    assert prefix == raw[:PREFIX_LEN]
    assert len(raw) > PREFIX_LEN


def test_generate_key_is_random():
    raw1, _ = generate_key()
    raw2, _ = generate_key()
    assert raw1 != raw2


def test_hash_key_deterministic_and_not_reversible_looking():
    raw, _ = generate_key()
    assert hash_key(raw) == hash_key(raw)
    assert hash_key(raw) != raw
    assert len(hash_key(raw)) == 64  # sha256 hex
