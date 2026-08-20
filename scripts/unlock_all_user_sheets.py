"""Remove range locks from every user yearly workbook (not the live template).

Does not change amazon-profit_TEMPLATE.xlsx or bound Apps Script.

  python scripts/unlock_all_user_sheets.py --dry-run
  python scripts/unlock_all_user_sheets.py
"""

from __future__ import annotations

import argparse
import json
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
from app.provision import parse_workbook_title  # noqa: E402
from app.schema import TEMPLATE_SPREADSHEET_TITLE  # noqa: E402
from app.sheet_protection import unlock_workbook  # noqa: E402
from app.sheets_retry import execute_with_retry  # noqa: E402
from app.users_store import load_users_config  # noqa: E402


def _iter_folder_spreadsheets(drive, folder_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "q": (
                f"'{folder_id}' in parents and "
                "mimeType = 'application/vnd.google-apps.spreadsheet' and "
                "trashed = false"
            ),
            "spaces": "drive",
            "fields": "nextPageToken,files(id,name)",
            "pageSize": 200,
            "orderBy": "name",
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = execute_with_retry(
            drive.files().list(**kwargs),
            label="unlock.list",
        )
        files.extend(resp.get("files") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def list_unlock_targets(
    drive, folder_id: str, template_id: str | None
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for f in _iter_folder_spreadsheets(drive, folder_id):
        title = f.get("name") or ""
        sid = f.get("id") or ""
        if title == TEMPLATE_SPREADSHEET_TITLE or (
            template_id and sid == template_id
        ):
            continue
        if ".retired." in title or ".bak" in title:
            continue
        uid, year = parse_workbook_title(title)
        if not uid or year is None:
            continue
        targets.append(
            {
                "title": title,
                "spreadsheet_id": sid,
                "user_id": uid,
                "year": year,
            }
        )
    targets.sort(key=lambda r: (r["user_id"].lower(), r["year"]))
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets_api = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    template_id = (cfg.get("template_spreadsheet_id") or "").strip() or None
    targets = list_unlock_targets(drive, folder_id, template_id)

    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "targets": len(targets),
                "titles": [t["title"] for t in targets],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    reports: list[dict[str, Any]] = []
    for t in targets:
        if args.dry_run:
            reports.append({**t, "skipped": "dry_run"})
            continue
        result = unlock_workbook(sheets_api, t["spreadsheet_id"])
        reports.append({**t, **result})
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)

    print(
        json.dumps({"ok": True, "reports": reports}, ensure_ascii=False),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
