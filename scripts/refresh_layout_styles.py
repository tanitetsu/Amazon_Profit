"""Refresh month summary layout (Book 3 widths/merges) + styles on live workbook."""

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
from app.buyer_cancel import checkbox_data_validation_requests  # noqa: E402
from app.schema import (  # noqa: E402
    DATA_START_ROW,
    KPI_LABEL_ROW,
    KPI_VALUE_ROW,
    NUM_COLS,
    OVERVIEW_NUM_COLS,
    SUMMARY_SHEET,
    col_letter,
    spreadsheet_title_from_gmail,
)
from app.sheet_builder import (  # noqa: E402
    build_summary_grid,
    month_summary_rows,
    period_from_months,
)
from app.sheet_style import month_style_requests, summary_style_requests  # noqa: E402
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update  # noqa: E402
from app.users_store import load_users_config  # noqa: E402


def _col_letter(n: int) -> str:
    return col_letter(n)


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
            spreadsheetId=sid, fields="sheets(properties)"
        ),
        label="layout.get",
    )
    months: list[tuple[str, int]] = []
    overview_id = None
    for s in meta.get("sheets", []):
        t = s["properties"]["title"]
        i = s["properties"]["sheetId"]
        if t == SUMMARY_SHEET:
            overview_id = i
        elif len(t) == 7 and t[4] == "-":
            months.append((t, i))
    months.sort(key=lambda x: x[0], reverse=True)
    month_titles = [t for t, _ in months]

    # Unmerge summary rows BEFORE rewriting values (writes into merges drop cells)
    unmerge_reqs = []
    for t, i in months:
        unmerge_reqs.append(
            {
                "unmergeCells": {
                    "range": {
                        "sheetId": i,
                        "startRowIndex": KPI_LABEL_ROW - 1,
                        "endRowIndex": KPI_VALUE_ROW,
                        "startColumnIndex": 0,
                        "endColumnIndex": NUM_COLS,
                    }
                }
            }
        )
    if unmerge_reqs:
        batch_update(
            sheets,
            sid,
            unmerge_reqs,
            chunk_size=20,
            pace_seconds=0.5,
            label="layout.unmerge",
        )

    # Rewrite month summary label/value rows
    end_letter = _col_letter(NUM_COLS)
    value_data = []
    for t, _ in months:
        labels, values = month_summary_rows()
        value_data.append(
            {
                "range": f"'{t}'!A{KPI_LABEL_ROW}:{end_letter}{KPI_VALUE_ROW}",
                "values": [labels, values],
            }
        )
    if value_data:
        values_batch_update(
            sheets, sid, value_data, label="layout.month_summary_values"
        )

    # Rebuild Overview grid (new month cell refs) for header + month rows
    ps, pe = period_from_months(month_titles)
    overview = build_summary_grid(gmail, year, ps, pe, month_titles)
    values_batch_update(
        sheets,
        sid,
        [
            {
                "range": f"'{SUMMARY_SHEET}'!A1:{_col_letter(OVERVIEW_NUM_COLS)}{len(overview)}",
                "values": overview,
            }
        ],
        label="layout.overview_grid",
    )

    reqs: list = []
    if overview_id is not None:
        reqs.extend(summary_style_requests(overview_id, month_count=len(months)))

    for t, i in months:
        data = (
            sheets.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"'{t}'!A{DATA_START_ROW}:A")
            .execute()
            .get("values", [])
        )
        n = 0
        for row in data:
            if not row or not str(row[0]).strip():
                break
            n += 1
        reqs.extend(month_style_requests(i, checkboxes=False))
        if n > 0:
            reqs.extend(
                checkbox_data_validation_requests(
                    i, DATA_START_ROW, DATA_START_ROW + n - 1
                )
            )

    batch_update(
        sheets, sid, reqs, chunk_size=12, pace_seconds=1.0, label="layout.styles"
    )
    print(
        {
            "spreadsheet_id": sid,
            "months": len(months),
            "merges": True,
            "ok": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
