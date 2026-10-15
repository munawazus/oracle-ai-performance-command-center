class AppError(Exception):
    """Base for expected, handled application errors. Message is safe to show to the client."""

    status_code = 400

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class ValidationAppError(AppError):
    status_code = 422


class AuthenticationError(AppError):
    status_code = 401


class AccountLockedError(AuthenticationError):
    pass


class AuthorizationError(AppError):
    status_code = 403


class UpstreamUnavailableError(AppError):
    """The Oracle target couldn't be reached/authenticated - not a bug in this app."""

    status_code = 502


class RateLimitedError(AppError):
    status_code = 429

    def __init__(self, message: str, *, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
