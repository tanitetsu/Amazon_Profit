"""Rebuild live Overview to match canonical layout (13 cols / live Overview)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import (  # noqa: E402
    drive_service,
    find_spreadsheet_in_folder,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.schema import (  # noqa: E402
    OVERVIEW_NUM_COLS,
    SUMMARY_SHEET,
    col_letter,
    spreadsheet_title_from_gmail,
)
from app.sheet_builder import build_summary_grid, period_from_months  # noqa: E402
from app.sheet_style import summary_style_requests  # noqa: E402
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update  # noqa: E402
from app.users_store import load_users_config  # noqa: E402


def main() -> int:
    cfg = load_users_config()
    gmail = "asamiodaka@gmail.com"
    year = 2026
    title = spreadsheet_title_from_gmail(gmail, year)
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    sid = find_spreadsheet_in_folder(drive, title, folder_id)
    if not sid:
        print(f"not found: {title}", file=sys.stderr)
        return 1

    meta = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=sid,
            fields="sheets(properties,merges)",
        ),
        label="ov.get",
    )
    overview_id = None
    merges: list = []
    months: list[str] = []
    for s in meta.get("sheets", []):
        t = s["properties"]["title"]
        if t == SUMMARY_SHEET:
            overview_id = s["properties"]["sheetId"]
            merges = list(s.get("merges") or [])
        elif len(t) == 7 and t[4] == "-":
            months.append(t)
    if overview_id is None:
        print("Overview not found", file=sys.stderr)
        return 1
    months = sorted(months, reverse=True)

    # Unmerge + clear old wide layout (was 13 cols)
    if merges:
        batch_update(
            sheets,
            sid,
            [{"unmergeCells": {"range": m}} for m in merges],
            label="ov.unmerge",
        )

    values_batch_update(
        sheets,
        sid,
        [{"range": f"'{SUMMARY_SHEET}'!A1:Z40", "values": [[""] * 26] * 40}],
        label="ov.clear",
    )

    ps, pe = period_from_months(months)
    grid = build_summary_grid(gmail, year, ps, pe, months)
    end = col_letter(OVERVIEW_NUM_COLS)
    values_batch_update(
        sheets,
        sid,
        [
            {
                "range": f"'{SUMMARY_SHEET}'!A1:{end}{len(grid)}",
                "values": grid,
            }
        ],
        label="ov.values",
    )

    batch_update(
        sheets,
        sid,
        summary_style_requests(overview_id, month_count=len(months)),
        chunk_size=40,
        pace_seconds=0.4,
        label="ov.style",
    )
    print({"spreadsheet_id": sid, "months": months, "rows": len(grid)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
