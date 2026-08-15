"""Split 税込価格 into 販売価格 + 税金 on the live template and every yearly book.

asamiodaka.b workbooks are copied to a .bak-price-tax-* file before changes.
Existing order rows are refilled from each user's 注文確定 mail (no tax math).
Editable columns are never written.

  python scripts/migrate_price_tax_columns.py --dry-run
  python scripts/migrate_price_tax_columns.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.google_clients import (  # noqa: E402
    drive_service,
    load_operator_credentials,
    resolve_operator_folder_id,
    sheets_service,
)
from app.migrate_price_tax import (  # noqa: E402
    BACKUP_USER_ID,
    list_migration_targets,
    migrate_spreadsheet,
)
from app.users_store import load_users_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-backfill", action="store_true")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Limit to title substring or user_id (repeatable)",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    sheets_api = sheets_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    template_id = (cfg.get("template_spreadsheet_id") or "").strip() or None
    targets = list_migration_targets(drive, folder_id, template_id)
    if args.only:
        needles = [x.lower() for x in args.only]
        targets = [
            t
            for t in targets
            if any(
                n in (t["title"] or "").lower()
                or n == (t.get("user_id") or "").lower()
                for n in needles
            )
        ]

    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "skip_backfill": args.skip_backfill,
                "targets": len(targets),
                "backup_user": BACKUP_USER_ID,
                "titles": [t["title"] for t in targets],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    results: list[dict] = []
    errors: list[dict] = []
    for t in targets:
        print(f"==> {t['title']} backup={t['do_backup']}", flush=True)
        try:
            info = migrate_spreadsheet(
                sheets_api,
                drive,
                spreadsheet_id=t["spreadsheet_id"],
                title=t["title"],
                folder_id=folder_id,
                gmail=t.get("gmail"),
                role=t.get("role"),
                is_template=t["is_template"],
                dry_run=args.dry_run,
                skip_backfill=args.skip_backfill,
                do_backup=t["do_backup"],
            )
            results.append(info)
            print(json.dumps(info, ensure_ascii=False, default=str), flush=True)
        except Exception as exc:  # noqa: BLE001
            err = {"title": t["title"], "spreadsheet_id": t["spreadsheet_id"], "error": str(exc)}
            errors.append(err)
            print(json.dumps(err, ensure_ascii=False), flush=True)

    print(
        json.dumps(
            {"ok": len(results), "errors": len(errors), "error_rows": errors},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
