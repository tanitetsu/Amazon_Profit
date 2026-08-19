"""Keep Google OAuth tokens warm (access refresh + refresh_token activity)."""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any

from app.clipping_roster import list_active_users
from app.gmail_oauth import list_linked_gmails, maintain_gmail_token


def maintain_all_gmail_tokens() -> dict[str, Any]:
    """
    For every linked roster user: force-refresh access token and ping Gmail profile.

    Google refresh tokens do not expire on a short clock, but unused ones can be
    invalidated (e.g. after months idle, password change, user revoke). Running
    this inside the 5-minute poll keeps them in active use.
    """
    candidates = [u["gmail"] for u in list_active_users() if u.get("gmail")]
    linked = list_linked_gmails(candidates)
    results: list[dict[str, Any]] = []
    errors = 0
    for gmail in linked:
        try:
            results.append({"ok": True, **maintain_gmail_token(gmail)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            errors += 1
            results.append({"ok": False, "gmail": gmail, "error": str(exc)})
    return {
        "ok": errors == 0,
        "at": datetime.now().isoformat(timespec="seconds"),
        "linked_users": len(linked),
        "errors": errors,
        "results": results,
    }


def maintain_operator_oauth_token() -> dict[str, Any]:
    """
    Keep the operator user refresh token in active use.

    Poll every 5 minutes: force-refresh the access token, persist it, then ping
    Drive so the grant is not idle. This prevents Google's 6-month unused-token
    invalidation. It cannot override the 7-day refresh expiry that applies only
    when the OAuth consent screen publishing status is Testing.
    Never starts a browser (Cloud Run / Scheduler).
    """
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    from app.google_clients import (
        _persist_operator_oauth,
        load_stored_operator_oauth_credentials,
    )
    from app.sheets_retry import call_with_retry, execute_with_retry

    try:
        creds = load_stored_operator_oauth_credentials()
        if not creds.refresh_token:
            raise RuntimeError("operator OAuth has no refresh_token")

        def _refresh() -> None:
            creds.refresh(Request())
            _persist_operator_oauth(creds)

        call_with_retry(_refresh, label="operator.oauth.maintain")

        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = execute_with_retry(
            drive.about().get(fields="user(emailAddress)"),
            label="operator.drive.about",
        )
        email = ((about.get("user") or {}).get("emailAddress") or "").strip().lower()
        return {
            "ok": True,
            "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
            "emailAddress": email or None,
            "refreshed": True,
        }
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}
