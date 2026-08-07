"""Fill empty 仕入金 from Mercari items/get (skip rows that already have a number)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

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
from app.mercari import fetch_mercari_price, mercari_item_id_from_sku  # noqa: E402
from app.schema import (  # noqa: E402
    COL,
    DATA_START_ROW,
    FORMULA_END_ROW,
    col_letter,
    spreadsheet_title_from_gmail,
)
from app.sheets_retry import execute_with_retry, values_batch_update  # noqa: E402
from app.users_store import load_users_config  # noqa: E402

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _is_blank_cost(raw: Any) -> bool:
    if raw is None:
        return True
    s = str(raw).strip()
    if not s:
        return True
    try:
        return float(s.replace(",", "")) == 0
    except ValueError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gmail", default="asamiodaka.b@gmail.com")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--pace-sec", type=float, default=0.35)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    title = spreadsheet_title_from_gmail(args.gmail, args.year)
    sid = find_spreadsheet_in_folder(drive, title, folder_id)
    if not sid:
        print(f"missing workbook: {title}", file=sys.stderr)
        return 1

    meta = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=sid, fields="sheets.properties(title)"
        ),
        label="fillcost.meta",
    )
    months = [
        s["properties"]["title"]
        for s in meta.get("sheets", [])
        if _MONTH_RE.match(s["properties"]["title"])
    ]
    months.sort(reverse=True)

    sku_col = col_letter(COL["sku"])
    cost_col = col_letter(COL["cost"])
    stats = {
        "months": len(months),
        "rows_scanned": 0,
        "already_filled": 0,
        "non_mercari_sku": 0,
        "fetched": 0,
        "written": 0,
        "api_miss": 0,
        "dry_run": bool(args.dry_run),
    }
    samples: list[dict[str, Any]] = []

    for month in months:
        sku_vals = (
            execute_with_retry(
                sheets.spreadsheets()
                .values()
                .get(
                    spreadsheetId=sid,
                    range=f"'{month}'!{sku_col}{DATA_START_ROW}:{sku_col}{FORMULA_END_ROW}",
                ),
                label=f"fillcost.sku.{month}",
            ).get("values")
            or []
        )
        cost_vals = (
            execute_with_retry(
                sheets.spreadsheets()
                .values()
                .get(
                    spreadsheetId=sid,
                    range=f"'{month}'!{cost_col}{DATA_START_ROW}:{cost_col}{FORMULA_END_ROW}",
                ),
                label=f"fillcost.cost.{month}",
            ).get("values")
            or []
        )
        n = max(len(sku_vals), len(cost_vals))
        updates: list[dict[str, Any]] = []
        for i in range(n):
            sku = str(sku_vals[i][0]).strip() if i < len(sku_vals) and sku_vals[i] else ""
            if not sku:
                continue
            stats["rows_scanned"] += 1
            row_1 = DATA_START_ROW + i
            cost_raw = (
                cost_vals[i][0] if i < len(cost_vals) and cost_vals[i] else ""
            )
            if not _is_blank_cost(cost_raw):
                stats["already_filled"] += 1
                continue
            if mercari_item_id_from_sku(sku) is None:
                stats["non_mercari_sku"] += 1
                continue
            price = fetch_mercari_price(sku)
            time.sleep(max(0.0, args.pace_sec))
            if price is None:
                stats["api_miss"] += 1
                continue
            stats["fetched"] += 1
            if len(samples) < 8:
                samples.append({"month": month, "row": row_1, "sku": sku, "price": price})
            if args.dry_run:
                continue
            updates.append(
                {
                    "range": f"'{month}'!{cost_col}{row_1}",
                    "values": [[price]],
                }
            )
        if updates:
            # chunk writes
            chunk = 80
            for j in range(0, len(updates), chunk):
                values_batch_update(
                    sheets,
                    sid,
                    updates[j : j + chunk],
                    label=f"fillcost.write.{month}.{j}",
                )
            stats["written"] += len(updates)
        print(
            f"{month}: scanned updates={len(updates)}",
            flush=True,
        )

    print(
        json.dumps(
            {"title": title, "spreadsheet_id": sid, "stats": stats, "samples": samples},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
