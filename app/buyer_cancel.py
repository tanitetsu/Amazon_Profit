"""Row status helpers: lock cancel checkbox for × / 返品."""

from __future__ import annotations

from typing import Any

from app.schema import COL, DATA_START_ROW, NUM_COLS
from app.sheets_retry import batch_update, execute_with_retry

BUYER_CANCEL_PROTECT_PREFIX = "apv:buyer-cancel:"
STATUS_LOCK_PROTECT_PREFIX = "apv:status-lock:"


def buyer_cancel_protect_description(row_1based: int) -> str:
    # Keep old prefix name for migrate compatibility; locks キャンセル col R
    return f"{BUYER_CANCEL_PROTECT_PREFIX}R{row_1based}"


def is_buyer_cancel_protection(description: str | None) -> bool:
    d = description or ""
    return d.startswith(BUYER_CANCEL_PROTECT_PREFIX) or d.startswith(
        STATUS_LOCK_PROTECT_PREFIX
    )


def clear_conditional_format_requests(sheet_id: int, rule_count: int) -> list[dict[str, Any]]:
    if rule_count <= 0:
        return []
    return [
        {"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}}
        for i in range(rule_count - 1, -1, -1)
    ]


def checkbox_data_validation_requests(
    sheet_id: int, row_start_1based: int, row_end_1based: int
) -> list[dict[str, Any]]:
    from app.schema import CHECKBOX_COLS

    if row_end_1based < row_start_1based:
        return []
    reqs: list[dict[str, Any]] = []
    for col_1based in CHECKBOX_COLS:
        c0 = col_1based - 1
        reqs.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_start_1based - 1,
                        "endRowIndex": row_end_1based,
                        "startColumnIndex": c0,
                        "endColumnIndex": c0 + 1,
                    },
                    "rule": {
                        "condition": {"type": "BOOLEAN"},
                        "showCustomUi": True,
                        "strict": True,
                    },
                }
            }
        )
    return reqs


def clear_checkbox_validation_requests(
    sheet_id: int, row_start_1based: int, row_end_1based: int
) -> list[dict[str, Any]]:
    """Remove BOOLEAN☑ UI from checkbox columns (template empty rows)."""
    from app.schema import CHECKBOX_COLS

    if row_end_1based < row_start_1based:
        return []
    reqs: list[dict[str, Any]] = []
    for col_1based in CHECKBOX_COLS:
        c0 = col_1based - 1
        reqs.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_start_1based - 1,
                        "endRowIndex": row_end_1based,
                        "startColumnIndex": c0,
                        "endColumnIndex": c0 + 1,
                    },
                    "rule": None,
                }
            }
        )
    return reqs


def lock_cancel_checkbox(
    sheets_api,
    spreadsheet_id: str,
    sheet_id: int,
    sheet_title: str,
    rows: list[int],
    *,
    operator_email: str | None = None,
) -> None:
    """Lock キャンセル (R) on given 1-based rows (buyer-cancel / return)."""
    if not rows:
        return
    from app.sheet_protection import protection_editor_emails

    editors = protection_editor_emails(operator_email=operator_email)
    editors_body = {"users": editors}

    cancel_col0 = COL["cancel"] - 1
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties,protectedRanges)"
        ),
        label="status_lock.getProtect",
    )
    existing_by_desc: dict[str, dict[str, Any]] = {}
    for s in meta.get("sheets", []):
        for pr in s.get("protectedRanges", []) or []:
            d = pr.get("description") or ""
            if d:
                existing_by_desc[d] = pr

    reqs: list[dict[str, Any]] = []
    for row_1based in rows:
        desc = buyer_cancel_protect_description(row_1based)
        existing = existing_by_desc.get(desc)
        if existing:
            got = {
                (e or "").strip().lower()
                for e in ((existing.get("editors") or {}).get("users") or [])
                if (e or "").strip()
            }
            if got != {e.lower() for e in editors}:
                reqs.append(
                    {
                        "updateProtectedRange": {
                            "protectedRange": {
                                "protectedRangeId": existing["protectedRangeId"],
                                "warningOnly": False,
                                "editors": editors_body,
                            },
                            "fields": "warningOnly,editors",
                        }
                    }
                )
            continue
        reqs.append(
            {
                "addProtectedRange": {
                    "protectedRange": {
                        "description": desc,
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_1based - 1,
                            "endRowIndex": row_1based,
                            "startColumnIndex": cancel_col0,
                            "endColumnIndex": cancel_col0 + 1,
                        },
                        "warningOnly": False,
                        "editors": editors_body,
                    }
                }
            }
        )
    if reqs:
        batch_update(
            sheets_api,
            spreadsheet_id,
            reqs,
            chunk_size=20,
            label=f"status_lock.{sheet_title}.n{len(rows)}",
        )


# Back-compat aliases used by older scripts
def apply_buyer_cancel(
    sheets_api,
    spreadsheet_id: str,
    sheet_id: int,
    sheet_title: str,
    row_1based: int,
    *,
    operator_email: str | None = None,
) -> None:
    apply_buyer_cancels_many(
        sheets_api,
        spreadsheet_id,
        sheet_id,
        sheet_title,
        [row_1based],
        operator_email=operator_email,
    )


def apply_buyer_cancels_many(
    sheets_api,
    spreadsheet_id: str,
    sheet_id: int,
    sheet_title: str,
    rows: list[int],
    *,
    operator_email: str | None = None,
) -> None:
    """Legacy entry: lock cancel cells (status/value writes done by caller)."""
    lock_cancel_checkbox(
        sheets_api,
        spreadsheet_id,
        sheet_id,
        sheet_title,
        rows,
        operator_email=operator_email,
    )


# Old CF helper removed from style path; keep stub for migrate scripts
def cancel_row_conditional_format_requests(sheet_id: int) -> list[dict[str, Any]]:
    return []
