"""Bootstrap 2026 yearly workbook from legacy Excel (no Gmail ingest)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.excel_import import import_legacy_excel_to_workbook  # noqa: E402
from app.google_clients import (  # noqa: E402
    drive_service,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.users_store import load_users_config  # noqa: E402

DEFAULT_EXCEL = Path(r"e:\DownLoad\Amazon利益管理シート①.xlsx")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gmail", default="asamiodaka@gmail.com")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--excel", type=Path, default=DEFAULT_EXCEL)
    ap.add_argument("--no-rebuild", action="store_true")
    args = ap.parse_args()

    if not args.excel.exists():
        print(f"missing excel: {args.excel}", file=sys.stderr)
        return 1

    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])

    result = import_legacy_excel_to_workbook(
        sheets,
        drive,
        gmail=args.gmail,
        excel_path=str(args.excel),
        year=args.year,
        folder_id=folder_id,
        rebuild=not args.no_rebuild,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
