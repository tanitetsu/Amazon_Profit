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
    """Refresh operator user OAuth (Drive/Sheets/gmail.send) access token."""
    from google.auth.transport.requests import Request

    from app.google_clients import load_operator_oauth_credentials, _persist_operator_oauth
    from app.sheets_retry import call_with_retry

    try:
        creds = load_operator_oauth_credentials()
        if creds.refresh_token:
            def _refresh() -> None:
                creds.refresh(Request())
                _persist_operator_oauth(creds)

            call_with_retry(_refresh, label="operator.oauth.maintain")
        return {
            "ok": True,
            "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
        }
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}
