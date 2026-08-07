"""Delete live yearly book, recreate from Excel with links."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.amazon_order import seller_order_detail_url  # noqa: E402
from app.excel_import import import_legacy_excel_to_workbook  # noqa: E402
from app.google_clients import (  # noqa: E402
    drive_service,
    find_spreadsheet_in_folder,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.legacy_excel import load_legacy_orders  # noqa: E402
from app.mercari import mercari_item_url  # noqa: E402
from app.schema import COL, col_letter, spreadsheet_title_from_gmail  # noqa: E402
from app.sheets_retry import execute_with_retry  # noqa: E402
from app.clipping_roster import upsert_clipping_user  # noqa: E402
from app.users_store import load_users_config  # noqa: E402

OLD_ID = "1uvx1PwTXueF1HY4J1Cv_IlKulqCjyvSE-UtP4oCFb5E"
GMAIL = "asamiodaka@gmail.com"
YEAR = 2026
EXCEL = Path(r"e:\DownLoad\Amazon利益管理シート①.xlsx")


def main() -> int:
    if not EXCEL.exists():
        print(f"missing excel: {EXCEL}", file=sys.stderr)
        return 1

    rows = load_legacy_orders(EXCEL)
    mercari_n = sum(1 for r in rows if mercari_item_url(r.sku))
    order_n = sum(1 for r in rows if seller_order_detail_url(r.order_id))
    print(f"excel rows={len(rows)} mercari_links={mercari_n} order_links={order_n}", flush=True)

    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    title = spreadsheet_title_from_gmail(GMAIL, YEAR)

    # Delete any existing yearly book (known id + title match)
    for fid in {OLD_ID, find_spreadsheet_in_folder(drive, title, folder_id)}:
        if not fid:
            continue
        try:
            drive.files().delete(fileId=fid).execute()
            print(f"deleted {fid}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"delete skip {fid}: {exc}", flush=True)

    result = import_legacy_excel_to_workbook(
        sheets,
        drive,
        gmail=GMAIL,
        excel_path=str(EXCEL),
        year=YEAR,
        folder_id=folder_id,
        rebuild=True,
        share_with_user=True,
    )
    upsert_clipping_user(GMAIL, "Normal")
    sid = result["spreadsheet_id"]

    title_a1 = f"{col_letter(COL['title'])}6"
    sample = execute_with_retry(
        sheets.spreadsheets().get(
            spreadsheetId=sid,
            ranges=[f"'2026-04'!A6", f"'2026-04'!{title_a1}", "'Overview'!A2"],
            includeGridData=True,
            fields=(
                "sheets.data.rowData.values("
                "userEnteredValue,formattedValue,userEnteredFormat(textFormat))"
            ),
        ),
        label="sample",
    )
    checks = []
    for i, label in enumerate(["orderA6", "titleSample", "overviewA2"]):
        data = sample["sheets"][0]["data"][i]
        cell = ((data.get("rowData") or [{}])[0].get("values") or [{}])[0]
        checks.append(
            {
                label: {
                    "user": cell.get("userEnteredValue"),
                    "fmt": cell.get("formattedValue"),
                    "font": (cell.get("userEnteredFormat") or {})
                    .get("textFormat", {})
                    .get("fontSize"),
                }
            }
        )
    result["checks"] = checks
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
