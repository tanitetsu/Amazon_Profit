"""Initialize / rebuild yearly profit workbooks on Google Sheets."""

from __future__ import annotations

from datetime import date
from typing import Sequence

from app.schema import SUMMARY_SHEET
from app.sheet_builder import (
    build_summary_grid,
    month_sheet_skeleton,
    period_from_months,
)
from app.sheet_style import month_style_requests, summary_style_requests
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update, values_clear


def _meta_sheets(sheets_api, spreadsheet_id: str) -> dict[str, int]:
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(spreadsheetId=spreadsheet_id),
        label="get.meta",
    )
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def _ensure_sheets(
    sheets_api,
    spreadsheet_id: str,
    month_titles: Sequence[str],
) -> dict[str, int]:
    titles = _meta_sheets(sheets_api, spreadsheet_id)
    requests: list[dict] = []

    if "サマリー" in titles and SUMMARY_SHEET not in titles:
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": titles["サマリー"],
                        "title": SUMMARY_SHEET,
                    },
                    "fields": "title",
                }
            }
        )
    if requests:
        batch_update(sheets_api, spreadsheet_id, requests, label="rename.summary")
        titles = _meta_sheets(sheets_api, spreadsheet_id)

    requests = []
    if SUMMARY_SHEET not in titles:
        requests.append({"addSheet": {"properties": {"title": SUMMARY_SHEET, "index": 0}}})
    for i, title in enumerate(month_titles):
        if title not in titles:
            requests.append({"addSheet": {"properties": {"title": title, "index": i + 1}}})
    if requests:
        batch_update(sheets_api, spreadsheet_id, requests, label="add.sheets")
        titles = _meta_sheets(sheets_api, spreadsheet_id)

    keep = {SUMMARY_SHEET, *month_titles}
    delete = [
        {"deleteSheet": {"sheetId": sid}}
        for title, sid in titles.items()
        if title not in keep
    ]
    if delete and len(titles) > len(delete):
        batch_update(sheets_api, spreadsheet_id, delete, label="delete.sheets")
        titles = _meta_sheets(sheets_api, spreadsheet_id)

    order_reqs: list[dict] = []
    # Newest month first after Overview
    desired = [SUMMARY_SHEET, *sorted(month_titles, reverse=True)]
    for index, name in enumerate(desired):
        sid = titles.get(name)
        if sid is None:
            continue
        order_reqs.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sid, "index": index},
                    "fields": "index",
                }
            }
        )
    if order_reqs:
        batch_update(sheets_api, spreadsheet_id, order_reqs, label="order.tabs")
        titles = _meta_sheets(sheets_api, spreadsheet_id)
    return titles


def _clear_sheet(sheets_api, spreadsheet_id: str, title: str) -> None:
    values_clear(sheets_api, spreadsheet_id, f"'{title}'")


def _clear_bandings(sheets_api, spreadsheet_id: str) -> None:
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId),bandedRanges)",
        ),
        label="get.bandings",
    )
    reqs = []
    for s in meta.get("sheets", []):
        for b in s.get("bandedRanges", []) or []:
            reqs.append({"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}})
    batch_update(sheets_api, spreadsheet_id, reqs, chunk_size=20, label="clear.bandings")


def _clear_conditional_formats(sheets_api, spreadsheet_id: str) -> None:
    from app.buyer_cancel import clear_conditional_format_requests

    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId),conditionalFormats)",
        ),
        label="get.conditionalFormats",
    )
    reqs: list[dict] = []
    for s in meta.get("sheets", []):
        sid = s["properties"]["sheetId"]
        n = len(s.get("conditionalFormats") or [])
        reqs.extend(clear_conditional_format_requests(sid, n))
    batch_update(
        sheets_api, spreadsheet_id, reqs, chunk_size=40, label="clear.conditionalFormats"
    )


def initialize_workbook(
    sheets_api,
    spreadsheet_id: str,
    gmail: str,
    *,
    year: int | None = None,
    month_titles: Sequence[str] | None = None,
    style_months: bool = True,
) -> list[str]:
    """Create Overview + month sheets (empty data unless caller fills rows)."""
    today = date.today()
    y = year if year is not None else today.year
    if month_titles is None:
        month_titles = [f"{y:04d}-{today.month:02d}"]
    else:
        month_titles = list(month_titles)
    # newest first for tabs
    month_titles = sorted(month_titles, reverse=True)
    period_start, period_end = period_from_months(month_titles)
    titles = _ensure_sheets(sheets_api, spreadsheet_id, month_titles)
    _clear_bandings(sheets_api, spreadsheet_id)
    _clear_conditional_formats(sheets_api, spreadsheet_id)

    for title in [SUMMARY_SHEET, *month_titles]:
        _clear_sheet(sheets_api, spreadsheet_id, title)

    value_data = [
        {
            "range": f"'{SUMMARY_SHEET}'!A1",
            "values": build_summary_grid(
                gmail, y, period_start, period_end, month_titles
            ),
        }
    ]
    for mt in month_titles:
        value_data.append(
            {"range": f"'{mt}'!A1", "values": month_sheet_skeleton(mt, data_rows=0)}
        )

    print(f"init.values months={month_titles} …", flush=True)
    values_batch_update(sheets_api, spreadsheet_id, value_data, label="init.values")
    print("init.styles.overview …", flush=True)
    batch_update(
        sheets_api,
        spreadsheet_id,
        summary_style_requests(titles[SUMMARY_SHEET], month_count=len(month_titles)),
        chunk_size=8,
        pace_seconds=1.2,
        label="init.styles.overview",
    )
    if style_months:
        for mt in month_titles:
            print(f"init.styles.{mt} …", flush=True)
            batch_update(
                sheets_api,
                spreadsheet_id,
                month_style_requests(titles[mt], checkboxes=False),
                chunk_size=8,
                pace_seconds=1.2,
                label=f"init.styles.{mt}",
            )
    else:
        print("init.styles.months skipped (caller will style on fill)", flush=True)
    return month_titles
