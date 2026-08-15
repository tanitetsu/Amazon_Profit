"""Apply parsed Amazon mails to yearly profit workbooks (APPEND / status)."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.buyer_cancel import checkbox_data_validation_requests, lock_cancel_checkbox
from app.gmail_fetch import iter_amazon_mails
from app.gmail_oauth import load_gmail_credentials
from app.google_clients import (
    SECRETS,
    copy_spreadsheet_in_folder,
    drive_service,
    find_spreadsheet_in_folder,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.mail_parser import ParsedMail, parse_eml_bytes
from app.mercari import fetch_mercari_price
from app.order_sku import normalize_sku
from app.schema import (
    COL,
    DATA_START_ROW,
    DETAIL_FIELDS,
    DETAIL_SPANS,
    FORMULA_END_ROW,
    NUM_COLS,
    STATUS_BUYER_CANCEL,
    STATUS_FONT_PT,
    STATUS_OPEN,
    STATUS_RETURN,
    STATUS_RETURN_FONT_PT,
    col_letter,
    points_fallback_from_price_tax,
    spreadsheet_title_from_gmail,
    user_id_from_gmail,
)
from app.sheet_builder import (
    linked_order_and_title,
    row_profit_formula,
    row_profit_rate_formula,
)
from app.sheet_links import apply_rich_links, order_title_rich_links
from app.sheets_retry import (
    batch_update,
    call_with_retry,
    execute_with_retry,
    values_batch_update,
)
from app.template_ops import ensure_months_for_order, next_data_row, touch_last_auto_update
from app.users_store import load_users_config

SEEN_DIR = SECRETS / "gmail_seen"
_DATE_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")


def _seen_path(gmail: str) -> Path:
    return SEEN_DIR / f"{user_id_from_gmail(gmail)}.json"


def _app_config_gcs_uri() -> str:
    return (
        (os.environ.get("APP_CONFIG_GCS_URI") or "").strip()
        or (os.environ.get("USERS_CONFIG_GCS_URI") or "").strip()
    )


def _seen_gcs_prefix() -> str | None:
    """gs://bucket/gmail_seen — same bucket as tokens unless GMAIL_SEEN_GCS_PREFIX set."""
    explicit = (os.environ.get("GMAIL_SEEN_GCS_PREFIX") or "").strip().rstrip("/")
    if explicit:
        return explicit
    users_uri = _app_config_gcs_uri()
    if users_uri.startswith("gs://"):
        bucket = users_uri[5:].split("/", 1)[0]
        return f"gs://{bucket}/gmail_seen"
    return None


def _seen_gcs_uri(gmail: str) -> str | None:
    prefix = _seen_gcs_prefix()
    if not prefix:
        return None
    return f"{prefix}/{user_id_from_gmail(gmail)}.json"


def _gcs_blob(uri: str):
    from app.gcs_credentials import gcs_storage_client

    assert uri.startswith("gs://")
    rest = uri[5:]
    bucket_name, _, blob_name = rest.partition("/")
    return gcs_storage_client().bucket(bucket_name).blob(blob_name)


def _parse_seen_payload(text: str) -> set[str]:
    try:
        data = json.loads(text)
        return set(data.get("ids") or [])
    except Exception:
        return set()


def load_seen_ids(gmail: str) -> set[str]:
    """
    Processed Gmail message ids. On Cloud Run, persist under GCS (alongside
    gmail_tokens) so cold starts do not re-fetch raw mail every poll.
    """
    uri = _seen_gcs_uri(gmail)
    if uri:
        blob = _gcs_blob(uri)
        if call_with_retry(blob.exists, label="gmail_seen.exists"):
            text = call_with_retry(
                lambda: blob.download_as_text(encoding="utf-8"),
                label="gmail_seen.download",
            )
            return _parse_seen_payload(text)
        return set()
    path = _seen_path(gmail)
    if not path.is_file():
        return set()
    try:
        return _parse_seen_payload(path.read_text(encoding="utf-8"))
    except Exception:
        return set()


def save_seen_ids(gmail: str, ids: set[str], *, keep: int = 8000) -> None:
    # Keep newest-ish by truncating arbitrary surplus (ids are opaque).
    trimmed = list(ids)
    if len(trimmed) > keep:
        trimmed = trimmed[-keep:]
    payload = json.dumps({"ids": trimmed}, ensure_ascii=False)
    uri = _seen_gcs_uri(gmail)
    if uri:
        call_with_retry(
            lambda: _gcs_blob(uri).upload_from_string(
                payload, content_type="application/json; charset=utf-8"
            ),
            label="gmail_seen.upload",
        )
        return
    SEEN_DIR.mkdir(parents=True, exist_ok=True)
    _seen_path(gmail).write_text(payload, encoding="utf-8")


def clear_seen_ids(gmail: str) -> bool:
    """Remove ingest dedupe state so the user is fully unlinked from mail jobs."""
    uri = _seen_gcs_uri(gmail)
    if uri:
        blob = _gcs_blob(uri)

        def _delete() -> bool:
            if not blob.exists():
                return False
            blob.delete()
            return True

        return bool(call_with_retry(_delete, label="gmail_seen.delete"))
    path = _seen_path(gmail)
    if path.is_file():
        path.unlink()
        return True
    return False


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _ensure_year_workbook(
    drive,
    sheets_api,
    *,
    gmail: str,
    year: int,
    folder_id: str,
) -> str:
    """Find or create yearly book (template copy + month tab)."""
    from app.template_ops import (
        hide_month_template_sheet,
        month_title,
        resolve_template_spreadsheet_id,
    )

    title = spreadsheet_title_from_gmail(gmail, year)
    existing = find_spreadsheet_in_folder(drive, title, folder_id)
    if existing:
        return existing

    cfg = load_users_config()
    template_id = resolve_template_spreadsheet_id(drive, cfg)
    spreadsheet_id = copy_spreadsheet_in_folder(drive, template_id, title, folder_id)
    hide_month_template_sheet(sheets_api, spreadsheet_id)
    seed_month = (
        month_title(year, date.today().month)
        if year == date.today().year
        else f"{year:04d}-01"
    )
    ensure_months_for_order(
        sheets_api, spreadsheet_id, seed_month, gmail=gmail, year=year
    )
    return spreadsheet_id


def _sheet_meta(sheets_api, spreadsheet_id: str) -> dict[str, int]:
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(spreadsheetId=spreadsheet_id),
        label="ingest.meta",
    )
    return {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets", [])
    }


def _order_row_values(
    *,
    order_id: str,
    sku: str,
    title: str,
    order_date: date | None,
    ship_by: date | None,
    price: int | None,
    tax: int | None,
    fee: int | None,
    points: int | None,
    proceeds: int | None,
    cost: int | None,
    sheet_row: int,
) -> list[Any]:
    vals: list[Any] = [""] * NUM_COLS
    oid, title_disp = linked_order_and_title(order_id, title, sku)
    vals[COL["order_id"] - 1] = oid
    vals[COL["sku"] - 1] = sku
    vals[COL["title"] - 1] = title_disp
    vals[COL["order_date"] - 1] = order_date.isoformat() if order_date else ""
    vals[COL["ship_by"] - 1] = ship_by.isoformat() if ship_by else ""
    vals[COL["status"] - 1] = STATUS_OPEN
    vals[COL["price"] - 1] = price
    vals[COL["tax"] - 1] = tax
    vals[COL["fee"] - 1] = fee
    vals[COL["points"] - 1] = points
    vals[COL["proceeds"] - 1] = proceeds
    vals[COL["cost"] - 1] = cost if cost is not None else ""
    vals[COL["extra_cost"] - 1] = ""
    vals[COL["profit"] - 1] = row_profit_formula(sheet_row)
    vals[COL["profit_rate"] - 1] = row_profit_rate_formula(sheet_row)
    vals[COL["ship_date"] - 1] = ""
    vals[COL["cost_done"] - 1] = False
    vals[COL["shipped"] - 1] = False
    vals[COL["cancel"] - 1] = False
    vals[COL["done"] - 1] = False
    vals[COL["comment"] - 1] = ""
    return vals


def _is_blank_cell(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def should_mark_mail_seen(result: dict[str, Any]) -> bool:
    """False when apply asks to retry on a later poll (e.g. status before order row)."""
    return not bool(result.get("defer_seen"))


def _decorate_order_row(
    sheets_api,
    spreadsheet_id: str,
    sheet_id: int,
    *,
    month: str,
    row_1: int,
    order_id: str,
    title: str,
    sku: str,
) -> None:
    """Idempotent ☑ validation + rich links for one detail row."""
    batch_update(
        sheets_api,
        spreadsheet_id,
        checkbox_data_validation_requests(sheet_id, row_1, row_1),
        label=f"ingest.checkbox.{month}.r{row_1}",
    )
    apply_rich_links(
        sheets_api,
        spreadsheet_id,
        sheet_id,
        order_title_rich_links(
            [(row_1, order_id, title, sku)],
            order_col_0=DETAIL_SPANS["order_id"][0],
            title_col_0=DETAIL_SPANS["title"][0],
        ),
        label=f"ingest.links.{month}.r{row_1}",
    )


def _read_detail_row(
    sheets_api, spreadsheet_id: str, month: str, row_1: int
) -> list[Any]:
    end = col_letter(NUM_COLS)
    data = (
        execute_with_retry(
            sheets_api.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=f"'{month}'!A{row_1}:{end}{row_1}",
                valueRenderOption="FORMULA",
            ),
            label=f"ingest.readRow.{month}.r{row_1}",
        ).get("values")
        or []
    )
    row = list(data[0]) if data else []
    if len(row) < NUM_COLS:
        row.extend([""] * (NUM_COLS - len(row)))
    return row


def _ensure_existing_order_row(
    sheets_api,
    spreadsheet_id: str,
    *,
    month: str,
    sheet_id: int,
    row_1: int,
    order_id: str,
    sku: str,
    title: str,
    order_date: date | None,
    ship_by: date | None,
    price: int | None,
    tax: int | None,
    fee: int | None,
    points: int | None,
    proceeds: int | None,
) -> dict[str, int]:
    """
    Fill blank auto cells from this mail, restore profit formulas if missing,
    then re-apply ☑ + links. Never overwrites non-blank auto values or manual cols.
    """
    current = _read_detail_row(sheets_api, spreadsheet_id, month, row_1)
    desired = _order_row_values(
        order_id=order_id,
        sku=sku,
        title=title,
        order_date=order_date,
        ship_by=ship_by,
        price=price,
        tax=tax,
        fee=fee,
        points=points,
        proceeds=proceeds,
        cost=None,
        sheet_row=row_1,
    )
    # Editable / checkbox / comment / cost: never bulk-copy; cost via mercari below.
    skip_keys = {
        "extra_cost",
        "ship_date",
        "cost_done",
        "shipped",
        "cancel",
        "done",
        "comment",
        "cost",
    }
    updates: list[dict[str, Any]] = []
    filled = 0
    for f in DETAIL_FIELDS:
        key = f.key
        if key in skip_keys:
            continue
        idx = COL[key] - 1
        if not _is_blank_cell(current[idx]):
            continue
        new_v = desired[idx]
        if key == "status":
            new_v = STATUS_OPEN
        if _is_blank_cell(new_v) and key not in ("profit", "profit_rate"):
            continue
        letter = col_letter(COL[key])
        updates.append(
            {"range": f"'{month}'!{letter}{row_1}", "values": [[new_v]]}
        )
        filled += 1

    # 仕入金: blank only → mercari (same as first APPEND).
    cost_idx = COL["cost"] - 1
    if _is_blank_cell(current[cost_idx]):
        cost = fetch_mercari_price(sku)
        if cost is not None:
            letter = col_letter(COL["cost"])
            updates.append(
                {"range": f"'{month}'!{letter}{row_1}", "values": [[cost]]}
            )
            filled += 1

    if updates:
        values_batch_update(
            sheets_api,
            spreadsheet_id,
            updates,
            label=f"ingest.fill.{month}.r{row_1}",
        )

    _decorate_order_row(
        sheets_api,
        spreadsheet_id,
        sheet_id,
        month=month,
        row_1=row_1,
        order_id=order_id,
        title=title,
        sku=sku,
    )
    return {"filled": filled}


def _append_order_lines(
    sheets_api,
    drive,
    *,
    gmail: str,
    folder_id: str,
    parsed: ParsedMail,
    operator_email: str | None,
) -> dict[str, Any]:
    order_date = _parse_date(parsed.order_date) or date.today()
    ship_by = _parse_date(parsed.ship_by)
    year = order_date.year
    month = _month_key(order_date)
    spreadsheet_id = _ensure_year_workbook(
        drive, sheets_api, gmail=gmail, year=year, folder_id=folder_id
    )
    ensure_months_for_order(
        sheets_api, spreadsheet_id, month, gmail=gmail, year=year
    )
    titles = _sheet_meta(sheets_api, spreadsheet_id)
    sheet_id = titles.get(month)
    if sheet_id is None:
        raise RuntimeError(f"month sheet missing after ensure: {month}")

    lines = parsed.lines or []
    if not lines:
        return {"action": "order", "appended": 0, "reason": "no_lines"}

    existing = _index_order_rows(sheets_api, spreadsheet_id, titles)
    appended = 0
    ensured = 0
    filled_cells = 0

    for line in lines:
        sku = normalize_sku(line.sku)
        oid = (parsed.order_id or "").strip()
        price = line.price
        tax = line.tax
        points = line.points
        if points is None:
            points = points_fallback_from_price_tax(price, tax)
        title = line.title or ""

        match = next(
            (
                r
                for r in existing
                if oid and r["order_id"] == oid and r["sku"] == sku
            ),
            None,
        )
        if match:
            # Retry path: complete ☑/links and fill blanks left by partial writes.
            stats = _ensure_existing_order_row(
                sheets_api,
                spreadsheet_id,
                month=match["month"],
                sheet_id=match["sheet_id"],
                row_1=match["row"],
                order_id=oid,
                sku=sku,
                title=title,
                order_date=order_date,
                ship_by=ship_by,
                price=price,
                tax=tax,
                fee=line.fee,
                points=points,
                proceeds=line.proceeds,
            )
            ensured += 1
            filled_cells += stats["filled"]
            continue

        cost = fetch_mercari_price(sku)
        row_1 = next_data_row(sheets_api, spreadsheet_id, month)
        values = _order_row_values(
            order_id=oid,
            sku=sku,
            title=title,
            order_date=order_date,
            ship_by=ship_by,
            price=price,
            tax=tax,
            fee=line.fee,
            points=points,
            proceeds=line.proceeds,
            cost=cost,
            sheet_row=row_1,
        )
        values_batch_update(
            sheets_api,
            spreadsheet_id,
            [{"range": f"'{month}'!A{row_1}", "values": [values]}],
            label=f"ingest.append.{month}.r{row_1}",
        )
        # Finish each row before the next so a mid-mail failure still leaves
        # completed rows with ☑ + links (retry only fills remaining lines).
        _decorate_order_row(
            sheets_api,
            spreadsheet_id,
            sheet_id,
            month=month,
            row_1=row_1,
            order_id=oid,
            title=title,
            sku=sku,
        )
        existing.append(
            {
                "month": month,
                "sheet_id": sheet_id,
                "row": row_1,
                "order_id": oid,
                "sku": sku,
            }
        )
        appended += 1

    touch_last_auto_update(sheets_api, spreadsheet_id, gmail=gmail, year=year)
    return {
        "action": "order",
        "order_id": parsed.order_id,
        "month": month,
        "year": year,
        "appended": appended,
        "ensured": ensured,
        "filled_cells": filled_cells,
        "spreadsheet_id": spreadsheet_id,
    }


def _sku_index_in_oid_to_sku_row() -> int:
    """0-based index of SKU within values range A(order_id)..U(sku).

    Order-id field is merged across many unit columns, so row[1] is empty;
    SKU lives at COL['sku'] - COL['order_id'].
    """
    return COL["sku"] - COL["order_id"]


def _order_sku_from_index_row(row: list[Any] | None) -> tuple[str, str]:
    """Extract (order_id, sku) from one values API row spanning order_id..sku."""
    if not row:
        return "", ""
    oid = str(row[0]).strip() if row else ""
    sku_i = _sku_index_in_oid_to_sku_row()
    sku = str(row[sku_i]).strip() if len(row) > sku_i else ""
    return oid, sku


def _index_order_rows(
    sheets_api, spreadsheet_id: str, titles: dict[str, int]
) -> list[dict[str, Any]]:
    """Scan YYYY-MM sheets for order_id + sku."""
    out: list[dict[str, Any]] = []
    oid_col = col_letter(COL["order_id"])
    sku_col = col_letter(COL["sku"])
    for title, sid in titles.items():
        if not re.fullmatch(r"\d{4}-\d{2}", title):
            continue
        data = (
            execute_with_retry(
                sheets_api.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{title}'!{oid_col}{DATA_START_ROW}:{sku_col}{FORMULA_END_ROW}",
                ),
                label=f"ingest.index.{title}",
            ).get("values")
            or []
        )
        for i, row in enumerate(data):
            oid, sku = _order_sku_from_index_row(row)
            if not oid:
                continue
            out.append(
                {
                    "month": title,
                    "sheet_id": sid,
                    "row": DATA_START_ROW + i,
                    "order_id": oid,
                    "sku": sku,
                }
            )
    return out


def _status_font_pt(status: str) -> int:
    return STATUS_RETURN_FONT_PT if status == STATUS_RETURN else STATUS_FONT_PT


def _apply_status_mail(
    sheets_api,
    drive,
    *,
    gmail: str,
    folder_id: str,
    parsed: ParsedMail,
    status: str,
    operator_email: str | None,
) -> dict[str, Any]:
    oid = (parsed.order_id or "").strip()
    if not oid:
        return {"action": "status", "status": status, "updated": 0, "reason": "no_order_id"}

    sku_filter = (parsed.sku or "").strip() or None
    # Search current year and ±1 around today (covers late cancels).
    years = sorted({date.today().year, date.today().year - 1})
    updated = 0
    touched: list[str] = []

    for year in years:
        title = spreadsheet_title_from_gmail(gmail, year)
        spreadsheet_id = find_spreadsheet_in_folder(drive, title, folder_id)
        if not spreadsheet_id:
            continue
        titles = _sheet_meta(sheets_api, spreadsheet_id)
        rows = _index_order_rows(sheets_api, spreadsheet_id, titles)
        targets = [
            r
            for r in rows
            if r["order_id"] == oid and (sku_filter is None or r["sku"] == sku_filter)
        ]
        if not targets:
            continue

        # Group by month for value writes + locks
        by_month: dict[str, list[dict[str, Any]]] = {}
        for r in targets:
            by_month.setdefault(r["month"], []).append(r)

        status_col = col_letter(COL["status"])
        for month, mrows in by_month.items():
            sheet_id = mrows[0]["sheet_id"]
            updates = [
                {
                    "range": f"'{month}'!{status_col}{r['row']}",
                    "values": [[status]],
                }
                for r in mrows
            ]
            values_batch_update(
                sheets_api,
                spreadsheet_id,
                updates,
                label=f"ingest.status.{month}",
            )
            # Font size for 返品 vs others
            font_reqs = []
            for r in mrows:
                font_reqs.append(
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": r["row"] - 1,
                                "endRowIndex": r["row"],
                                "startColumnIndex": DETAIL_SPANS["status"][0],
                                "endColumnIndex": DETAIL_SPANS["status"][1],
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {
                                        "fontSize": _status_font_pt(status),
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.textFormat.fontSize",
                        }
                    }
                )
            if font_reqs:
                batch_update(
                    sheets_api,
                    spreadsheet_id,
                    font_reqs,
                    label=f"ingest.statusFont.{month}",
                )
            lock_cancel_checkbox(
                sheets_api,
                spreadsheet_id,
                sheet_id,
                month,
                [r["row"] for r in mrows],
                operator_email=operator_email,
            )
            updated += len(mrows)

        touch_last_auto_update(sheets_api, spreadsheet_id, gmail=gmail, year=year)
        touched.append(spreadsheet_id)

    # No matching order row yet (order mail failed / not ingested) → do not mark
    # seen so a later poll can apply ×/返品 after the row appears.
    return {
        "action": "status",
        "status": status,
        "order_id": oid,
        "sku": sku_filter,
        "updated": updated,
        "spreadsheets": touched,
        "defer_seen": updated == 0,
        "reason": "no_matching_row" if updated == 0 else None,
    }


def apply_parsed_mail(
    sheets_api,
    drive,
    *,
    gmail: str,
    folder_id: str,
    parsed: ParsedMail,
    operator_email: str | None = None,
) -> dict[str, Any]:
    kind = parsed.kind
    if kind == "order":
        return _append_order_lines(
            sheets_api,
            drive,
            gmail=gmail,
            folder_id=folder_id,
            parsed=parsed,
            operator_email=operator_email,
        )
    if kind == "cancel_request":
        return _apply_status_mail(
            sheets_api,
            drive,
            gmail=gmail,
            folder_id=folder_id,
            parsed=parsed,
            status=STATUS_BUYER_CANCEL,
            operator_email=operator_email,
        )
    if kind in ("return_approved", "atoz"):
        return _apply_status_mail(
            sheets_api,
            drive,
            gmail=gmail,
            folder_id=folder_id,
            parsed=parsed,
            status=STATUS_RETURN,
            operator_email=operator_email,
        )
    return {"action": "skip", "reason": f"unknown_kind:{kind}"}


def _env_positive_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def ingest_max_fetch_per_poll() -> int:
    """Unseen messages to download/apply per user per poll (bounded catch-up)."""
    return _env_positive_int("MAIL_INGEST_MAX_PER_POLL", 25)


def ingest_budget_sec() -> float:
    """Soft wall-clock budget for one user's ingest inside a poll request."""
    return float(_env_positive_int("MAIL_INGEST_BUDGET_SEC", 480))


def ingest_user_mail(
    gmail: str,
    *,
    max_results: int = 1000,
    max_fetch: int | None = None,
    budget_sec: float | None = None,
) -> dict[str, Any]:
    """
    Pull Amazon mails for gmail (must have stored OAuth token) and write sheets
    using the operator Drive/Sheets credentials.

    Default max_results=1000 is for initial / manual ingest. Polling passes a
    smaller max_results (typically 100) and caps ``max_fetch`` so each tick
    finishes inside Cloud Run's request timeout.
    """
    gmail = gmail.strip()
    creds = load_gmail_credentials(gmail)
    if not creds:
        raise RuntimeError(f"Gmail not linked for {gmail}")

    op = load_operator_credentials()
    drive = drive_service(op)
    sheets_api = sheets_service(op)
    cfg = load_users_config()
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    operator_email = (cfg.get("operator_drive_email") or "").strip() or None

    seen = load_seen_ids(gmail)
    results: list[dict[str, Any]] = []
    processed = 0
    skipped_seen = 0
    parse_miss = 0
    truncated = False
    fetch_cap = ingest_max_fetch_per_poll() if max_fetch is None else max(0, int(max_fetch))
    budget = ingest_budget_sec() if budget_sec is None else max(0.0, float(budget_sec))
    deadline = time.monotonic() + budget if budget > 0 else None

    for msg in iter_amazon_mails(
        creds,
        max_results=max_results,
        max_fetch=fetch_cap,
        skip_ids=seen,
    ):
        if deadline is not None and time.monotonic() >= deadline:
            truncated = True
            break
        if msg.id in seen:
            skipped_seen += 1
            continue
        parsed = parse_eml_bytes(msg.raw_bytes)
        if not parsed:
            parse_miss += 1
            seen.add(msg.id)
            save_seen_ids(gmail, seen)
            continue
        try:
            result = apply_parsed_mail(
                sheets_api,
                drive,
                gmail=gmail,
                folder_id=folder_id,
                parsed=parsed,
                operator_email=operator_email,
            )
            result["message_id"] = msg.id
            result["subject"] = parsed.subject
            results.append(result)
            processed += 1
            # Mark seen only when apply fully settled. Exceptions and defer_seen
            # (status with no row yet) retry on the next poll.
            if should_mark_mail_seen(result):
                seen.add(msg.id)
                # Persist incrementally so Cloud Run timeout/OOM does not replay
                # the whole max_results window on the next tick.
                save_seen_ids(gmail, seen)
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "message_id": msg.id,
                    "subject": parsed.subject,
                    "error": str(exc),
                }
            )

    save_seen_ids(gmail, seen)
    return {
        "gmail": gmail,
        "processed": processed,
        "parse_miss": parse_miss,
        "skipped_seen": skipped_seen,
        "truncated": truncated,
        "max_fetch": fetch_cap,
        "results": results,
        "synced_at": datetime.now().isoformat(timespec="seconds"),
    }
