"""Migrate live workbooks to cancel-column layout.

Non-destructive: keeps month tabs and order rows. Inserts キャンセル column,
updates KPI/Overview/hint/CF. Range locks are stripped (all cells user-editable).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.buyer_cancel import (  # noqa: E402
    apply_buyer_cancels_many,
    cancel_row_conditional_format_requests,
    checkbox_data_validation_requests,
    clear_conditional_format_requests,
)
from app.google_clients import (  # noqa: E402
    load_operator_credentials,
    load_users_config,
    sheets_service,
)
from app.schema import (  # noqa: E402
    CHECKBOX_COL_WIDTH_PX,
    COL,
    DATA_START_ROW,
    FORMULA_END_ROW,
    HEADER_ROW,
    HINT_ROW_TEXT,
    NUM_COLS,
    ORDER_HEADERS,
    OVERVIEW_NUM_COLS,
    SUMMARY_SHEET,
)
from app.sheet_builder import month_kpi_formulas  # noqa: E402
from app.sheet_protection import apply_protections  # noqa: E402
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update  # noqa: E402


def _meta(sheets_api, spreadsheet_id: str) -> list[dict]:
    return execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields=(
                "sheets(properties(sheetId,title,index,gridProperties),"
                "conditionalFormats,protectedRanges)"
            ),
        ),
        label="migrate.meta",
    ).get("sheets", [])


def _month_tabs(sheets: list[dict]) -> list[tuple[str, int]]:
    out = []
    for s in sheets:
        title = s["properties"]["title"]
        if len(title) == 7 and title[4] == "-" and title[:4].isdigit():
            out.append((title, s["properties"]["sheetId"]))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _header_row(sheets_api, spreadsheet_id: str, title: str) -> list[str]:
    resp = (
        sheets_api.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{title}'!A{HEADER_ROW}:Z{HEADER_ROW}")
        .execute()
    )
    row = (resp.get("values") or [[]])[0]
    return list(row)


def _needs_cancel_column(headers: list[str]) -> bool:
    # Old: … 発送, 返品, コメント  | New: … 発送, キャンセル, 返品, コメント
    if len(headers) >= 17 and headers[16] == "キャンセル":
        return False
    if len(headers) >= 17 and headers[15] == "発送" and headers[16] == "返品":
        return True
    if "キャンセル" not in headers and "返品" in headers:
        return True
    return "キャンセル" not in headers


def _insert_cancel_column(sheets_api, spreadsheet_id: str, sheet_id: int) -> None:
    # Insert before old 返品 (0-based index 16)
    batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 16,
                        "endIndex": 17,
                    },
                    "inheritFromBefore": True,
                }
            }
        ],
        label="migrate.insertCancelCol",
    )


def _set_column_count(sheets_api, spreadsheet_id: str, sheet_id: int, n: int) -> None:
    batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"columnCount": n},
                    },
                    "fields": "gridProperties.columnCount",
                }
            }
        ],
        label="migrate.colCount",
    )


def _last_data_row(sheets_api, spreadsheet_id: str, title: str) -> int:
    resp = (
        sheets_api.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{title}'!A{DATA_START_ROW}:A")
        .execute()
    )
    values = resp.get("values") or []
    last = DATA_START_ROW - 1
    for i, row in enumerate(values):
        if row and str(row[0]).strip():
            last = DATA_START_ROW + i
    return last


def _find_strikethrough_rows(
    sheets_api, spreadsheet_id: str, title: str, sheet_id: int
) -> list[int]:
    """Return 1-based row numbers where any of A–F has strikethrough."""
    last = _last_data_row(sheets_api, spreadsheet_id, title)
    if last < DATA_START_ROW:
        return []
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            ranges=[f"'{title}'!A{DATA_START_ROW}:F{last}"],
            includeGridData=True,
            fields=(
                "sheets(properties(sheetId,title),"
                "data(rowData(values("
                "userEnteredFormat/textFormat/strikethrough,"
                "effectiveFormat/textFormat/strikethrough,"
                "formattedValue"
                "))))"
            ),
        ),
        label="migrate.strikeScan",
    )
    rows: list[int] = []
    data = None
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") == sheet_id and s.get("data"):
            data = s["data"][0]
            break
    if data is None:
        for s in meta.get("sheets", []):
            if s.get("data"):
                data = s["data"][0]
                break
    if not data:
        return rows
    for i, row in enumerate(data.get("rowData") or []):
        vals = row.get("values") or []
        if not vals:
            continue
        struck = False
        has_content = False
        for cell in vals[:6]:
            if cell.get("formattedValue"):
                has_content = True
            ue = ((cell.get("userEnteredFormat") or {}).get("textFormat") or {}).get(
                "strikethrough"
            )
            ef = ((cell.get("effectiveFormat") or {}).get("textFormat") or {}).get(
                "strikethrough"
            )
            if ue or ef:
                struck = True
        if struck and has_content:
            rows.append(DATA_START_ROW + i)
    return rows


def _update_month_chrome(
    sheets_api, spreadsheet_id: str, title: str, sheet_id: int
) -> None:
    kpis = month_kpi_formulas()
    kpi_labels = [
        "",
        "有効売上",
        "利益",
        "利益率",
        "総注文",
        "キャンセル",
        "発送済",
        "売上金",
        "仕入",
        "手数料",
        "Pt",
    ]
    kpi_values = [""] * 11
    for cell, formula in kpis.items():
        kpi_values[ord(cell[0]) - 64 - 1] = formula

    values_batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {"range": f"'{title}'!A2", "values": [[HINT_ROW_TEXT]]},
            {"range": f"'{title}'!A3", "values": [kpi_labels]},
            {"range": f"'{title}'!A4", "values": [kpi_values]},
            {"range": f"'{title}'!A{HEADER_ROW}", "values": [list(ORDER_HEADERS)]},
        ],
        label=f"migrate.monthChrome.{title}",
    )

    last = max(_last_data_row(sheets_api, spreadsheet_id, title), DATA_START_ROW)
    validate_end = min(FORMULA_END_ROW, max(last + 50, DATA_START_ROW + 50))

    # widths for checkbox cols P–R (indices 15–17)
    width_reqs = [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": i,
                    "endIndex": i + 1,
                },
                "properties": {"pixelSize": CHECKBOX_COL_WIDTH_PX},
                "fields": "pixelSize",
            }
        }
        for i in (15, 16, 17)
    ]
    # comment col S
    width_reqs.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 18,
                    "endIndex": 19,
                },
                "properties": {"pixelSize": 375},
                "fields": "pixelSize",
            }
        }
    )
    # clear + add CF
    sheets_meta = _meta(sheets_api, spreadsheet_id)
    cf_n = 0
    for s in sheets_meta:
        if s["properties"]["sheetId"] == sheet_id:
            cf_n = len(s.get("conditionalFormats") or [])
            break
    cf_reqs = clear_conditional_format_requests(sheet_id, cf_n)
    cf_reqs.extend(cancel_row_conditional_format_requests(sheet_id))
    cf_reqs.extend(
        checkbox_data_validation_requests(sheet_id, DATA_START_ROW, validate_end)
    )
    # editable header blue for O–S
    from app.sheet_style import EDITABLE_HEAD, EDITABLE_HEAD_TEXT

    for col_1based in (
        COL["ship_notify_date"],
        COL["ship_notify"],
        COL["cancel"],
        COL["return"],
        COL["comment"],
    ):
        c0 = col_1based - 1
        cf_reqs.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": HEADER_ROW - 1,
                        "endRowIndex": HEADER_ROW,
                        "startColumnIndex": c0,
                        "endColumnIndex": c0 + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": EDITABLE_HEAD,
                            "textFormat": {
                                "foregroundColor": EDITABLE_HEAD_TEXT,
                                "fontSize": 11,
                                "bold": True,
                                "fontFamily": "Arial",
                            },
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "wrapStrategy": "WRAP",
                        }
                    },
                    "fields": (
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat,"
                        "userEnteredFormat.horizontalAlignment,"
                        "userEnteredFormat.verticalAlignment,"
                        "userEnteredFormat.wrapStrategy"
                    ),
                }
            }
        )

    batch_update(
        sheets_api,
        spreadsheet_id,
        width_reqs + cf_reqs,
        chunk_size=20,
        pace_seconds=0.8,
        label=f"migrate.monthStyle.{title}",
    )


def _update_overview(sheets_api, spreadsheet_id: str, month_titles: list[str]) -> None:
    sheets = _meta(sheets_api, spreadsheet_id)
    ov = next(s for s in sheets if s["properties"]["title"] == SUMMARY_SHEET)
    ov_id = ov["properties"]["sheetId"]
    _set_column_count(sheets_api, spreadsheet_id, ov_id, OVERVIEW_NUM_COLS)

    month_start = 11
    n = max(len(month_titles), 1)
    month_end = month_start + n - 1
    kpi_labels = [
        "",
        "有効売上",
        "利益",
        "総注文",
        "キャンセル",
        "発送済",
        "売上金",
        "仕入",
        "手数料",
        "利益率",
        "Pt",
    ]
    kpi_vals = [
        "",
        f"=SUM(B{month_start}:B{month_end})",
        f"=SUM(C{month_start}:C{month_end})",
        f"=SUM(D{month_start}:D{month_end})",
        f"=SUM(E{month_start}:E{month_end})",
        f"=SUM(F{month_start}:F{month_end})",
        f"=SUM(G{month_start}:G{month_end})",
        f"=SUM(H{month_start}:H{month_end})",
        f"=SUM(I{month_start}:I{month_end})",
        '=IFERROR(C7/B7,"")',
        f"=SUM(K{month_start}:K{month_end})",
    ]
    table_header = [
        "月",
        "有効売上",
        "利益",
        "総注文",
        "キャンセル",
        "発送済",
        "売上金",
        "仕入",
        "手数料",
        "利益率",
        "Pt",
    ]
    month_rows = []
    for m in month_titles:
        month_rows.append(
            [
                m,
                f"='{m}'!B4",
                f"='{m}'!C4",
                f"='{m}'!E4",
                f"='{m}'!F4",
                f"='{m}'!G4",
                f"='{m}'!H4",
                f"='{m}'!I4",
                f"='{m}'!J4",
                f"='{m}'!D4",
                f"='{m}'!K4",
            ]
        )

    data = [
        {"range": f"'{SUMMARY_SHEET}'!A6", "values": [kpi_labels]},
        {"range": f"'{SUMMARY_SHEET}'!A7", "values": [kpi_vals]},
        {"range": f"'{SUMMARY_SHEET}'!A10", "values": [table_header]},
    ]
    if month_rows:
        data.append({"range": f"'{SUMMARY_SHEET}'!A11", "values": month_rows})
    values_batch_update(sheets_api, spreadsheet_id, data, label="migrate.overview")


def migrate_spreadsheet(sheets_api, spreadsheet_id: str) -> dict:
    sheets = _meta(sheets_api, spreadsheet_id)
    months = _month_tabs(sheets)
    report: dict = {"spreadsheet_id": spreadsheet_id, "months": [], "overview": False}

    for title, sheet_id in months:
        headers = _header_row(sheets_api, spreadsheet_id, title)
        inserted = False
        if _needs_cancel_column(headers):
            _insert_cancel_column(sheets_api, spreadsheet_id, sheet_id)
            inserted = True
        _set_column_count(sheets_api, spreadsheet_id, sheet_id, NUM_COLS)
        _update_month_chrome(sheets_api, spreadsheet_id, title, sheet_id)

        struck = _find_strikethrough_rows(sheets_api, spreadsheet_id, title, sheet_id)
        if struck:
            apply_buyer_cancels_many(
                sheets_api,
                spreadsheet_id,
                sheet_id=sheet_id,
                sheet_title=title,
                rows_1based=struck,
            )
        report["months"].append(
            {
                "title": title,
                "inserted_cancel_col": inserted,
                "strikethrough_rows": struck,
                "buyer_cancel_locked": struck,
            }
        )
        time.sleep(3)

    month_titles = [t for t, _ in months]
    _update_overview(sheets_api, spreadsheet_id, month_titles)
    report["overview"] = True

    # Strip remaining range locks (policy: all cells user-editable).
    apply_protections(sheets_api, spreadsheet_id, data_row_counts=None)
    report["protections"] = True
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spreadsheet-id",
        default="",
        help="Target spreadsheet id (default: first on-drive user workbook)",
    )
    args = parser.parse_args()
    creds = load_operator_credentials()
    sheets = sheets_service(creds)

    sid = (args.spreadsheet_id or "").strip()
    if not sid:
        from app.provision import list_user_workbooks

        users = list_user_workbooks()
        live = [u for u in users if u.get("spreadsheet_id") and u.get("on_drive")]
        if not live:
            raise SystemExit("No on-drive workbooks found")
        sid = live[0]["spreadsheet_id"]
        print(f"Using {live[0].get('title')} ({sid})")

    report = migrate_spreadsheet(sheets, sid)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
