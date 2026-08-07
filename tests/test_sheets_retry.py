"""Tests for sheets_retry.call_with_retry / is_transient."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.sheets_retry import call_with_retry, is_auth_fatal, is_transient


class TransientClassificationTests(unittest.TestCase):
    def test_timeout_is_transient(self) -> None:
        self.assertTrue(is_transient(TimeoutError("timed out")))

    def test_auth_fatal_not_transient(self) -> None:
        exc = RuntimeError("invalid_grant: Token has been expired or revoked")
        self.assertTrue(is_auth_fatal(exc))
        self.assertFalse(is_transient(exc))


class CallWithRetryTests(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("connection reset")
            return "ok"

        with patch("app.sheets_retry.time.sleep"):
            self.assertEqual(call_with_retry(flaky, max_attempts=5, label="t"), "ok")
        self.assertEqual(calls["n"], 3)

    def test_non_transient_raises_immediately(self) -> None:
        calls = {"n": 0}

        def hard():
            calls["n"] += 1
            raise ValueError("bad input")

        with self.assertRaises(ValueError):
            call_with_retry(hard, max_attempts=5, label="t")
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
