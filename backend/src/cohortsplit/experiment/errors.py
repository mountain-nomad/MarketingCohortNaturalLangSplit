"""API errors of the experiment endpoints (``{"error": {"code", "message", ...}}``)."""

from collections.abc import Sequence

from cohortsplit.auth.errors import ApiError, ConflictError, UnprocessableError


class PreparedCohortNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            404,
            "prepared_cohort_not_found",
            "This previewed cohort does not exist, has expired or belongs to someone else. "
            "Interpret and preview the request again.",
        )


class RunNotFoundError(ApiError):
    """Unknown runs and runs the caller may not see get the same answer (ruling E4)."""

    def __init__(self) -> None:
        super().__init__(404, "run_not_found", "Run not found.")


def cohort_empty() -> UnprocessableError:
    return UnprocessableError(
        "cohort_empty",
        "The cohort is empty now: there is nothing to split. Rephrase the request or check "
        "the business context.",
    )


def empty_group_confirmation_required(groups: Sequence[str]) -> ConflictError:
    listed = " and ".join(groups)
    return ConflictError(
        "empty_group_confirmation_required",
        f"The {listed} group has no users. Confirm that you want to export it anyway.",
        groups=list(groups),
    )


def canonical_user_id_changed(stored: str, current: str | None) -> ConflictError:
    return ConflictError(
        "canonical_user_id_changed",
        "The canonical user identifier changed since this run was made, so extra columns "
        "cannot be matched to its users. Download the user ids only.",
        run_canonical_user_id=stored,
        current_canonical_user_id=current,
    )


class UnsupportedUserIdError(ApiError):
    def __init__(self, type_name: str) -> None:
        super().__init__(
            500,
            "unsupported_user_id",
            f"The canonical user id column returns values of type {type_name}, which cannot "
            "be assigned deterministically. Use an integer, text or UUID key.",
        )
