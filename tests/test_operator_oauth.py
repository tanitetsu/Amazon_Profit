"""Operator OAuth store/refresh errors must not look like a missing GCS URI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from app.google_clients import (
    _require_desktop_operator_client,
    load_operator_oauth_credentials,
    load_stored_operator_oauth_credentials,
    probe_operator_oauth,
)


def _expired_creds(*, refresh_token: str | None = "rt", scopes: list[str] | None = None) -> MagicMock:
    creds = MagicMock()
    creds.valid = False
    creds.expired = True
    creds.refresh_token = refresh_token
    creds.scopes = scopes or [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/script.projects",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    creds.has_scopes.return_value = True
    creds.expiry = None
    return creds


@patch("app.google_clients.Credentials.from_authorized_user_info")
@patch("app.google_clients._load_operator_token_text", return_value='{"token":"x"}')
def test_revoked_refresh_token_mentions_invalid_grant(
    _load_text: MagicMock, from_info: MagicMock
) -> None:
    creds = _expired_creds()
    creds.refresh.side_effect = RefreshError(
        "invalid_grant: Token has been expired or revoked."
    )
    from_info.return_value = creds

    with pytest.raises(RuntimeError) as caught:
        load_stored_operator_oauth_credentials()

    msg = str(caught.value)
    assert "invalid_grant" in msg
    assert "unusable" in msg
    assert "set OPERATOR_TOKEN_GCS_URI" not in msg


@patch("app.google_clients.Credentials.from_authorized_user_info")
@patch("app.google_clients._load_operator_token_text", return_value='{"token":"x"}')
def test_valid_token_with_missing_scopes(_load_text: MagicMock, from_info: MagicMock) -> None:
    creds = MagicMock()
    creds.valid = True
    creds.expired = False
    creds.refresh_token = "rt"
    creds.scopes = ["https://www.googleapis.com/auth/drive"]
    creds.has_scopes.return_value = False
    from_info.return_value = creds

    with pytest.raises(RuntimeError) as caught:
        load_stored_operator_oauth_credentials()

    msg = str(caught.value)
    assert "missing scopes" in msg
    assert "gmail.send" in msg


@patch("app.google_clients._load_operator_token_text", return_value=None)
def test_missing_token_file(_load_text: MagicMock) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        load_stored_operator_oauth_credentials()
    assert "operator_token.json" in str(caught.value)


@patch("app.google_clients.load_stored_operator_oauth_credentials")
def test_probe_ok_and_error(stored: MagicMock) -> None:
    creds = MagicMock()
    creds.expiry = None
    stored.return_value = creds
    assert probe_operator_oauth() == {"ok": True, "expiry": None}

    stored.side_effect = RuntimeError("operator user OAuth token is unusable: refresh failed")
    probed = probe_operator_oauth()
    assert probed["ok"] is False
    assert "unusable" in probed["error"]


@patch("app.google_clients.InstalledAppFlow")
@patch("app.google_clients.uses_adc_credentials", return_value=True)
@patch("app.google_clients.load_stored_operator_oauth_credentials")
def test_cloud_run_does_not_open_browser_when_token_revoked(
    stored: MagicMock, _adc: MagicMock, flow: MagicMock
) -> None:
    stored.side_effect = RuntimeError(
        "operator user OAuth token is unusable: refresh failed (invalid_grant)"
    )
    with pytest.raises(RuntimeError) as caught:
        load_operator_oauth_credentials()
    assert "invalid_grant" in str(caught.value)
    flow.from_client_secrets_file.assert_not_called()


def test_web_oauth_client_is_rejected_for_operator_login(tmp_path) -> None:
    path = tmp_path / "oauth_client.json"
    path.write_text('{"web": {"client_id": "example.apps.googleusercontent.com"}}', encoding="utf-8")
    with pytest.raises(FileNotFoundError) as caught:
        _require_desktop_operator_client(path)
    assert "redirect_uri_mismatch" in str(caught.value)
    assert "oauth_client_desktop.json" in str(caught.value)


def test_desktop_oauth_client_is_accepted(tmp_path) -> None:
    path = tmp_path / "oauth_client_desktop.json"
    path.write_text(
        '{"installed": {"client_id": "example.apps.googleusercontent.com"}}',
        encoding="utf-8",
    )
    _require_desktop_operator_client(path)
