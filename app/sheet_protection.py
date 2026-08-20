"""Share workbooks as Editor. Range locks are not used — every cell is user-editable."""

from __future__ import annotations

import os
import re
from typing import Any

from app.schema import HINT_ROW, HINT_ROW_TEXT, MONTH_TEMPLATE_SHEET, SUMMARY_SHEET
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update

# Legacy live books still use "Overview"; template uses SUMMARY_SHEET.
_SUMMARY_TITLES = frozenset({SUMMARY_SHEET, "Overview"})
_MONTH_TITLE_RE = re.compile(r"^\d{4}-\d{2}$")


def protection_editor_emails(*, operator_email: str | None = None) -> list[str]:
    """
    Kept for config compatibility. Range protection is no longer applied, so
    this list is unused on the live path.
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


def _spreadsheet_meta(sheets_api, spreadsheet_id: str, *, label: str) -> dict[str, Any]:
    return execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties,protectedRanges)"
        ),
        label=label,
    )


def _protected_range_ids(meta: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for s in meta.get("sheets", []):
        for pr in s.get("protectedRanges", []) or []:
            pid = pr.get("protectedRangeId")
            if pid is None:
                continue
            ids.append(int(pid))
    return ids


def has_protected_ranges(sheets_api, spreadsheet_id: str) -> bool:
    meta = _spreadsheet_meta(sheets_api, spreadsheet_id, label="protect.has.get")
    return bool(_protected_range_ids(meta))


def clear_all_protections(sheets_api, spreadsheet_id: str) -> int:
    """Delete every protected range so Editor users can edit all cells."""
    meta = _spreadsheet_meta(sheets_api, spreadsheet_id, label="protect.clear.get")
    ids = _protected_range_ids(meta)
    if not ids:
        return 0
    delete_reqs = [
        {"deleteProtectedRange": {"protectedRangeId": pid}} for pid in ids
    ]
    batch_update(
        sheets_api,
        spreadsheet_id,
        delete_reqs,
        chunk_size=40,
        label="protect.clear",
    )
    return len(ids)


def clear_apv_protections(sheets_api, spreadsheet_id: str) -> None:
    """Back-compat alias: strip all range locks (not only apv:*)."""
    clear_all_protections(sheets_api, spreadsheet_id)


def refresh_edit_hints(sheets_api, spreadsheet_id: str) -> int:
    """Rewrite month / seed-sheet hint rows to match the no-lock policy."""
    meta = _spreadsheet_meta(sheets_api, spreadsheet_id, label="protect.hint.get")
    updates: list[dict[str, Any]] = []
    for s in meta.get("sheets", []):
        title = (s.get("properties") or {}).get("title") or ""
        if title in _SUMMARY_TITLES:
            continue
        if title != MONTH_TEMPLATE_SHEET and not _MONTH_TITLE_RE.match(title):
            continue
        safe = title.replace("'", "''")
        updates.append(
            {
                "range": f"'{safe}'!A{HINT_ROW}",
                "values": [[HINT_ROW_TEXT]],
            }
        )
    if updates:
        values_batch_update(
            sheets_api,
            spreadsheet_id,
            updates,
            label="protect.hint",
        )
    return len(updates)


def unlock_workbook(
    sheets_api,
    spreadsheet_id: str,
    *,
    update_hints: bool = True,
) -> dict[str, int]:
    """Remove range locks and optionally refresh the edit hint."""
    deleted = clear_all_protections(sheets_api, spreadsheet_id)
    hints = refresh_edit_hints(sheets_api, spreadsheet_id) if update_hints else 0
    return {"deleted_protections": deleted, "hints_updated": hints}


def apply_protections(
    sheets_api,
    spreadsheet_id: str,
    data_row_counts: dict[str, int] | None = None,
    *,
    role: str | None = None,
    skip_if_present: bool = False,
) -> None:
    """
    No range locks for any role. Auto-fill does not lock cells.

    skip_if_present: when True (keep/re-provision), skip if the book already
    has no protected ranges. Books that still have locks are always cleared.

    data_row_counts and role are accepted for call-site compatibility and ignored.
    """
    del data_row_counts, role
    if skip_if_present and not has_protected_ranges(sheets_api, spreadsheet_id):
        return
    unlock_workbook(sheets_api, spreadsheet_id)
