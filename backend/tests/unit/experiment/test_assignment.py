"""Deterministic test/control assignment (FR-8, BR-5, AC-14 .. AC-18, ruling E1).

The algorithm is pinned by golden vectors computed independently from the written
specification::

    bucket = int.from_bytes(sha256(utf8(key) + b"\\x00" + utf8(user_id_text))[:8], "big") % 10_000
    group  = "test" if bucket < test_percent * 100 else "control"

A refactor that changes any assignment fails here.
"""

import hashlib
import json
import os
import subprocess
import sys
import uuid

import pytest
from pydantic import ValidationError

from cohortsplit.experiment.assignment import (
    ASSIGNMENT_VERSION,
    BUCKETS,
    Allocation,
    InvalidExperimentKeyError,
    assign,
    bucket,
    canonical_id_text,
    split,
    validate_experiment_key,
)

KEY = "push_black_friday_2026"
HALF = Allocation(test=50, control=50)
NINETY = Allocation(test=90, control=10)


def reference_bucket(key: str, user_id_text: str) -> int:
    """The written specification, independent of the implementation."""
    digest = hashlib.sha256(key.encode("utf-8") + b"\x00" + user_id_text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 10_000


# (key, canonical user id text, expected bucket) — computed once from the specification.
GOLDEN = [
    (KEY, "1", 8125),
    (KEY, "2", 8898),
    (KEY, "4", 4720),
    (KEY, "6", 374),
    (KEY, "7", 8896),
    (KEY, "13", 245),
    (KEY, "42", 5720),
    (KEY, "1000000", 9249),
    (KEY, "0", 6541),
    ("aa_check_q1", "7", 3283),
    ("Exp-1.v2", "user-abc", 5911),
]


def test_algorithm_constants() -> None:
    assert BUCKETS == 10_000
    assert ASSIGNMENT_VERSION == 1


@pytest.mark.parametrize(("key", "user_id", "expected"), GOLDEN)
def test_golden_vectors(key: str, user_id: str, expected: int) -> None:
    assert bucket(key, user_id) == expected
    assert reference_bucket(key, user_id) == expected


def test_golden_groups_at_50_50_and_90_10() -> None:
    assert assign(KEY, 1, HALF) == "control"  # 8125 >= 5000
    assert assign(KEY, 4, HALF) == "test"  # 4720 < 5000
    assert assign(KEY, 6, HALF) == "test"
    assert assign(KEY, 1, NINETY) == "test"  # 8125 < 9000
    assert assign(KEY, 1_000_000, NINETY) == "control"  # 9249 >= 9000
    assert assign(KEY, 13, Allocation(test=3, control=97)) == "test"  # 245 < 300
    assert assign(KEY, 6, Allocation(test=3, control=97)) == "control"  # 374 >= 300


def test_threshold_is_exclusive() -> None:
    # bucket 4720: test for test >= 48 %, control for test <= 47 %.
    assert assign(KEY, 4, Allocation(test=48, control=52)) == "test"
    assert assign(KEY, 4, Allocation(test=47, control=53)) == "control"


def test_integer_and_string_ids_share_the_canonical_text() -> None:
    assert canonical_id_text(42) == "42"
    assert canonical_id_text("42") == "42"
    assert canonical_id_text(-3) == "-3"
    assert assign(KEY, 42, HALF) == assign(KEY, "42", HALF)
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert canonical_id_text(uid) == "12345678-1234-5678-1234-567812345678"


@pytest.mark.parametrize("bad", [True, None, 1.5, b"1", ""])
def test_unsupported_ids_are_refused(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_id_text(bad)


def test_assignment_depends_on_key() -> None:
    other = [assign("another_key", u, HALF) for u in range(1, 200)]
    mine = [assign(KEY, u, HALF) for u in range(1, 200)]
    assert other != mine


def test_split_twice_identical_and_independent_of_order_and_size() -> None:
    ids = list(range(1, 501))
    first = split(KEY, ids, HALF)
    again = split(KEY, list(reversed(ids)), HALF)
    subset = split(KEY, ids[:50], HALF)

    assert set(first.test) == set(again.test)
    assert set(first.control) == set(again.control)
    # BR-5: a user's group does not depend on who else is in the cohort.
    assert set(subset.test) == set(first.test) & set(ids[:50])


def test_split_keeps_input_order_within_groups() -> None:
    ids = [9, 3, 7, 1, 5]
    result = split(KEY, ids, HALF)
    assert list(result.test) == [i for i in ids if i in set(result.test)]
    assert list(result.control) == [i for i in ids if i in set(result.control)]


def test_groups_disjoint_and_union_is_cohort() -> None:
    ids = list(range(1, 2001))
    result = split(KEY, ids, Allocation(test=30, control=70))

    assert not set(result.test) & set(result.control)
    assert set(result.test) | set(result.control) == set(ids)
    assert len(result.test) + len(result.control) == len(ids)


def test_same_user_same_group_in_different_cohorts() -> None:
    cohort_a = list(range(1, 300))
    cohort_b = list(range(150, 600))
    a = split(KEY, cohort_a, HALF)
    b = split(KEY, cohort_b, HALF)

    shared = set(cohort_a) & set(cohort_b)
    assert shared
    for user in shared:
        assert (user in set(a.test)) == (user in set(b.test))


@pytest.mark.parametrize(("test", "control"), [(50, 50), (90, 10)], ids=["50-50", "90-10"])
def test_distribution_within_one_point(test: int, control: int) -> None:
    ids = range(1, 100_001)
    result = split(KEY, ids, Allocation(test=test, control=control))

    share = 100 * len(result.test) / 100_000
    assert abs(share - test) <= 1.0
    assert abs(100 * len(result.control) / 100_000 - control) <= 1.0


def test_assignment_identical_in_a_fresh_interpreter() -> None:
    """AC-14 across a restart: a new process with a different hash seed agrees."""
    ids = list(range(1, 301))
    here = [assign(KEY, u, HALF) for u in ids]
    code = (
        "import json; from cohortsplit.experiment.assignment import Allocation, assign; "
        f"print(json.dumps([assign({KEY!r}, u, Allocation(test=50, control=50)) "
        "for u in range(1, 301)]))"
    )
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        output = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
        ).stdout
        assert json.loads(output) == here


# -- validation (AC-18) ------------------------------------------------------------------


def test_default_allocation_is_50_50() -> None:
    assert Allocation() == HALF


@pytest.mark.parametrize(
    ("test", "control"),
    [(60, 50), (50, 49), (100, 0), (0, 100), (99.5, 0.5), (-1, 101), (101, -1)],
)
def test_invalid_allocations_are_refused(test: object, control: object) -> None:
    with pytest.raises(ValidationError):
        Allocation(test=test, control=control)  # type: ignore[arg-type]


@pytest.mark.parametrize(("test", "control"), [(1, 99), (99, 1), (10, 90)])
def test_extreme_valid_allocations(test: int, control: int) -> None:
    assert Allocation(test=test, control=control).test == test


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  push_black_friday_2026  ", "push_black_friday_2026"),
        ("Exp-1.v2", "Exp-1.v2"),
        ("a", "a"),
        ("A" * 100, "A" * 100),
    ],
)
def test_valid_keys_are_trimmed(raw: str, expected: str) -> None:
    assert validate_experiment_key(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "\t\n",
        "has space",
        "../etc/passwd",
        "a/b",
        "a\\b",
        "-leading",
        "trailing.",
        ".hidden",
        "key\x00nul",
        "ключ",
        "émoji😀",
        'quote"',
        "semi;colon",
        "A" * 101,
    ],
)
def test_invalid_keys_are_refused(raw: str) -> None:
    with pytest.raises(InvalidExperimentKeyError):
        validate_experiment_key(raw)
