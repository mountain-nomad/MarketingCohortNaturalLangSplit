"""Argon2id hashing and the password policy (FR-A1). Pure unit tests, no database."""

import pytest
from argon2 import Type

from cohortsplit.auth import passwords
from cohortsplit.auth.passwords import PasswordPolicyError, validate_password_policy
from tests.auth.helpers import STRONG_PASSWORD


def test_hash_is_argon2id_and_never_contains_plaintext() -> None:
    hashed = passwords.hash_password(STRONG_PASSWORD)

    assert hashed.startswith("$argon2id$")
    assert STRONG_PASSWORD not in hashed


def test_hashes_are_salted() -> None:
    assert passwords.hash_password(STRONG_PASSWORD) != passwords.hash_password(STRONG_PASSWORD)


def test_verify_accepts_correct_and_rejects_wrong_password() -> None:
    hashed = passwords.hash_password(STRONG_PASSWORD)

    assert passwords.verify_password(hashed, STRONG_PASSWORD) is True
    assert passwords.verify_password(hashed, STRONG_PASSWORD + "x") is False


def test_verify_returns_false_for_malformed_hash() -> None:
    assert passwords.verify_password("not-a-hash", STRONG_PASSWORD) is False


def test_production_hasher_is_memory_hard_argon2id() -> None:
    hasher = passwords.production_hasher()

    assert hasher.type is Type.ID
    # OWASP minimum for Argon2id is 19 MiB / t=2; we use argon2-cffi's RFC 9106 profile.
    assert hasher.memory_cost >= 64 * 1024
    assert hasher.time_cost >= 3


@pytest.mark.parametrize("length", [12, 13, 64, 128])
def test_policy_accepts_lengths_12_to_128(length: int) -> None:
    validate_password_policy("a" * length)


@pytest.mark.parametrize("length", [0, 1, 11, 129, 500])
def test_policy_rejects_lengths_outside_12_to_128(length: int) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("a" * length)


def test_policy_has_no_composition_rules() -> None:
    validate_password_policy("all lowercase words only")


def test_policy_counts_characters_not_bytes() -> None:
    validate_password_policy("é" * 12)  # 24 bytes in UTF-8, 12 characters
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("é" * 129)


def test_policy_error_does_not_echo_the_password() -> None:
    with pytest.raises(PasswordPolicyError) as excinfo:
        validate_password_policy("tiny-secret")

    assert "tiny-secret" not in str(excinfo.value)
