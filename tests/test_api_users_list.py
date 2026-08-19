"""GET /api/users must not depend on operator gmail.send OAuth for listing."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from wsgi import app as flask_app


def test_list_user_workbooks_uses_runtime_credentials_on_adc() -> None:
    drive = MagicMock()
    with (
        patch("app.provision.uses_adc_credentials", return_value=True),
        patch(
            "app.provision.load_users_config",
            return_value={"folder_name": "User_Acounting"},
        ),
        patch("app.provision.load_operator_credentials", return_value=MagicMock()) as load_creds,
        patch("app.provision.drive_service", return_value=drive),
        patch("app.provision.find_folder_id", return_value=None),
        patch("app.clipping_roster.list_active_users", return_value=[]),
    ):
        from app.provision import list_user_workbooks

        rows = list_user_workbooks()

    assert rows == []
    load_creds.assert_called_once()
    from app import provision as provision_mod

    src = inspect.getsource(provision_mod.list_user_workbooks)
    assert "load_operator_oauth_credentials" not in src


def test_api_users_returns_gcs_roster_when_drive_fails() -> None:
    client = flask_app.test_client()
    api_users = flask_app.view_functions["api_users"]
    with (
        patch.dict(
            api_users.__globals__,
            {
                "list_user_workbooks": MagicMock(
                    side_effect=RuntimeError("operator OAuth missing required scopes")
                )
            },
        ),
        patch(
            "app.clipping_roster.load_role_map",
            return_value={"alice": "Normal", "bob": "Admin"},
        ),
    ):
        res = client.get("/api/users")

    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["users"] == []
    assert "operator OAuth" in (data.get("users_error") or "")
    assert {row["user_id"]: row["role"] for row in data["roster"]} == {
        "alice": "Normal",
        "bob": "Admin",
    }
    assert "roster_error" not in data
