"""Operator OAuth keep-alive (mail poll)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.token_maintain import maintain_operator_oauth_token


@patch("googleapiclient.discovery.build")
@patch("app.sheets_retry.execute_with_retry")
@patch("app.sheets_retry.call_with_retry")
@patch("app.google_clients._persist_operator_oauth")
@patch("app.google_clients.load_stored_operator_oauth_credentials")
@patch("google.auth.transport.requests.Request")
def test_operator_keep_alive_refreshes_and_pings_drive(
    _req: MagicMock,
    load_stored: MagicMock,
    persist: MagicMock,
    call_retry: MagicMock,
    execute: MagicMock,
    build: MagicMock,
) -> None:
    creds = MagicMock()
    creds.refresh_token = "rt"
    creds.expiry = None
    load_stored.return_value = creds
    execute.return_value = {"user": {"emailAddress": "26964u@gmail.com"}}

    def _run(fn, **_kwargs):
        fn()
        return None

    call_retry.side_effect = _run

    out = maintain_operator_oauth_token()

    assert out["ok"] is True
    assert out["refreshed"] is True
    assert out["emailAddress"] == "26964u@gmail.com"
    creds.refresh.assert_called_once()
    persist.assert_called_once_with(creds)
    build.assert_called_once()


@patch("app.google_clients.load_stored_operator_oauth_credentials")
def test_operator_keep_alive_returns_error_without_browser(load_stored: MagicMock) -> None:
    load_stored.side_effect = RuntimeError(
        "operator user OAuth token is unusable: refresh failed (invalid_grant)"
    )
    out = maintain_operator_oauth_token()
    assert out["ok"] is False
    assert "invalid_grant" in (out.get("error") or "")
