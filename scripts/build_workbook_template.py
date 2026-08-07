"""Build / refresh amazon-profit_TEMPLATE.xlsx on operator Drive.

Do not run against the live template unless the operator explicitly asks.
Sheets: ダッシュボード + 月次テンプレート left **visible** (operator can edit;
user books hide it after copy via template_ops.hide_month_template_sheet).
Detail rows: 2000 merged, empty rows without ☑.
Cancel☑→状態 bound onEdit can be attached here; operator may Save/edit later.
New users inherit whatever is on the template at copy time.
☑ validation is added only on order APPEND / Excel data rows.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import (  # noqa: E402
    create_spreadsheet_in_folder,
    drive_service,
    find_spreadsheet_in_folder,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.schema import (  # noqa: E402
    DETAIL_DATA_ROWS,
    MONTH_TEMPLATE_SHEET,
    OVERVIEW_KPI_VALUE_ROW,
    OVERVIEW_MONTH_DATA_START_ROW,
    OVERVIEW_MONTH_SLOTS,
    OVERVIEW_NUM_COLS,
    OVERVIEW_ROW_COUNT,
    OVERVIEW_SECTION_MONTH_LABEL,
    OVERVIEW_SECTION_SUMMARY_LABEL,
    OVERVIEW_METRIC_LABELS,
    SUMMARY_SHEET,
    TEMPLATE_SPREADSHEET_TITLE,
)
from app.sheet_builder import month_sheet_skeleton  # noqa: E402
from app.sheet_style import month_style_requests, summary_style_requests  # noqa: E402
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update  # noqa: E402
from app.template_ops import annual_sum_formulas  # noqa: E402
from app.users_store import load_users_config, save_users_config  # noqa: E402
from app.workbook import _ensure_sheets  # noqa: E402


def main() -> int:
    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])

    existing = find_spreadsheet_in_folder(drive, TEMPLATE_SPREADSHEET_TITLE, folder_id)
    if existing:
        print(f"deleting old template {existing}", flush=True)
        drive.files().delete(fileId=existing).execute()

    sid = create_spreadsheet_in_folder(
        drive, sheets, TEMPLATE_SPREADSHEET_TITLE, folder_id
    )
    print(f"created {sid}", flush=True)

    # Ensure sheets named as template (dashboard + month template)
    titles = _ensure_sheets(sheets, sid, [MONTH_TEMPLATE_SHEET])
    # _ensure_sheets may leave Overview — rename to ダッシュボード
    meta = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=sid, fields="sheets.properties(sheetId,title)"
        ),
        label="tmplbuild.meta",
    )
    reqs = []
    for s in meta.get("sheets", []):
        t = s["properties"]["title"]
        sid_ = s["properties"]["sheetId"]
        if t in ("Overview", "シート1", "Sheet1") and t != SUMMARY_SHEET:
            reqs.append(
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": sid_, "title": SUMMARY_SHEET},
                        "fields": "title",
                    }
                }
            )
        # Prefer 月次テンプレート visible on the template (operator edit). Hide is on copy.
    # Drop stray sheets
    keep = {SUMMARY_SHEET, MONTH_TEMPLATE_SHEET}
    for s in meta.get("sheets", []):
        t = s["properties"]["title"]
        if t not in keep and t not in ("Overview", "シート1", "Sheet1"):
            reqs.append({"deleteSheet": {"sheetId": s["properties"]["sheetId"]}})
    if reqs:
        batch_update(sheets, sid, reqs, label="tmplbuild.rename")

    titles = {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in execute_with_retry(
            sheets.spreadsheets().get(
                spreadsheetId=sid, fields="sheets.properties(sheetId,title)"
            ),
            label="tmplbuild.meta2",
        ).get("sheets", [])
    }
    if SUMMARY_SHEET not in titles:
        # create dashboard
        batch_update(
            sheets,
            sid,
            [{"addSheet": {"properties": {"title": SUMMARY_SHEET, "index": 0}}}],
            label="tmplbuild.add.dash",
        )
        titles = {
            s["properties"]["title"]: s["properties"]["sheetId"]
            for s in execute_with_retry(
                sheets.spreadsheets().get(
                    spreadsheetId=sid, fields="sheets.properties(sheetId,title)"
                ),
                label="tmplbuild.meta3",
            ).get("sheets", [])
        }

    # Dashboard grid: blank meta, wide SUM, no month rows
    end_slot = OVERVIEW_MONTH_DATA_START_ROW + OVERVIEW_MONTH_SLOTS - 1
    dash: list[list] = [
        ["ダッシュボード"],
        [""],  # meta filled on provision
        [],
        [OVERVIEW_SECTION_SUMMARY_LABEL],
        ["", *OVERVIEW_METRIC_LABELS],
        annual_sum_formulas(),
        [],
        [OVERVIEW_SECTION_MONTH_LABEL],
        ["月", *OVERVIEW_METRIC_LABELS],
    ]
    while len(dash) < end_slot:
        dash.append([""] * OVERVIEW_NUM_COLS)

    values_batch_update(
        sheets,
        sid,
        [{"range": f"'{SUMMARY_SHEET}'!A1", "values": dash}],
        label="tmplbuild.dash.values",
    )
    batch_update(
        sheets,
        sid,
        summary_style_requests(titles[SUMMARY_SHEET], month_count=0),
        chunk_size=8,
        pace_seconds=1.0,
        label="tmplbuild.dash.style",
    )
    # Force fixed rowCount
    batch_update(
        sheets,
        sid,
        [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": titles[SUMMARY_SHEET],
                        "gridProperties": {"rowCount": OVERVIEW_ROW_COUNT},
                    },
                    "fields": "gridProperties.rowCount",
                }
            }
        ],
        label="tmplbuild.dash.rows",
    )

    # Month template: skeleton + style with 2000 detail merges (no ☑)
    mt_grid = month_sheet_skeleton(MONTH_TEMPLATE_SHEET, data_rows=0)
    mt_grid[0][0] = MONTH_TEMPLATE_SHEET
    values_batch_update(
        sheets,
        sid,
        [{"range": f"'{MONTH_TEMPLATE_SHEET}'!A1", "values": mt_grid}],
        label="tmplbuild.month.values",
    )
    batch_update(
        sheets,
        sid,
        month_style_requests(
            titles[MONTH_TEMPLATE_SHEET],
            data_rows=DETAIL_DATA_ROWS,
            checkboxes=False,
        ),
        chunk_size=8,
        pace_seconds=1.2,
        label="tmplbuild.month.style",
    )
    # Belt-and-suspenders: never leave ☑ on empty template detail rows.
    from app.buyer_cancel import clear_checkbox_validation_requests
    from app.schema import DATA_START_ROW, FORMULA_END_ROW

    batch_update(
        sheets,
        sid,
        clear_checkbox_validation_requests(
            titles[MONTH_TEMPLATE_SHEET], DATA_START_ROW, FORMULA_END_ROW
        ),
        label="tmplbuild.month.clear_checkboxes",
    )
    # Leave 月次テンプレート visible here; user-book copy path hides it.

    # Cancel☑→状態: optional attach; operator may Save/edit on template later.
    script_error = None
    try:
        from app.apps_script_deploy import ensure_cancel_onedit_script

        ensure_cancel_onedit_script(sid, creds=creds)
    except Exception as exc:  # noqa: BLE001
        script_error = str(exc)
        print("script attach failed:", script_error, flush=True)

    cfg["template_spreadsheet_id"] = sid
    save_users_config(cfg)
    url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    print(
        json.dumps(
            {
                "template_spreadsheet_id": sid,
                "url": url,
                "kpi_row": OVERVIEW_KPI_VALUE_ROW,
                "month_slots": OVERVIEW_MONTH_SLOTS,
                "row_count": OVERVIEW_ROW_COUNT,
                "script_error": script_error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
