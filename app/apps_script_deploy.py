"""Deploy container-bound Apps Script (Cancel☑ → 状態 simple onEdit).

New users inherit the script via Drive template copy. Do not re-attach on
provision (API re-attach can break simple onEdit registration).

Activate once on the template: Extensions → Apps Script → Save.
Existing books: ``scripts/deploy_cancel_onedit_all.py`` then Save once if needed.
"""

from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build

from app.google_clients import load_operator_credentials, sheets_service
from app.schema import apps_script_cancel_onedit_source
from app.sheets_retry import batch_update, execute_with_retry, http_status

APPSSCRIPT_JSON = """{
  "timeZone": "Asia/Tokyo",
  "exceptionLogging": "STACKDRIVER",
  "runtimeVersion": "V8"
}
"""

SCRIPT_TITLE = "amazon-profit-cancel-onedit"
SCRIPT_META_KEY = "apv_cancel_onedit_script_id"


def script_service(creds=None):
    return build(
        "script",
        "v1",
        credentials=creds or load_operator_credentials(),
        cache_discovery=False,
    )


def _missing_script_error(exc: BaseException) -> bool:
    status = http_status(exc)
    msg = str(exc).lower()
    return (
        status == 404
        or "not found" in msg
        or "notfound" in msg
        or "invalid script key" in msg
    )


def _read_script_meta(sheets_api, spreadsheet_id: str) -> tuple[str | None, int | None]:
    try:
        found = execute_with_retry(
            sheets_api.spreadsheets()
            .developerMetadata()
            .search(
                spreadsheetId=spreadsheet_id,
                body={
                    "dataFilters": [
                        {
                            "developerMetadataLookup": {
                                "metadataKey": SCRIPT_META_KEY,
                                "visibility": "DOCUMENT",
                            }
                        }
                    ]
                },
            ),
            label="script.meta.search",
        )
    except Exception as exc:
        if http_status(exc) in (403, 404) or "developerMetadata" in str(exc):
            return None, None
        raise
    matched = found.get("matchedDeveloperMetadata") or []
    if not matched:
        return None, None
    meta = matched[0].get("developerMetadata") or {}
    return (meta.get("metadataValue") or None), meta.get("metadataId")


def _write_script_meta(
    sheets_api, spreadsheet_id: str, script_id: str, metadata_id: int | None
) -> None:
    if metadata_id is not None:
        reqs: list[dict[str, Any]] = [
            {
                "updateDeveloperMetadata": {
                    "dataFilters": [
                        {
                            "developerMetadataLookup": {
                                "metadataKey": SCRIPT_META_KEY,
                                "visibility": "DOCUMENT",
                            }
                        }
                    ],
                    "developerMetadata": {"metadataValue": script_id},
                    "fields": "metadataValue",
                }
            }
        ]
    else:
        reqs = [
            {
                "createDeveloperMetadata": {
                    "developerMetadata": {
                        "metadataKey": SCRIPT_META_KEY,
                        "metadataValue": script_id,
                        "location": {"spreadsheet": True},
                        "visibility": "DOCUMENT",
                    }
                }
            }
        ]
    batch_update(sheets_api, spreadsheet_id, reqs, label="script.meta.write")


def _find_bound_script_id(spreadsheet_id: str, *, creds=None) -> tuple[str | None, int | None]:
    svc = script_service(creds)
    sheets = sheets_service(creds or load_operator_credentials())

    meta_sid, meta_id = _read_script_meta(sheets, spreadsheet_id)
    if meta_sid:
        try:
            execute_with_retry(
                svc.projects().get(scriptId=meta_sid),
                label="script.projects.get.meta",
            )
            return meta_sid, meta_id
        except Exception as exc:
            if not _missing_script_error(exc):
                raise

    try:
        existing = execute_with_retry(
            svc.projects().get(scriptId=spreadsheet_id),
            label="script.projects.get.self",
        )
        return (existing.get("scriptId") or spreadsheet_id), meta_id
    except Exception as exc:
        if not _missing_script_error(exc):
            raise
    return None, meta_id


def ensure_cancel_onedit_script(spreadsheet_id: str, *, creds=None) -> dict[str, Any]:
    """Attach or update container-bound Cancel☑→状態 onEdit on a workbook."""
    source = apps_script_cancel_onedit_source()
    creds = creds or load_operator_credentials()
    svc = script_service(creds)
    sheets = sheets_service(creds)
    created = False
    script_id, meta_id = _find_bound_script_id(spreadsheet_id, creds=creds)

    if not script_id:
        created_proj = execute_with_retry(
            svc.projects().create(
                body={
                    "title": SCRIPT_TITLE,
                    "parentId": spreadsheet_id,
                }
            ),
            label="script.projects.create",
        )
        script_id = created_proj["scriptId"]
        created = True

    execute_with_retry(
        svc.projects().updateContent(
            scriptId=script_id,
            body={
                "files": [
                    {
                        "name": "CancelStatusOnEdit",
                        "type": "SERVER_JS",
                        "source": source,
                    },
                    {
                        "name": "appsscript",
                        "type": "JSON",
                        "source": APPSSCRIPT_JSON,
                    },
                ]
            },
        ),
        label="script.projects.updateContent",
    )
    _write_script_meta(sheets, spreadsheet_id, script_id, meta_id)
    return {
        "spreadsheet_id": spreadsheet_id,
        "script_id": script_id,
        "bound": True,
        "created": created,
        "script_editor": f"https://script.google.com/home/projects/{script_id}/edit",
    }
