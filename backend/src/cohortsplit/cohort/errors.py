"""API errors of the cohort endpoints (``{"error": {"code", "message", ...}}``)."""

from collections.abc import Sequence

from cohortsplit.auth.errors import ApiError, ConflictError, ServiceUnavailableError

SCHEMA_INVENTORY_UNAVAILABLE = (
    "No crawl has been stored yet, so there is no schema to interpret against. "
    "Run the crawler first."
)


def schema_inventory_unavailable() -> ConflictError:
    return ConflictError("schema_inventory_unavailable", SCHEMA_INVENTORY_UNAVAILABLE)


def canonical_user_id_missing(message: str) -> ConflictError:
    return ConflictError("canonical_user_id_missing", message)


def llm_not_configured(message: str) -> ServiceUnavailableError:
    return ServiceUnavailableError("llm_not_configured", message)


def llm_misconfigured(message: str) -> ServiceUnavailableError:
    return ServiceUnavailableError("llm_misconfigured", message)


def llm_unavailable(message: str) -> ServiceUnavailableError:
    return ServiceUnavailableError("llm_unavailable", message)


class LlmInvalidOutputApiError(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__(502, "llm_invalid_output", message)


class InterpretationNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            404,
            "interpretation_not_found",
            "This interpretation does not exist, has expired or belongs to someone else. "
            "Interpret the request again.",
        )


def confirmation_mismatch() -> ConflictError:
    return ConflictError(
        "confirmation_mismatch",
        "The confirmation does not match the interpretation (spec hash or semantic version). "
        "Review the interpretation and confirm it again.",
    )


def semantic_version_changed(expected: str, current: str) -> ConflictError:
    return ConflictError(
        "semantic_version_changed",
        "The business context changed since this request was interpreted. Interpret the "
        "request again and review the new interpretation.",
        interpreted_version=expected,
        current_version=current,
    )


class ExportColumnUnsupportedError(ApiError):
    def __init__(self, columns: Sequence[str], user_table: str) -> None:
        super().__init__(
            422,
            "export_column_unsupported",
            f"Only columns of the user table {user_table} can be previewed or exported "
            "(one row per user): " + ", ".join(columns) + ".",
            extra={"columns": list(columns)},
        )


class UnknownValueError(ApiError):
    def __init__(self, missing: Sequence[tuple[str, str]]) -> None:
        listed = "; ".join(f"no {column} = '{value}'" for column, value in missing)
        super().__init__(
            422,
            "unknown_value",
            f"The warehouse has {listed}. Check the spelling or rephrase the request.",
            extra={"values": [{"column": c, "value": v} for c, v in missing]},
        )


class RowCapError(ApiError):
    def __init__(self, row_cap: int) -> None:
        super().__init__(
            422,
            "row_cap_exceeded",
            f"The cohort has more than the row cap of {row_cap:,} users, so it was not "
            "previewed. Narrow the request (or ask an administrator to raise "
            "COHORTSPLIT_WAREHOUSE_ROW_CAP).",
            extra={"row_cap": row_cap},
        )


class QueryTimeoutApiError(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__(504, "query_timeout", message)


class WarehouseApiError(ApiError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status, code, message)


class SqlValidationApiError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            500,
            "sql_validation_failed",
            "The generated SQL failed validation and was not executed. This is a defect; "
            "it has been logged.",
        )
