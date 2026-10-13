"""Bounded retries for transient pipeline failures."""

import time

import httpx


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.ConnectError,
)


class RetryFailure(Exception):
    """A transient failure persisted until retries were exhausted."""

    def __init__(self, error, attempts):
        self.original_error = error
        self.attempts = attempts
        super().__init__(
            f"{type(error).__name__} after {attempts} "
            f"attempt(s): {error}"
        )


def is_retryable_error(error):
    """Identify temporary network and API failures."""
    if isinstance(error, RETRYABLE_EXCEPTIONS):
        return True

    status = getattr(error, "code", None)

    if status is None:
        status = getattr(error, "status_code", None)

    try:
        return int(status) in RETRYABLE_STATUS_CODES
    except (TypeError, ValueError):
        return False


def retry_call(operation, *, max_attempts=3, delay_seconds=0.0):
    """
    Execute operation and return (result, attempts_used).

    Retry transient failures only. Preserve original exception types
    for non-retryable errors.
    """
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer.")

    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative.")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation(), attempt

        except Exception as error:
            if not is_retryable_error(error):
                raise

            if attempt == max_attempts:
                raise RetryFailure(error, attempt) from error

            if delay_seconds:
                time.sleep(delay_seconds * (2 ** (attempt - 1)))
