"""Password hashing (Argon2id) and the password policy (FR-A1).

Plaintext passwords are never stored, logged, or included in error messages.
"""

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "PasswordHasher",
    "PasswordPolicyError",
    "hash_password",
    "production_hasher",
    "validate_password_policy",
    "verify_against_dummy",
    "verify_password",
]

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


class PasswordPolicyError(ValueError):
    """The password violates the policy. The message never contains the password."""


def production_hasher() -> PasswordHasher:
    """Argon2id with argon2-cffi's RFC 9106 low-memory profile (t=3, m=64 MiB, p=4)."""
    return PasswordHasher(type=Type.ID)


# Module-level so tests can swap in cheap parameters; never None after first use.
_hasher: PasswordHasher | None = None
# Hash of a random value, verified against for unknown accounts to equalize timing.
_dummy_hash: str | None = None


def _get_hasher() -> PasswordHasher:
    global _hasher
    if _hasher is None:
        _hasher = production_hasher()
    return _hasher


def validate_password_policy(password: str) -> None:
    """12..128 characters, no composition rules."""
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} "
            "characters long."
        )


def hash_password(password: str) -> str:
    return _get_hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _get_hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_against_dummy(password: str) -> None:
    """Spend the same work as a real verification (unknown or inactive accounts)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dummy-password-for-timing-equalization")
    verify_password(_dummy_hash, password)
