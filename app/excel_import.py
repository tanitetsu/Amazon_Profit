"""Write legacy Excel / parsed order rows into a yearly Google workbook."""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from app.buyer_cancel import checkbox_data_validation_requests, lock_cancel_checkbox
from app.legacy_excel import LegacyOrderRow, group_by_month, load_legacy_orders
from app.mercari import fetch_mercari_price, mercari_item_url
from app.order_sku import normalize_sku
from app.schema import (
    COL,
    DATA_START_ROW,
    DETAIL_SPANS,
    NUM_COLS,
    SUMMARY_SHEET,
    spreadsheet_title_from_gmail,
)
from app.sheet_builder import (
    build_summary_grid,
    linked_order_and_title,
    month_sheet_skeleton,
    period_from_months,
    row_profit_formula,
    row_profit_rate_formula,
)
from app.sheet_links import apply_rich_links, order_title_rich_links
from app.sheet_protection import apply_protections, share_editor
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update
from app.workbook import initialize_workbook


def _sheets_date(d: date | None) -> str | None:
    if d is None:
        return None
    return d.isoformat()


def order_row_values(row: LegacyOrderRow, sheet_row: int) -> list[Any]:
    vals: list[Any] = [""] * NUM_COLS
    sku = normalize_sku(row.sku)
    oid, title = linked_order_and_title(row.order_id, row.title, sku)
    vals[COL["order_id"] - 1] = oid
    vals[COL["sku"] - 1] = sku
    vals[COL["title"] - 1] = title
    vals[COL["order_date"] - 1] = _sheets_date(row.order_date)
    vals[COL["ship_by"] - 1] = _sheets_date(row.ship_by)
    vals[COL["status"] - 1] = row.status
    vals[COL["price"] - 1] = row.price
    vals[COL["tax"] - 1] = row.tax
    vals[COL["fee"] - 1] = row.fee
    vals[COL["points"] - 1] = row.points
    vals[COL["proceeds"] - 1] = row.proceeds
    vals[COL["cost"] - 1] = row.cost
    vals[COL["extra_cost"] - 1] = ""
    vals[COL["profit"] - 1] = row_profit_formula(sheet_row)
    vals[COL["profit_rate"] - 1] = row_profit_rate_formula(sheet_row)
    vals[COL["ship_date"] - 1] = _sheets_date(row.ship_date)
    vals[COL["cost_done"] - 1] = False
    vals[COL["shipped"] - 1] = bool(row.shipped)
    vals[COL["cancel"] - 1] = False
    vals[COL["done"] - 1] = False
    vals[COL["comment"] - 1] = row.comment
    return vals


def fill_month_sheet(
    sheets_api,
    spreadsheet_id: str,
    sheet_id: int,
    month_title: str,
    rows: Sequence[LegacyOrderRow],
    *,
    operator_email: str | None = None,
) -> None:
    """Write month values only. Template already has merges/CF/fonts — never restyle."""
    n = len(rows)
    skeleton = month_sheet_skeleton(month_title, data_rows=0)
    values = [list(r) for r in skeleton]
    for i, row in enumerate(rows):
        sheet_row = DATA_START_ROW + i
        values.append(order_row_values(row, sheet_row))

    values_batch_update(
        sheets_api,
        spreadsheet_id,
        [{"range": f"'{month_title}'!A1", "values": values}],
        label=f"fill.{month_title}.values",
    )

    if n > 0:
        batch_update(
            sheets_api,
            spreadsheet_id,
            checkbox_data_validation_requests(
                sheet_id, DATA_START_ROW, DATA_START_ROW + n - 1
            ),
            label=f"fill.{month_title}.checkbox",
        )

    link_rows = [
        (DATA_START_ROW + i, row.order_id, row.title, normalize_sku(row.sku))
        for i, row in enumerate(rows)
    ]
    apply_rich_links(
        sheets_api,
        spreadsheet_id,
        sheet_id,
        order_title_rich_links(
            link_rows,
            order_col_0=DETAIL_SPANS["order_id"][0],
            title_col_0=DETAIL_SPANS["title"][0],
        ),
        label=f"fill.{month_title}.links",
    )

    lock_rows = [
        DATA_START_ROW + i for i, row in enumerate(rows) if row.cancel_lock
    ]
    if lock_rows:
        lock_cancel_checkbox(
            sheets_api,
            spreadsheet_id,
            sheet_id,
            month_title,
            lock_rows,
            operator_email=operator_email,
        )


def import_legacy_excel_to_workbook(
    sheets_api,
    drive,
    *,
    gmail: str,
    excel_path: str,
    year: int = 2026,
    folder_id: str,
    share_with_user: bool = True,
    rebuild: bool = True,
    use_template: bool = True,
) -> dict[str, Any]:
    """
    Create (or rebuild) amazon-profit_{user}_{year}.xlsx and load Excel rows.
    Default: copy Drive template, ensure months, fill values (no restyle).
    """
    from app.google_clients import (
        copy_spreadsheet_in_folder,
        create_spreadsheet_in_folder,
        find_spreadsheet_in_folder,
        retire_spreadsheet_for_overwrite,
    )
    from app.template_ops import (
        ensure_months_for_order,
        hide_month_template_sheet,
        resolve_template_spreadsheet_id,
        touch_last_auto_update,
    )
    from app.users_store import load_users_config
    from app.clipping_roster import upsert_clipping_user

    rows = load_legacy_orders(excel_path)
    # Excel 仕入金を優先。空かつメルカリ対象 SKU のみ API 補完。
    enriched = 0
    for i, row in enumerate(rows, start=1):
        if row.cost is not None:
            continue
        sku = normalize_sku(row.sku)
        if not mercari_item_url(sku):
            continue
        price = fetch_mercari_price(sku)
        if price is not None:
            row.cost = price
            enriched += 1
        if i % 25 == 0 or i == len(rows):
            print(f"mercari.cost progress {i}/{len(rows)} filled={enriched}", flush=True)
    print(f"mercari.cost done filled={enriched}", flush=True)
    by_month = group_by_month(rows)
    # keep only target year
    by_month = {k: v for k, v in by_month.items() if k.startswith(f"{year:04d}-")}
    if not by_month:
        raise ValueError(f"no rows for year {year} in {excel_path}")

    month_titles = sorted(by_month.keys(), reverse=True)
    months_asc = sorted(by_month.keys())
    title = spreadsheet_title_from_gmail(gmail, year)

    existing = find_spreadsheet_in_folder(drive, title, folder_id)
    if existing and rebuild:
        print(f"retire existing {existing} …", flush=True)
        retire_spreadsheet_for_overwrite(drive, existing, title)
        existing = None

    if existing and not rebuild:
        spreadsheet_id = existing
    elif use_template:
        template_id = resolve_template_spreadsheet_id(drive)
        print(f"copy template {template_id} → {title} …", flush=True)
        spreadsheet_id = copy_spreadsheet_in_folder(
            drive, template_id, title, folder_id
        )
        hide_month_template_sheet(sheets_api, spreadsheet_id)
        print("ensure months from template …", flush=True)
        ensure_months_for_order(
            sheets_api, spreadsheet_id, months_asc[0], gmail=gmail, year=year
        )
        ensure_months_for_order(
            sheets_api, spreadsheet_id, months_asc[-1], gmail=gmail, year=year
        )
    else:
        spreadsheet_id = create_spreadsheet_in_folder(
            drive, sheets_api, title, folder_id
        )
        print("initialize_workbook …", flush=True)
        initialize_workbook(
            sheets_api,
            spreadsheet_id,
            gmail,
            year=year,
            month_titles=month_titles,
            style_months=False,
        )

    titles = {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in execute_with_retry(
            sheets_api.spreadsheets().get(spreadsheetId=spreadsheet_id),
            label="import.meta",
        ).get("sheets", [])
    }

    period_start, period_end = period_from_months(month_titles)
    values_batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {
                "range": f"'{SUMMARY_SHEET}'!A1",
                "values": build_summary_grid(
                    gmail, year, period_start, period_end, month_titles
                ),
            }
        ],
        label="import.overview.values",
    )
    touch_last_auto_update(sheets_api, spreadsheet_id, gmail=gmail, year=year)

    operator = (load_users_config().get("operator_drive_email") or "").strip()
    counts: dict[str, int] = {}
    for mt in month_titles:
        print(f"fill.{mt} rows={len(by_month[mt])} …", flush=True)
        fill_month_sheet(
            sheets_api,
            spreadsheet_id,
            titles[mt],
            mt,
            by_month[mt],
            operator_email=operator,
        )
        counts[mt] = len(by_month[mt])
        print(f"fill.{mt} done", flush=True)

    apply_protections(sheets_api, spreadsheet_id)
    upsert_clipping_user(gmail, "Normal")

    # Cancel☑→状態: テンプレ copy 継承。ここでは API 再配備しない（重複プロジェクト防止）
    script_error = None

    if share_with_user:
        share_editor(
            drive,
            spreadsheet_id,
            gmail,
            send_notification=False,
            email_message=None,
        )

    return {
        "gmail": gmail,
        "year": year,
        "title": title,
        "spreadsheet_id": spreadsheet_id,
        "months": counts,
        "total_rows": sum(counts.values()),
        "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        "script_error": script_error,
        "from_template": bool(use_template),
        "mercari_cost_filled": enriched,
    }
