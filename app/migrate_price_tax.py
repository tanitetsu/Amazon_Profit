"""Split 税込価格 → 販売価格 + 税金 on live template and user books.

Does not compute tax from a combined amount. Existing rows are filled from
the user's 注文確定 mail (価格 / 税金 as parsed). Editable columns are never
written.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.gmail_fetch import iter_amazon_mails
from app.gmail_oauth import load_gmail_credentials
from app.mail_parser import parse_eml_bytes
from app.order_sku import normalize_sku
from app.schema import (
    COL,
    DATA_START_ROW,
    DETAIL_FIELDS,
    DETAIL_FONT_PT,
    DETAIL_HEADER_FONT_PT,
    DETAIL_SPANS,
    FORMULA_END_ROW,
    HEADER_ROW,
    KPI_LABEL_ROW,
    KPI_VALUE_ROW,
    MONTH_COL_WIDTHS,
    MONTH_SUMMARY_LABELS,
    MONTH_SUMMARY_MERGES,
    MONTH_TEMPLATE_SHEET,
    NUM_COLS,
    OVERVIEW_KPI_LABEL_ROW,
    OVERVIEW_KPI_VALUE_ROW,
    OVERVIEW_METRIC_KPI_INDEX,
    OVERVIEW_METRIC_LABELS,
    OVERVIEW_MONTH_DATA_START_ROW,
    OVERVIEW_MONTH_HEADER_ROW,
    OVERVIEW_MONTH_SLOTS,
    OVERVIEW_NUM_COLS,
    SUMMARY_CANCEL_LABEL_FONT_PT,
    SUMMARY_LABEL_FONT_PT,
    SUMMARY_SHEET,
    SUMMARY_VALUE_FONT_PT,
    TEMPLATE_SPREADSHEET_TITLE,
    col_letter,
    gmail_from_user_id,
)
from app.sheet_builder import (
    month_kpi_anchor_a1,
    month_summary_rows,
    row_profit_formula,
    row_profit_rate_formula,
)
from app.sheet_style import (
    BLACK,
    COUNT_LABEL_BG,
    HAIR,
    HERO_TEXT,
    SUMMARY_BLACK_BG,
    SUMMARY_BLUE_BG,
    SUMMARY_GREEN_BG,
    SUMMARY_ORANGE_BG,
    TABLE_HEAD,
    WHITE,
    _all_borders,
    _col_widths,
    _merge,
    _outer_inner_borders,
    _paint,
    summary_style_requests,
)
from app.sheets_retry import batch_update, execute_with_retry, values_batch_update
from app.template_ops import annual_sum_formulas, touch_last_auto_update

BACKUP_USER_ID = "asamiodaka.b"
ORDER_MAIL_QUERY = (
    "from:amazon.co.jp subject:注文確定 -subject:返金手続き開始"
)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_TITLE_RE = re.compile(r"^amazon-profit_(.+)_(\d{4})\.xlsx$", re.IGNORECASE)

EDITABLE_KEYS = frozenset(f.key for f in DETAIL_FIELDS if f.editable)
# Insert 税金 units at the old 手数料 start (= new tax start after schema change).
TAX_INSERT_INDEX_0 = DETAIL_SPANS["tax"][0]
TAX_UNITS = DETAIL_SPANS["tax"][1] - DETAIL_SPANS["tax"][0]


def month_headers_migrated(price_header: str, tax_header: str) -> bool:
    return (price_header or "").strip() == "販売価格" and (
        tax_header or ""
    ).strip() == "税金"


def overview_labels_migrated(labels: list[str]) -> bool:
    cleaned = [(x or "").strip() for x in labels]
    return "販売価格" in cleaned and "税金" in cleaned


def price_tax_updates_for_row(
    month: str,
    row_1: int,
    price: int | float | None,
    tax: int | float | None,
) -> list[dict[str, Any]]:
    """Value writes for 販売価格 / 税金 only. Never touches editable cols."""
    updates: list[dict[str, Any]] = []
    if price is not None:
        updates.append(
            {
                "range": f"'{month}'!{col_letter(COL['price'])}{row_1}",
                "values": [[price]],
            }
        )
    if tax is not None:
        updates.append(
            {
                "range": f"'{month}'!{col_letter(COL['tax'])}{row_1}",
                "values": [[tax]],
            }
        )
    forbidden = {col_letter(COL[k]) for k in EDITABLE_KEYS}
    for u in updates:
        letter = "".join(ch for ch in u["range"].split("!")[1] if ch.isalpha())
        if letter in forbidden:
            raise RuntimeError(f"refusing editable-column write: {u['range']}")
    return updates


def _sheet_props(meta: dict[str, Any]) -> list[dict[str, Any]]:
    return [s["properties"] for s in meta.get("sheets", [])]


def _month_and_template_sheets(
    meta: dict[str, Any],
) -> list[tuple[str, int, dict[str, Any]]]:
    out: list[tuple[str, int, dict[str, Any]]] = []
    for s in meta.get("sheets", []):
        props = s["properties"]
        title = props["title"]
        if title == MONTH_TEMPLATE_SHEET or _MONTH_RE.match(title):
            out.append((title, props["sheetId"], props))
    out.sort(key=lambda x: (x[0] != MONTH_TEMPLATE_SHEET, x[0]), reverse=True)
    return out


def _cell_str(sheets_api, spreadsheet_id: str, a1: str) -> str:
    data = execute_with_retry(
        sheets_api.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=a1),
        label="migrate.read.cell",
    )
    vals = data.get("values") or []
    if not vals or not vals[0]:
        return ""
    return str(vals[0][0] or "").strip()


def _row_values(sheets_api, spreadsheet_id: str, a1: str) -> list[str]:
    data = execute_with_retry(
        sheets_api.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=a1),
        label="migrate.read.row",
    )
    vals = data.get("values") or []
    return [str(x or "").strip() for x in (vals[0] if vals else [])]


def detect_month_migrated(sheets_api, spreadsheet_id: str, title: str) -> bool:
    price_h = _cell_str(
        sheets_api,
        spreadsheet_id,
        f"'{title}'!{col_letter(COL['price'])}{HEADER_ROW}",
    )
    tax_h = _cell_str(
        sheets_api,
        spreadsheet_id,
        f"'{title}'!{col_letter(COL['tax'])}{HEADER_ROW}",
    )
    return month_headers_migrated(price_h, tax_h)


def detect_overview_migrated(sheets_api, spreadsheet_id: str) -> bool:
    end = col_letter(OVERVIEW_NUM_COLS)
    labels = _row_values(
        sheets_api,
        spreadsheet_id,
        f"'{SUMMARY_SHEET}'!A{OVERVIEW_KPI_LABEL_ROW}:{end}{OVERVIEW_KPI_LABEL_ROW}",
    )
    return overview_labels_migrated(labels)


def backup_spreadsheet(
    drive,
    *,
    spreadsheet_id: str,
    title: str,
    folder_id: str,
) -> dict[str, Any]:
    from app.google_clients import (
        drive_service,
        load_operator_oauth_credentials,
        uses_adc_credentials,
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bak_title = f"{title}.bak-price-tax-{stamp}"
    create_drive = drive
    if uses_adc_credentials():
        create_drive = drive_service(load_operator_oauth_credentials())
    copied = execute_with_retry(
        create_drive.files().copy(
            fileId=spreadsheet_id,
            body={"name": bak_title, "parents": [folder_id]},
            fields="id,name,webViewLink",
        ),
        label="migrate.backup.copy",
    )
    return {
        "backup_id": copied["id"],
        "backup_title": copied.get("name") or bak_title,
        "backup_url": copied.get("webViewLink")
        or f"https://docs.google.com/spreadsheets/d/{copied['id']}/edit",
    }


def _unmerge_summary_rows(sheet_id: int, col_count: int) -> list[dict[str, Any]]:
    return [
        {
            "unmergeCells": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": KPI_LABEL_ROW - 1,
                    "endRowIndex": KPI_VALUE_ROW,
                    "startColumnIndex": 0,
                    "endColumnIndex": max(col_count, NUM_COLS),
                }
            }
        }
    ]


def _insert_tax_dimension(sheet_id: int) -> dict[str, Any]:
    return {
        "insertDimension": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": TAX_INSERT_INDEX_0,
                "endIndex": TAX_INSERT_INDEX_0 + TAX_UNITS,
            },
            "inheritFromBefore": False,
        }
    }


def _set_column_count(sheet_id: int, n: int) -> dict[str, Any]:
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"columnCount": n},
            },
            "fields": "gridProperties.columnCount",
        }
    }


def _summary_layout_requests(sheet_id: int) -> list[dict[str, Any]]:
    """Rebuild month KPI merges/styles only (rows 2–3). Does not paint data."""

    def _kpi_style(i: int) -> tuple[dict, dict]:
        if i in (0, 1, 2, 3):
            return SUMMARY_BLACK_BG, WHITE
        if i == 4:
            return SUMMARY_BLUE_BG, WHITE
        if i in (5, 6):
            return SUMMARY_ORANGE_BG, WHITE
        if i in (7, 8):
            return SUMMARY_GREEN_BG, WHITE
        return COUNT_LABEL_BG, BLACK

    reqs: list[dict[str, Any]] = list(_unmerge_summary_rows(sheet_id, NUM_COLS))
    n_kpi = len(MONTH_SUMMARY_MERGES)
    cancel_kpi_i = MONTH_SUMMARY_LABELS.index("キャンセル")
    for i in range(n_kpi):
        c0, c1 = MONTH_SUMMARY_MERGES[i]
        bg, fg = _kpi_style(i)
        borders = _outer_inner_borders(is_first=(i == 0), is_last=(i == n_kpi - 1))
        label = MONTH_SUMMARY_LABELS[i]
        wrap = "WRAP" if "\n" in label else "OVERFLOW_CELL"
        label_pt = (
            SUMMARY_CANCEL_LABEL_FONT_PT if i == cancel_kpi_i else SUMMARY_LABEL_FONT_PT
        )
        if c1 - c0 > 1:
            reqs.append(_merge(sheet_id, KPI_LABEL_ROW - 1, KPI_LABEL_ROW, c0, c1))
            reqs.append(_merge(sheet_id, KPI_VALUE_ROW - 1, KPI_VALUE_ROW, c0, c1))
        reqs.append(
            _paint(
                sheet_id,
                KPI_LABEL_ROW - 1,
                KPI_LABEL_ROW,
                c0,
                c1,
                bg,
                textFormat={"foregroundColor": fg, "bold": True, "fontSize": label_pt},
                horizontalAlignment="CENTER",
                verticalAlignment="MIDDLE",
                borders=borders,
                wrapStrategy=wrap,
            )
        )
        reqs.append(
            _paint(
                sheet_id,
                KPI_VALUE_ROW - 1,
                KPI_VALUE_ROW,
                c0,
                c1,
                WHITE,
                textFormat={
                    "bold": True,
                    "fontSize": SUMMARY_VALUE_FONT_PT,
                    "foregroundColor": BLACK,
                },
                horizontalAlignment="CENTER",
                verticalAlignment="MIDDLE",
                borders=borders,
            )
        )

    def _fmt(col0: int, pattern: str, ntype: str = "NUMBER") -> dict:
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": KPI_VALUE_ROW - 1,
                    "endRowIndex": KPI_VALUE_ROW,
                    "startColumnIndex": col0,
                    "endColumnIndex": col0 + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": ntype, "pattern": pattern}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }

    for i in (0, 1, 2, 3, 4, 5, 6, 7):
        reqs.append(_fmt(MONTH_SUMMARY_MERGES[i][0], "#,##0"))
    reqs.append(_fmt(MONTH_SUMMARY_MERGES[8][0], "0.0%", "PERCENT"))
    for i in (9, 10, 11, 12):
        reqs.append(_fmt(MONTH_SUMMARY_MERGES[i][0], "#,##0"))
    return reqs


def _tax_column_layout_requests(sheet_id: int) -> list[dict[str, Any]]:
    c0, c1 = DETAIL_SPANS["tax"]
    reqs: list[dict[str, Any]] = [
        {
            "unmergeCells": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": HEADER_ROW - 1,
                    "endRowIndex": FORMULA_END_ROW,
                    "startColumnIndex": c0,
                    "endColumnIndex": c1,
                }
            }
        }
    ]
    if c1 - c0 > 1:
        reqs.append(_merge(sheet_id, HEADER_ROW - 1, HEADER_ROW, c0, c1))
        reqs.append(
            _merge(
                sheet_id,
                DATA_START_ROW - 1,
                FORMULA_END_ROW,
                c0,
                c1,
                merge_type="MERGE_ROWS",
            )
        )
    reqs.append(
        _paint(
            sheet_id,
            HEADER_ROW - 1,
            HEADER_ROW,
            c0,
            c1,
            TABLE_HEAD,
            textFormat={
                "foregroundColor": HERO_TEXT,
                "bold": True,
                "fontSize": DETAIL_HEADER_FONT_PT,
            },
            horizontalAlignment="CENTER",
            verticalAlignment="MIDDLE",
        )
    )
    reqs.append(
        _paint(
            sheet_id,
            DATA_START_ROW - 1,
            FORMULA_END_ROW,
            c0,
            c1,
            WHITE,
            horizontalAlignment="RIGHT",
            verticalAlignment="MIDDLE",
            borders=_all_borders(1, HAIR),
            numberFormat={"type": "NUMBER", "pattern": "#,##0"},
            textFormat={"fontSize": DETAIL_FONT_PT},
        )
    )
    reqs.extend(_col_widths(sheet_id, list(MONTH_COL_WIDTHS)))
    return reqs


def _write_month_headers_and_summary(
    sheets_api, spreadsheet_id: str, title: str
) -> None:
    labels, values = month_summary_rows()
    header = [""] * NUM_COLS
    for f in DETAIL_FIELDS:
        header[DETAIL_SPANS[f.key][0]] = f.header
    values_batch_update(
        sheets_api,
        spreadsheet_id,
        [
            {
                "range": f"'{title}'!A{KPI_LABEL_ROW}",
                "values": [labels],
            },
            {
                "range": f"'{title}'!A{KPI_VALUE_ROW}",
                "values": [values],
            },
            {
                "range": f"'{title}'!A{HEADER_ROW}",
                "values": [header],
            },
        ],
        label=f"migrate.month.values.{title}",
    )


def _rewrite_profit_formulas(
    sheets_api, spreadsheet_id: str, title: str
) -> int:
    """Rewrite 利益 / 利益率 on rows that already have an order id."""
    oid_col = col_letter(COL["order_id"])
    data = (
        execute_with_retry(
            sheets_api.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=f"'{title}'!{oid_col}{DATA_START_ROW}:{oid_col}{FORMULA_END_ROW}",
            ),
            label=f"migrate.oids.{title}",
        ).get("values")
        or []
    )
    updates: list[dict[str, Any]] = []
    for i, row in enumerate(data):
        oid = str(row[0]).strip() if row else ""
        if not oid:
            continue
        r = DATA_START_ROW + i
        updates.append(
            {
                "range": f"'{title}'!{col_letter(COL['profit'])}{r}",
                "values": [[row_profit_formula(r)]],
            }
        )
        updates.append(
            {
                "range": f"'{title}'!{col_letter(COL['profit_rate'])}{r}",
                "values": [[row_profit_rate_formula(r)]],
            }
        )
    if updates:
        values_batch_update(
            sheets_api,
            spreadsheet_id,
            updates,
            label=f"migrate.profit.{title}",
        )
    return len(updates) // 2


def migrate_month_sheet(
    sheets_api,
    spreadsheet_id: str,
    *,
    title: str,
    sheet_id: int,
    col_count: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    already = detect_month_migrated(sheets_api, spreadsheet_id, title)
    info: dict[str, Any] = {
        "title": title,
        "already_migrated": already,
        "inserted": False,
        "profit_rows": 0,
    }
    if dry_run:
        return info
    if not already:
        reqs: list[dict[str, Any]] = []
        reqs.extend(_unmerge_summary_rows(sheet_id, col_count))
        reqs.append(_insert_tax_dimension(sheet_id))
        reqs.append(_set_column_count(sheet_id, NUM_COLS))
        batch_update(
            sheets_api,
            spreadsheet_id,
            reqs,
            label=f"migrate.month.insert.{title}",
        )
        info["inserted"] = True
    style_reqs = [_set_column_count(sheet_id, NUM_COLS)]
    style_reqs.extend(_summary_layout_requests(sheet_id))
    style_reqs.extend(_tax_column_layout_requests(sheet_id))
    batch_update(
        sheets_api,
        spreadsheet_id,
        style_reqs,
        chunk_size=8,
        pace_seconds=0.8,
        label=f"migrate.month.style.{title}",
    )
    _write_month_headers_and_summary(sheets_api, spreadsheet_id, title)
    if _MONTH_RE.match(title):
        info["profit_rows"] = _rewrite_profit_formulas(
            sheets_api, spreadsheet_id, title
        )
    return info


def _overview_month_titles(sheets_api, spreadsheet_id: str) -> list[str]:
    start = OVERVIEW_MONTH_DATA_START_ROW
    end = start + OVERVIEW_MONTH_SLOTS - 1
    data = (
        execute_with_retry(
            sheets_api.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=f"'{SUMMARY_SHEET}'!A{start}:A{end}",
            ),
            label="migrate.overview.months",
        ).get("values")
        or []
    )
    titles: list[str] = []
    for row in data:
        raw = str(row[0]).strip() if row else ""
        raw = raw.strip("=").strip('"').strip("'")
        titles.append(raw if _MONTH_RE.match(raw) else "")
    return titles


def migrate_overview(
    sheets_api,
    spreadsheet_id: str,
    *,
    sheet_id: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    already = detect_overview_migrated(sheets_api, spreadsheet_id)
    month_titles = _overview_month_titles(sheets_api, spreadsheet_id)
    info = {
        "already_migrated": already,
        "inserted": False,
        "month_slots": sum(1 for t in month_titles if t),
    }
    if dry_run:
        return info
    if not already:
        batch_update(
            sheets_api,
            spreadsheet_id,
            [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": 2,
                            "endIndex": 3,
                        },
                        "inheritFromBefore": True,
                    }
                },
                _set_column_count(sheet_id, OVERVIEW_NUM_COLS),
            ],
            label="migrate.overview.insert",
        )
        info["inserted"] = True

    def month_row(m: str) -> list[Any]:
        if not m:
            return [""] * OVERVIEW_NUM_COLS
        return [f'="{m}"'] + [
            f"='{m}'!{month_kpi_anchor_a1(i)}" for i in OVERVIEW_METRIC_KPI_INDEX
        ]

    grid_updates = [
        {
            "range": f"'{SUMMARY_SHEET}'!B{OVERVIEW_KPI_LABEL_ROW}",
            "values": [list(OVERVIEW_METRIC_LABELS)],
        },
        {
            "range": f"'{SUMMARY_SHEET}'!A{OVERVIEW_KPI_VALUE_ROW}",
            "values": [annual_sum_formulas()],
        },
        {
            "range": f"'{SUMMARY_SHEET}'!A{OVERVIEW_MONTH_HEADER_ROW}",
            "values": [["月", *OVERVIEW_METRIC_LABELS]],
        },
    ]
    start = OVERVIEW_MONTH_DATA_START_ROW
    filled = [month_row(t) for t in month_titles]
    if filled:
        grid_updates.append(
            {
                "range": f"'{SUMMARY_SHEET}'!A{start}",
                "values": filled,
            }
        )
    values_batch_update(
        sheets_api,
        spreadsheet_id,
        grid_updates,
        label="migrate.overview.values",
    )
    batch_update(
        sheets_api,
        spreadsheet_id,
        summary_style_requests(sheet_id, month_count=info["month_slots"]),
        chunk_size=8,
        pace_seconds=0.6,
        label="migrate.overview.style",
    )
    return info


def collect_price_tax_from_gmail(
    gmail: str, *, max_results: int = 4000
) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    """(order_id, sku) → (販売価格, 税金) from 注文確定 mails. No calculation."""
    creds = load_gmail_credentials(gmail)
    if not creds:
        return {}
    out: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for msg in iter_amazon_mails(
        creds, query=ORDER_MAIL_QUERY, max_results=max_results
    ):
        parsed = parse_eml_bytes(msg.raw_bytes)
        if not parsed or parsed.kind != "order" or not parsed.order_id:
            continue
        oid = parsed.order_id.strip()
        for line in parsed.lines or []:
            sku = normalize_sku(line.sku)
            out[(oid, sku)] = (line.price, line.tax)
    return out


def _index_data_rows(
    sheets_api, spreadsheet_id: str, titles: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    oid_col = col_letter(COL["order_id"])
    sku_col = col_letter(COL["sku"])
    sku_off = COL["sku"] - COL["order_id"]
    for title in titles:
        if not _MONTH_RE.match(title):
            continue
        data = (
            execute_with_retry(
                sheets_api.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{title}'!{oid_col}{DATA_START_ROW}:{sku_col}{FORMULA_END_ROW}",
                ),
                label=f"migrate.index.{title}",
            ).get("values")
            or []
        )
        for i, row in enumerate(data):
            oid = str(row[0]).strip() if row else ""
            if not oid:
                continue
            sku = str(row[sku_off]).strip() if len(row) > sku_off else ""
            rows.append(
                {
                    "month": title,
                    "row": DATA_START_ROW + i,
                    "order_id": oid,
                    "sku": normalize_sku(sku),
                }
            )
    return rows


def backfill_price_tax_from_mail(
    sheets_api,
    spreadsheet_id: str,
    *,
    gmail: str,
    month_titles: list[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    mail_map = collect_price_tax_from_gmail(gmail)
    rows = _index_data_rows(sheets_api, spreadsheet_id, month_titles)
    updates: list[dict[str, Any]] = []
    matched = 0
    unmatched: list[dict[str, str]] = []
    for r in rows:
        key = (r["order_id"], r["sku"])
        found = mail_map.get(key)
        if found is None and r["sku"]:
            # order-only fallback when the mail line has empty sku
            found = mail_map.get((r["order_id"], ""))
        if found is None:
            unmatched.append({"order_id": r["order_id"], "sku": r["sku"], "month": r["month"]})
            continue
        price, tax = found
        chunk = price_tax_updates_for_row(r["month"], r["row"], price, tax)
        if chunk:
            updates.extend(chunk)
            matched += 1
    if updates and not dry_run:
        values_batch_update(
            sheets_api,
            spreadsheet_id,
            updates,
            label="migrate.backfill.price_tax",
        )
    return {
        "gmail": gmail,
        "mail_keys": len(mail_map),
        "sheet_rows": len(rows),
        "matched": matched,
        "unmatched": len(unmatched),
        "unmatched_sample": unmatched[:20],
        "wrote": 0 if dry_run else len(updates),
    }


def migrate_spreadsheet(
    sheets_api,
    drive,
    *,
    spreadsheet_id: str,
    title: str,
    folder_id: str,
    gmail: str | None,
    role: str | None,
    is_template: bool,
    dry_run: bool = False,
    skip_backfill: bool = False,
    do_backup: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": title,
        "spreadsheet_id": spreadsheet_id,
        "is_template": is_template,
        "dry_run": dry_run,
    }
    if do_backup and not dry_run:
        result["backup"] = backup_spreadsheet(
            drive,
            spreadsheet_id=spreadsheet_id,
            title=title,
            folder_id=folder_id,
        )
    elif do_backup:
        result["backup"] = {"skipped": "dry_run"}

    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title,gridProperties))",
        ),
        label="migrate.meta",
    )
    months = _month_and_template_sheets(meta)
    month_infos = []
    for mt, sid, props in months:
        gp = props.get("gridProperties") or {}
        month_infos.append(
            migrate_month_sheet(
                sheets_api,
                spreadsheet_id,
                title=mt,
                sheet_id=sid,
                col_count=int(gp.get("columnCount") or NUM_COLS),
                dry_run=dry_run,
            )
        )
    result["months"] = month_infos

    ov = next(
        (
            s["properties"]
            for s in meta.get("sheets", [])
            if s["properties"]["title"] == SUMMARY_SHEET
        ),
        None,
    )
    if ov:
        result["overview"] = migrate_overview(
            sheets_api,
            spreadsheet_id,
            sheet_id=ov["sheetId"],
            dry_run=dry_run,
        )

    if not dry_run:
        try:
            from app.apps_script_deploy import ensure_cancel_onedit_script

            result["apps_script"] = ensure_cancel_onedit_script(spreadsheet_id)
        except Exception as exc:  # noqa: BLE001
            result["apps_script_error"] = str(exc)
    if not dry_run and not is_template and role is not None:
        try:
            from app.sheet_protection import apply_protections

            apply_protections(sheets_api, spreadsheet_id, role=role)
            result["protections"] = True
        except Exception as exc:  # noqa: BLE001
            result["protections_error"] = str(exc)
        if gmail:
            try:
                touch_last_auto_update(
                    sheets_api,
                    spreadsheet_id,
                    gmail=gmail,
                    year=int(_TITLE_RE.match(title).group(2))  # type: ignore[union-attr]
                    if _TITLE_RE.match(title)
                    else datetime.now().year,
                )
            except Exception:
                pass

    if (
        not skip_backfill
        and gmail
        and not is_template
    ):
        result["backfill"] = backfill_price_tax_from_mail(
            sheets_api,
            spreadsheet_id,
            gmail=gmail,
            month_titles=[t for t, _, _ in months],
            dry_run=dry_run,
        )
    return result


def list_migration_targets(drive, folder_id: str, template_id: str | None) -> list[dict[str, Any]]:
    from app.clipping_roster import list_active_users

    try:
        roles = {u["user_id"]: u["role"] for u in list_active_users()}
    except Exception:
        roles = {}
    resp = execute_with_retry(
        drive.files().list(
            q=(
                f"'{folder_id}' in parents and "
                "mimeType = 'application/vnd.google-apps.spreadsheet' and "
                "trashed = false"
            ),
            fields="files(id,name)",
            pageSize=200,
            orderBy="name",
        ),
        label="migrate.list",
    )
    targets: list[dict[str, Any]] = []
    for f in resp.get("files") or []:
        name = f.get("name") or ""
        sid = f.get("id") or ""
        is_tmpl = name == TEMPLATE_SPREADSHEET_TITLE or (
            bool(template_id) and sid == template_id
        )
        if name.endswith(".bak-price-tax") or ".bak-price-tax-" in name:
            continue
        uid, year = None, None
        m = _TITLE_RE.match(name)
        if m:
            uid, year = m.group(1), int(m.group(2))
        if not is_tmpl and not m:
            continue
        targets.append(
            {
                "title": name,
                "spreadsheet_id": sid,
                "is_template": is_tmpl,
                "user_id": uid,
                "year": year,
                "gmail": gmail_from_user_id(uid) if uid else None,
                "role": roles.get(uid) if uid else None,
                "do_backup": (uid == BACKUP_USER_ID),
            }
        )
    targets.sort(key=lambda r: (not r["is_template"], r["title"].lower()))
    return targets
