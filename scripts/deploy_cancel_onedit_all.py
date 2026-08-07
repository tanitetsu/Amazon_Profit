"""Push bound Cancel☑→状態 onEdit to template + every yearly workbook."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.apps_script_deploy import ensure_cancel_onedit_script  # noqa: E402
from app.google_clients import (  # noqa: E402
    drive_service,
    load_operator_credentials,
    resolve_operator_folder_id,
)
from app.schema import TEMPLATE_SPREADSHEET_TITLE, apps_script_cancel_onedit_source  # noqa: E402
from app.sheets_retry import execute_with_retry  # noqa: E402
from app.users_store import load_users_config  # noqa: E402

_TITLE_RE = re.compile(r"^amazon-profit_.+_\d{4}\.xlsx$", re.IGNORECASE)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_users_config()
    creds = load_operator_credentials()
    drive = drive_service(creds)
    folder_id = resolve_operator_folder_id(drive, cfg["folder_name"])
    template_id = (cfg.get("template_spreadsheet_id") or "").strip() or None
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
        label="deploy_onedit.list",
    )
    targets: list[dict] = []
    for f in resp.get("files") or []:
        name = f.get("name") or ""
        sid = f.get("id") or ""
        is_tmpl = name == TEMPLATE_SPREADSHEET_TITLE or (
            bool(template_id) and sid == template_id
        )
        if not is_tmpl and not _TITLE_RE.match(name):
            continue
        targets.append({"title": name, "spreadsheet_id": sid, "is_template": is_tmpl})
    targets.sort(key=lambda r: (not r["is_template"], r["title"].lower()))
    print(f"targets={len(targets)}", flush=True)
    expect = apps_script_cancel_onedit_source()

    ok_n = 0
    errors: list[dict] = []
    for t in targets:
        try:
            info = ensure_cancel_onedit_script(t["spreadsheet_id"], creds=creds)
            row = {
                **t,
                **info,
                "ok": True,
                "source_len": len(expect),
            }
            ok_n += 1
            print(
                json.dumps(
                    {
                        "title": row["title"],
                        "ok": True,
                        "created": row.get("created"),
                        "script_id": row.get("script_id"),
                        "script_editor": row.get("script_editor"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            err = {**t, "ok": False, "error": str(exc)}
            errors.append(err)
            print(json.dumps(err, ensure_ascii=False), flush=True)

    print(
        json.dumps(
            {"summary": {"total": len(targets), "ok": ok_n, "failed": len(errors)}, "errors": errors},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
