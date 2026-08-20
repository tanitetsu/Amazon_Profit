"""Row status helpers (cancel checkbox is not range-locked)."""

from __future__ import annotations

from typing import Any

BUYER_CANCEL_PROTECT_PREFIX = "apv:buyer-cancel:"
STATUS_LOCK_PROTECT_PREFIX = "apv:status-lock:"


def buyer_cancel_protect_description(row_1based: int) -> str:
    # Keep old prefix name for migrate compatibility (historical cancel-col lock).
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
    """No-op: cancel cells stay user-editable after auto-fill (× / 返品 included)."""
    del sheets_api, spreadsheet_id, sheet_id, sheet_title, rows, operator_email


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
    """Legacy entry: no-op (status/value writes done by caller)."""
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
