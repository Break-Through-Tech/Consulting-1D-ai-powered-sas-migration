"""Tests for bounded retries and transient pipeline failures."""

import unittest
from unittest.mock import call, patch

import httpx
from google.genai import errors

from src.pipeline_retry import (
    RetryFailure,
    retry_call,
)


class FlakyOperation:
    """Fail a specified number of times, then succeed."""

    def __init__(self, *failures):
        self.failures = list(failures)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


def api_error(code):
    details = {"error": {"message": "test API error"}}
    if code >= 500:
        return errors.ServerError(code, details)
    return errors.ClientError(code, details)


class RetryTests(unittest.TestCase):

    def test_success_without_retries(self):
        operation = FlakyOperation()

        result, attempts = retry_call(operation)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 1)
        self.assertEqual(operation.calls, 1)

    def test_transient_api_errors_are_retried(self):
        for code in (429, 503):
            with self.subTest(status_code=code):
                operation = FlakyOperation(api_error(code))

                result, attempts = retry_call(operation)

                self.assertEqual(result, "ok")
                self.assertEqual(attempts, 2)
                self.assertEqual(operation.calls, 2)

    def test_network_errors_are_retried(self):
        errors_to_test = (
            httpx.ReadTimeout("timeout"),
            httpx.ConnectError("connection failed"),
            TimeoutError("timeout"),
            ConnectionError("connection failed"),
        )

        for error in errors_to_test:
            with self.subTest(error=type(error).__name__):
                operation = FlakyOperation(error)

                result, attempts = retry_call(operation)

                self.assertEqual(result, "ok")
                self.assertEqual(attempts, 2)

    def test_retry_exhaustion(self):
        operation = FlakyOperation(
            api_error(503),
            api_error(503),
            api_error(503),
        )

        with self.assertRaises(RetryFailure) as caught:
            retry_call(operation, max_attempts=3)

        self.assertEqual(operation.calls, 3)
        self.assertEqual(caught.exception.attempts, 3)
        self.assertIsInstance(
            caught.exception.original_error,
            errors.ServerError,
        )

    def test_non_retryable_errors_keep_original_type(self):
        errors_to_test = (
            api_error(400),
            ValueError("invalid input"),
            TypeError("invalid output"),
            FileNotFoundError("missing file"),
        )

        for error in errors_to_test:
            with self.subTest(error=type(error).__name__):
                operation = FlakyOperation(error)

                with self.assertRaises(type(error)):
                    retry_call(operation)

                self.assertEqual(operation.calls, 1)

    def test_invalid_retry_settings(self):
        for attempts in (0, -1, True, 2.5):
            with self.subTest(max_attempts=attempts):
                with self.assertRaises(ValueError):
                    retry_call(
                        FlakyOperation(),
                        max_attempts=attempts,
                    )

        with self.assertRaises(ValueError):
            retry_call(
                FlakyOperation(),
                delay_seconds=-1,
            )

    def test_exponential_backoff(self):
        operation = FlakyOperation(
            api_error(503),
            api_error(503),
        )

        with patch("src.pipeline_retry.time.sleep") as sleep:
            result, attempts = retry_call(
                operation,
                max_attempts=3,
                delay_seconds=2,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 3)
        self.assertEqual(
            sleep.call_args_list,
            [call(2), call(4)],
        )


if __name__ == "__main__":
    unittest.main()
