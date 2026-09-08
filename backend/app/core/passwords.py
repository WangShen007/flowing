"""Versioned password hashes; legacy SHA-256 is accepted only for migration."""
from __future__ import annotations

import hashlib
import secrets

ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    if len(encoded) == 64 and all(char in "0123456789abcdef" for char in encoded):
        return secrets.compare_digest(hashlib.sha256(password.encode()).hexdigest(), encoded)
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        count = int(iterations)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= count <= 2_000_000 or len(salt) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), count).hex()
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def needs_upgrade(encoded: str) -> bool:
    return not encoded.startswith(f"pbkdf2_sha256${ITERATIONS}$")
