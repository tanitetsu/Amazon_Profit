"""Smoke: copy template, create gap months, verify descending order, delete smoke file."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import (  # noqa: E402
    copy_spreadsheet_in_folder,
    drive_service,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.template_ops import (  # noqa: E402
    ensure_months_for_order,
    list_month_sheets,
    resolve_template_spreadsheet_id,
)
from app.users_store import load_users_config  # noqa: E402


def main() -> int:
    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder = resolve_operator_folder_id(drive, cfg["folder_name"])
    tid = resolve_template_spreadsheet_id(drive, cfg)
    smoke = "amazon-profit__smoke_template_2026.xlsx"
    # cleanup old
    from app.google_clients import find_spreadsheet_in_folder

    old = find_spreadsheet_in_folder(drive, smoke, folder)
    if old:
        drive.files().delete(fileId=old).execute()
    sid = copy_spreadsheet_in_folder(drive, tid, smoke, folder)
    print("smoke id", sid, flush=True)
    ensure_months_for_order(
        sheets, sid, "2026-04", gmail="smoke@example.com", year=2026
    )
    ensure_months_for_order(
        sheets, sid, "2026-07", gmail="smoke@example.com", year=2026
    )
    months = list_month_sheets(sheets, sid)
    print("months", months, flush=True)
    assert months == ["2026-07", "2026-06", "2026-05", "2026-04"], months
    drive.files().delete(fileId=sid).execute()
    print("ok deleted smoke", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
