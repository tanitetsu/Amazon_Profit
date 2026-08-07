"""Protect locked columns; leave Book-3 editable columns open."""

from __future__ import annotations

import os
import re
from typing import Any

from app.schema import (
    DATA_START_ROW,
    DETAIL_FIELDS,
    DETAIL_SPANS,
    FORMULA_END_ROW,
    NUM_COLS,
    SUMMARY_SHEET,
)
from app.sheets_retry import batch_update, execute_with_retry

# Legacy live books still use "Overview"; template uses SUMMARY_SHEET.
_SUMMARY_TITLES = frozenset({SUMMARY_SHEET, "Overview"})
_MONTH_TITLE_RE = re.compile(r"^\d{4}-\d{2}$")


def protection_editor_emails(*, operator_email: str | None = None) -> list[str]:
    """
    Who may edit protected cells (owner can always edit).

    Shared end-users must NOT be listed here — Sheets otherwise treats them
    as editors of the protected range and the lock is ineffective.
    """
    from app.users_store import load_users_config

    cfg = load_users_config()
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        email = (raw or "").strip()
        if not email:
            return
        key = email.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(email)

    _add(operator_email or (cfg.get("operator_drive_email") or ""))
    for item in cfg.get("protection_editor_emails") or []:
        _add(str(item))
    env_extra = (os.environ.get("PROTECTION_EDITOR_EMAILS") or "").strip()
    if env_extra:
        for part in env_extra.split(","):
            _add(part)
    if not out:
        raise ValueError(
            "protection editors empty: set operator_drive_email "
            "and/or protection_editor_emails"
        )
    return out


def share_editor(
    drive,
    file_id: str,
    email: str,
    *,
    send_notification: bool = True,
    email_message: str | None = None,
) -> dict[str, Any]:
    """Grant writer. Returns {action, notified} for diagnostics."""
    existing = (
        execute_with_retry(
            drive.permissions().list(
                fileId=file_id, fields="permissions(id,emailAddress,role,type)"
            ),
            label="drive.permissions.list",
        ).get("permissions", [])
    )
    for p in existing:
        if (p.get("emailAddress") or "").lower() == email.lower():
            if p.get("role") in ("writer", "owner"):
                return {"action": "already_writer", "notified": False}
            execute_with_retry(
                drive.permissions().update(
                    fileId=file_id, permissionId=p["id"], body={"role": "writer"}
                ),
                label="drive.permissions.update",
            )
            return {"action": "upgraded", "notified": False}
    body: dict[str, Any] = {
        "type": "user",
        "role": "writer",
        "emailAddress": email,
    }
    create_kwargs: dict[str, Any] = {
        "fileId": file_id,
        "body": body,
        "sendNotificationEmail": bool(send_notification),
        "fields": "id",
    }
    if send_notification and email_message:
        create_kwargs["emailMessage"] = email_message
    execute_with_retry(
        drive.permissions().create(**create_kwargs),
        label="drive.permissions.create",
    )
    return {"action": "created", "notified": bool(send_notification)}


def unshare_user(drive, file_id: str, email: str) -> bool:
    key = email.strip().lower()
    existing = (
        execute_with_retry(
            drive.permissions().list(
                fileId=file_id, fields="permissions(id,emailAddress,role,type)"
            ),
            label="drive.permissions.list",
        ).get("permissions", [])
    )
    removed = False
    for p in existing:
        if (p.get("emailAddress") or "").lower() != key:
            continue
        if p.get("role") == "owner":
            continue
        execute_with_retry(
            drive.permissions().delete(fileId=file_id, permissionId=p["id"]),
            label="drive.permissions.delete",
        )
        removed = True
    return removed


def _editors_match(pr: dict[str, Any], want: list[str]) -> bool:
    got = {
        (e or "").strip().lower()
        for e in ((pr.get("editors") or {}).get("users") or [])
        if (e or "").strip()
    }
    return got == {e.lower() for e in want}


def clear_apv_protections(sheets_api, spreadsheet_id: str) -> None:
    """Remove all apv:* protected ranges (Admin workbooks: no range locks)."""
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties,protectedRanges)"
        ),
        label="protect.clear.get",
    )
    delete_reqs: list[dict[str, Any]] = []
    for s in meta.get("sheets", []):
        title = s["properties"]["title"]
        for pr in s.get("protectedRanges", []) or []:
            desc = pr.get("description") or ""
            rng = pr.get("range") or {}
            sheet_wide = (
                rng.get("startRowIndex") is None
                and rng.get("endRowIndex") is None
                and rng.get("startColumnIndex") is None
                and rng.get("endColumnIndex") is None
            )
            if title in _SUMMARY_TITLES and sheet_wide:
                delete_reqs.append(
                    {"deleteProtectedRange": {"protectedRangeId": pr["protectedRangeId"]}}
                )
                continue
            if desc.startswith("apv:"):
                delete_reqs.append(
                    {"deleteProtectedRange": {"protectedRangeId": pr["protectedRangeId"]}}
                )
    if delete_reqs:
        batch_update(
            sheets_api,
            spreadsheet_id,
            delete_reqs,
            chunk_size=40,
            label="protect.clear",
        )


def apply_protections(
    sheets_api,
    spreadsheet_id: str,
    data_row_counts: dict[str, int] | None = None,
    *,
    role: str | None = None,
    skip_if_present: bool = False,
) -> None:
    """
    Lock Overview entirely.
    Month: lock top + auto columns; leave editable fields open.
    Status is unprotected (Apps Script onEdit and auto-fill write values).
    Preserves per-cell cancel locks (apv:buyer-cancel:…) but refreshes their editors.

    role=Admin → strip all apv protections (no range locks).
    role=Exclusive/Normal/None → apply current locks.

    skip_if_present: when True (keep/re-provision), skip the full delete+re-add
    cycle if apv protections already exist — those dominate Sheets latency.
    """
    from app.ai_roles import ROLE_ADMIN, normalize_app_role

    app_role = normalize_app_role(role) if role is not None else None
    if app_role == ROLE_ADMIN:
        clear_apv_protections(sheets_api, spreadsheet_id)
        return

    from app.buyer_cancel import is_buyer_cancel_protection

    editors = protection_editor_emails()
    editors_body = {"users": editors}

    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties,protectedRanges)"
        ),
        label="protect.get",
    )

    if skip_if_present:
        has_apv = False
        for s in meta.get("sheets", []):
            for pr in s.get("protectedRanges", []) or []:
                desc = pr.get("description") or ""
                if desc.startswith("apv:"):
                    has_apv = True
                    break
            if has_apv:
                break
        if has_apv:
            return

    delete_reqs: list[dict[str, Any]] = []
    update_reqs: list[dict[str, Any]] = []
    for s in meta.get("sheets", []):
        title = s["properties"]["title"]
        for pr in s.get("protectedRanges", []) or []:
            desc = pr.get("description") or ""
            rng = pr.get("range") or {}
            sheet_wide = (
                rng.get("startRowIndex") is None
                and rng.get("endRowIndex") is None
                and rng.get("startColumnIndex") is None
                and rng.get("endColumnIndex") is None
            )
            # Reclaim summary sheet lock even if applied without apv: prefix.
            if title in _SUMMARY_TITLES and sheet_wide:
                delete_reqs.append(
                    {"deleteProtectedRange": {"protectedRangeId": pr["protectedRangeId"]}}
                )
                continue
            if is_buyer_cancel_protection(desc):
                if not _editors_match(pr, editors):
                    update_reqs.append(
                        {
                            "updateProtectedRange": {
                                "protectedRange": {
                                    "protectedRangeId": pr["protectedRangeId"],
                                    "warningOnly": False,
                                    "editors": editors_body,
                                },
                                "fields": "warningOnly,editors",
                            }
                        }
                    )
                continue
            if not desc.startswith("apv:"):
                continue
            delete_reqs.append(
                {"deleteProtectedRange": {"protectedRangeId": pr["protectedRangeId"]}}
            )
    if delete_reqs:
        batch_update(
            sheets_api, spreadsheet_id, delete_reqs, chunk_size=40, label="protect.delete"
        )
    if update_reqs:
        batch_update(
            sheets_api, spreadsheet_id, update_reqs, chunk_size=40, label="protect.editors"
        )
        # Refresh meta after buyer-cancel editor updates only when we also deleted —
        # add path needs current sheet list either way.
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties,protectedRanges)"
        ),
        label="protect.get2",
    )

    # Locked detail fields (spans), excluding status + editable
    locked_spans: list[tuple[int, int]] = []
    for f in DETAIL_FIELDS:
        if f.key == "status" or f.editable:
            continue
        locked_spans.append(DETAIL_SPANS[f.key])

    requests: list[dict[str, Any]] = []
    for s in meta.get("sheets", []):
        title = s["properties"]["title"]
        sheet_id = s["properties"]["sheetId"]
        if title in _SUMMARY_TITLES:
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "description": "apv:summary",
                            "range": {"sheetId": sheet_id},
                            "warningOnly": False,
                            "editors": editors_body,
                        }
                    }
                }
            )
            continue

        if not _MONTH_TITLE_RE.match(title):
            continue

        n = (data_row_counts or {}).get(title, FORMULA_END_ROW - DATA_START_ROW + 1)
        requests.append(
            {
                "addProtectedRange": {
                    "protectedRange": {
                        "description": "apv:month-top",
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": DATA_START_ROW - 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": NUM_COLS,
                        },
                        "warningOnly": False,
                        "editors": editors_body,
                    }
                }
            }
        )
        if n <= 0:
            continue
        data_end = DATA_START_ROW - 1 + n
        for i, (c0, c1) in enumerate(locked_spans):
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "description": f"apv:month-lock:{i}",
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": DATA_START_ROW - 1,
                                "endRowIndex": data_end,
                                "startColumnIndex": c0,
                                "endColumnIndex": c1,
                            },
                            "warningOnly": False,
                            "editors": editors_body,
                        }
                    }
                }
            )

    if requests:
        batch_update(
            sheets_api, spreadsheet_id, requests, chunk_size=20, label="protect.add"
        )
