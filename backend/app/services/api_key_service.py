"""Key generation/hashing for API-key auth. sha256 is sufficient here — these are
high-entropy random tokens, not human-chosen passwords, so no bcrypt/argon2 cost
factor is needed."""
from __future__ import annotations
import hashlib
import secrets

PREFIX_LEN = 12  # "thm_" + 8 chars, enough to disambiguate without help of the hash


def generate_key() -> tuple[str, str]:
    """Returns (raw_key, prefix). raw_key is shown to the user exactly once."""
    raw = "thm_" + secrets.token_urlsafe(24)
    return raw, raw[:PREFIX_LEN]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
