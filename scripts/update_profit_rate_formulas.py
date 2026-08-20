"""Rewrite detail-row 利益率 on template + all yearly workbooks in User_Acounting.

Detail: 利益 / 仕入金  (blank when 仕入金 is empty or 0).
Does not change month-summary or dashboard 利益率.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import (  # noqa: E402
    drive_service,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.schema import (  # noqa: E402
    COL,
    DATA_START_ROW,
    FORMULA_END_ROW,
    MONTH_TEMPLATE_SHEET,
    TEMPLATE_SPREADSHEET_TITLE,
    col_letter,
)
from app.sheet_builder import row_profit_rate_formula  # noqa: E402
from app.sheets_retry import execute_with_retry, values_batch_update  # noqa: E402
from app.users_store import load_users_config  # noqa: E402

_TITLE_RE = re.compile(r"^amazon-profit_.+_\d{4}\.xlsx$", re.IGNORECASE)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _list_targets(drive, folder_id: str, template_id: str | None) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "q": (
                f"'{folder_id}' in parents and "
                "mimeType = 'application/vnd.google-apps.spreadsheet' and "
                "trashed = false"
            ),
            "fields": "nextPageToken,files(id,name)",
            "pageSize": 200,
            "orderBy": "name",
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = execute_with_retry(
            drive.files().list(**kwargs),
            label="update_rate.list",
        )
        files.extend(resp.get("files") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    targets: list[dict[str, Any]] = []
    for f in files:
        name = f.get("name") or ""
        sid = f.get("id") or ""
        if ".retired." in name or ".bak" in name:
            continue
        is_tmpl = name == TEMPLATE_SPREADSHEET_TITLE or (
            bool(template_id) and sid == template_id
        )
        if not is_tmpl and not _TITLE_RE.match(name):
            continue
        targets.append({"title": name, "spreadsheet_id": sid, "is_template": is_tmpl})
    targets.sort(key=lambda r: (not r["is_template"], r["title"].lower()))
    return targets


def _sheet_titles(sheets_api, spreadsheet_id: str) -> list[str]:
    meta = execute_with_retry(
        sheets_api.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(title))",
        ),
        label="update_rate.meta",
    )
    return [s["properties"]["title"] for s in meta.get("sheets") or []]


def update_spreadsheet(sheets_api, spreadsheet_id: str) -> dict[str, Any]:
    titles = _sheet_titles(sheets_api, spreadsheet_id)
    rate_col = col_letter(COL["profit_rate"])
    detail_formula = row_profit_rate_formula(DATA_START_ROW)
    detail_block = [[detail_formula] for _ in range(DATA_START_ROW, FORMULA_END_ROW + 1)]

    data: list[dict[str, Any]] = []
    month_titles: list[str] = []
    for title in titles:
        if title == MONTH_TEMPLATE_SHEET or _MONTH_RE.match(title):
            month_titles.append(title)
            data.append(
                {
                    "range": f"'{title}'!{rate_col}{DATA_START_ROW}:{rate_col}{FORMULA_END_ROW}",
                    "values": detail_block,
                }
            )

    if not data:
        return {
            "spreadsheet_id": spreadsheet_id,
            "months": [],
            "detail_rows": 0,
            "skipped": "no_month_sheets",
        }

    values_batch_update(
        sheets_api,
        spreadsheet_id,
        data,
        chunk_size=10,
        label="update_rate.write",
    )
    return {
        "spreadsheet_id": spreadsheet_id,
        "months": month_titles,
        "detail_rows": FORMULA_END_ROW - DATA_START_ROW + 1,
        "formula": detail_formula,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spreadsheet-id",
        default="",
        help="Single spreadsheet id (default: template + all yearly books)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List targets only",
    )
    args = parser.parse_args()

    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    template_id = (cfg.get("template_spreadsheet_id") or "").strip() or None

    sid = (args.spreadsheet_id or "").strip()
    if sid:
        targets = [{"title": sid, "spreadsheet_id": sid, "is_template": False}]
    else:
        targets = _list_targets(drive, folder_id, template_id)

    print(f"targets={len(targets)} dry_run={args.dry_run}", flush=True)
    if args.dry_run:
        for t in targets:
            print(json.dumps(t, ensure_ascii=False), flush=True)
        return 0

    ok_n = 0
    errors: list[dict[str, Any]] = []
    for t in targets:
        try:
            report = update_spreadsheet(sheets, t["spreadsheet_id"])
            ok_n += 1
            print(
                json.dumps(
                    {
                        "title": t["title"],
                        "is_template": t.get("is_template"),
                        "ok": True,
                        **report,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 — continue other books
            errors.append({"title": t["title"], "error": str(exc)})
            print(
                json.dumps(
                    {"title": t["title"], "ok": False, "error": str(exc)},
                    ensure_ascii=False,
                ),
                flush=True,
            )

    print(json.dumps({"ok": ok_n, "errors": len(errors)}, ensure_ascii=False), flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
