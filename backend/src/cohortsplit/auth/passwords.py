"""Password hashing (Argon2id) and the password policy (FR-A1)."""

from argon2 import PasswordHasher

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "PasswordHasher",
    "PasswordPolicyError",
    "hash_password",
    "production_hasher",
    "validate_password_policy",
    "verify_password",
]

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


class PasswordPolicyError(ValueError):
    """The password violates the policy. The message never contains the password."""


def production_hasher() -> PasswordHasher:
    raise NotImplementedError


_hasher: PasswordHasher | None = None
_dummy_hash: str | None = None


def validate_password_policy(password: str) -> None:
    raise NotImplementedError


def hash_password(password: str) -> str:
    raise NotImplementedError


def verify_password(password_hash: str, password: str) -> bool:
    raise NotImplementedError
