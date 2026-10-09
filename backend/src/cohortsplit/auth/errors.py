"""API errors with a stable machine-readable code.

Rendered as ``{"error": {"code": ..., "message": ..., **extra}}`` by the handler that
``cohortsplit.auth.wiring.install_auth`` registers.
"""

from collections.abc import Mapping


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers or {})
        self.extra = dict(extra or {})

    def body(self) -> dict[str, object]:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


class NotAuthenticatedError(ApiError):
    def __init__(self) -> None:
        super().__init__(401, "not_authenticated", "Sign in to continue.")


class PermissionDeniedError(ApiError):
    def __init__(self, message: str = "You do not have permission to do this.") -> None:
        super().__init__(403, "permission_denied", message)


class PasswordChangeRequiredError(ApiError):
    def __init__(self) -> None:
        super().__init__(403, "password_change_required", "Set a new password before continuing.")


class CsrfError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            403, "csrf_failed", "Missing or invalid CSRF token. Reload the page and try again."
        )


class NotFoundError(ApiError):
    def __init__(self, what: str = "Resource") -> None:
        super().__init__(404, "not_found", f"{what} not found.")


class ConflictError(ApiError):
    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(409, code, message, extra=extra)


class UnprocessableError(ApiError):
    def __init__(self, code: str, message: str, **extra: object) -> None:
        super().__init__(422, code, message, extra=extra)


class InvalidCredentialsError(ApiError):
    """Identical for unknown email, wrong password, and inactive account (AC-A2)."""

    def __init__(self) -> None:
        super().__init__(401, "invalid_credentials", "Invalid email or password.")


class LoginLockedError(ApiError):
    def __init__(self, retry_after_seconds: int) -> None:
        minutes = max(1, -(-retry_after_seconds // 60))
        super().__init__(
            429,
            "login_locked",
            f"Too many failed sign-in attempts. Try again in {minutes} minute(s).",
            headers={"Retry-After": str(retry_after_seconds)},
            extra={"retry_after_seconds": retry_after_seconds},
        )


class ServiceUnavailableError(ApiError):
    def __init__(self, code: str = "service_unavailable", message: str | None = None) -> None:
        super().__init__(
            503,
            code,
            message or "The application database is unavailable. Try again later.",
        )
