"""Google API / network helpers with rate-limit backoff (no silent drop)."""

from __future__ import annotations

import random
import time
from typing import Any, Callable, TypeVar

try:
    from googleapiclient.errors import HttpError
except ImportError:  # pragma: no cover
    HttpError = Exception  # type: ignore[misc, assignment]

T = TypeVar("T")

_TRANSIENT_HTTP = frozenset({408, 429, 500, 502, 503, 504})


def http_status(exc: BaseException) -> int | None:
    if isinstance(exc, HttpError):
        resp = getattr(exc, "resp", None)
        if resp is not None:
            try:
                return int(resp.status)
            except (TypeError, ValueError):
                return None
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status is not None:
        try:
            return int(status)
        except (TypeError, ValueError):
            return None
    return None


def is_rate_limited(exc: BaseException) -> bool:
    if http_status(exc) == 429:
        return True
    msg = str(exc).lower()
    return (
        "quota exceeded" in msg
        or "rate limit" in msg
        or "ratelimit" in msg
        or "write requests per minute" in msg
        or "resource_exhausted" in msg
    )


def is_auth_fatal(exc: BaseException) -> bool:
    """OAuth / token errors that must not be retried."""
    msg = str(exc).lower()
    return (
        "invalid_grant" in msg
        or "token has been expired or revoked" in msg
        or "token has been revoked" in msg
        or "invalid_rapt" in msg
        or "unauthorized_client" in msg
    )


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, Exception) and type(exc).__name__ in (
        "WorkbookExistsError",
        "ValueError",
        "FileNotFoundError",
    ):
        # Structural / caller errors — never retry.
        if type(exc).__name__ == "WorkbookExistsError":
            return False
    # ProvisionError wraps the underlying failure after rollback.
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if type(exc).__name__ == "ProvisionError" and cause is not None and cause is not exc:
        return is_transient(cause)

    if is_auth_fatal(exc):
        return False
    status = http_status(exc)
    if status in _TRANSIENT_HTTP:
        return True
    if is_rate_limited(exc):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError, OSError)):
        return True
    name = type(exc).__name__
    if name in (
        "TransportError",
        "ServiceUnavailable",
        "InternalServerError",
        "TooManyRequests",
        "DeadlineExceeded",
        "GatewayTimeout",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectError",
        "RemoteProtocolError",
        "NetworkError",
    ):
        return True
    msg = str(exc).lower()
    return (
        "timed out" in msg
        or "timeout" in msg
        or "connection reset" in msg
        or "connection aborted" in msg
        or "temporarily unavailable" in msg
        or "backend error" in msg
        or "unavailable" in msg
        or "503" in msg
        or "502" in msg
        or "500" in msg
        or "504" in msg
    )


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 10,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
    label: str = "api",
) -> T:
    """
    Call ``fn`` with exponential backoff on transient errors (429 / 5xx / timeout).
    Exhausted retries re-raise (do not skip).
    """
    last: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — API clients raise varied types
            last = exc
            if not is_transient(exc) or attempt >= max_attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay *= 0.75 + random.random() * 0.5
            kind = "rate limited" if is_rate_limited(exc) else "transient"
            print(
                f"{label}: {kind} "
                f"(attempt {attempt}/{max_attempts}), sleep {delay:.1f}s - {type(exc).__name__}"
            )
            time.sleep(delay)
    assert last is not None
    raise last


def execute_with_retry(
    request: Any,
    *,
    max_attempts: int = 10,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
    label: str = "sheets",
) -> Any:
    """
    Execute a googleapiclient request. On 429 / write quota / timeout, sleep with
    exponential backoff + jitter and retry. Exhausted retries raise (do not skip).
    """
    return call_with_retry(
        lambda: request.execute(num_retries=0),
        max_attempts=max_attempts,
        base_delay=base_delay,
        max_delay=max_delay,
        label=label,
    )


def _soft_filter(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop ops that often 400 when nothing to change (safe to omit once)."""
    return [
        r
        for r in requests
        if "unmergeCells" not in r and "addBanding" not in r
    ]


def batch_update(
    sheets_api,
    spreadsheet_id: str,
    requests: list[dict[str, Any]],
    *,
    chunk_size: int = 15,
    pace_seconds: float = 1.05,
    label: str = "batchUpdate",
) -> None:
    """Send batchUpdate in chunks; retry 429; never silently drop hard failures."""
    if not requests:
        return
    for i in range(0, len(requests), chunk_size):
        chunk = requests[i : i + chunk_size]
        body = {"requests": chunk}
        try:
            execute_with_retry(
                sheets_api.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id, body=body
                ),
                label=f"{label}[{i}]",
            )
        except Exception as exc:
            if is_rate_limited(exc) or is_transient(exc):
                raise
            filtered = _soft_filter(chunk)
            if not filtered:
                # Chunk was only soft ops that failed — skip this chunk only
                print(f"{label}[{i}]: skip soft-only chunk ({type(exc).__name__})")
            elif len(filtered) == len(chunk):
                raise
            else:
                execute_with_retry(
                    sheets_api.spreadsheets().batchUpdate(
                        spreadsheetId=spreadsheet_id,
                        body={"requests": filtered},
                    ),
                    label=f"{label}[{i}].soft",
                )
        if pace_seconds > 0 and i + chunk_size < len(requests):
            time.sleep(pace_seconds)


def values_batch_update(
    sheets_api,
    spreadsheet_id: str,
    data: list[dict[str, Any]],
    *,
    chunk_size: int = 20,
    pace_seconds: float = 0.9,
    value_input_option: str = "USER_ENTERED",
    label: str = "values.batchUpdate",
) -> None:
    if not data:
        return
    for i in range(0, len(data), chunk_size):
        part = data[i : i + chunk_size]
        execute_with_retry(
            sheets_api.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": value_input_option, "data": part},
            ),
            label=f"{label}[{i}]",
        )
        if pace_seconds > 0 and i + chunk_size < len(data):
            time.sleep(pace_seconds)


def values_clear(sheets_api, spreadsheet_id: str, range_name: str) -> None:
    execute_with_retry(
        sheets_api.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=range_name
        ),
        label=f"values.clear:{range_name}",
    )
