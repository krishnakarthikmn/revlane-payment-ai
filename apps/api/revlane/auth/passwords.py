"""Password hashing with scrypt, from the standard library.

A stored hash names its own parameters, so they can be raised later without
breaking the hashes already stored.
"""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1
KEY_BYTES = 32
MIN_LENGTH = 10


class WeakPassword(ValueError):
    pass


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES)


def hash_password(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"A password needs at least {MIN_LENGTH} characters.")
    salt = secrets.token_bytes(16)
    key = _derive(password, salt, N, R, P)
    encode = base64.b64encode
    return f"scrypt${N}${R}${P}${encode(salt).decode()}${encode(key).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    """True if the password matches. Takes the same time whether or not there
    is a hash to compare with, so a missing account cannot be told from a
    wrong password by timing."""
    try:
        scheme, n, r, p, salt, key = (stored or DUMMY).split("$")
        expected = base64.b64decode(key)
        derived = _derive(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return scheme == "scrypt" and hmac.compare_digest(derived, expected) and stored is not None


# Compared against when there is no account, so the work done is the same.
_EMPTY_SALT = base64.b64encode(bytes(16)).decode()
_EMPTY_KEY = base64.b64encode(bytes(KEY_BYTES)).decode()
DUMMY = f"scrypt${N}${R}${P}${_EMPTY_SALT}${_EMPTY_KEY}"
