"""Google Drive / Sheets helpers for the operator account."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import google.auth
from google.auth.credentials import Credentials as GoogleCredentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.sheets_retry import call_with_retry, execute_with_retry

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    # Cancel☑ → 状態 (container-bound Apps Script deploy)
    "https://www.googleapis.com/auth/script.projects",
    # Plan B: consent mail from operator Gmail
    "https://www.googleapis.com/auth/gmail.send",
]

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
CLIENT_SECRETS = SECRETS / "oauth_client.json"
# Operator local login prefers Desktop client (loopback works). Web client is for Gmail callback.
OPERATOR_CLIENT_SECRETS = SECRETS / "oauth_client_desktop.json"
TOKEN_PATH = SECRETS / "operator_token.json"


def _operator_client_secrets_path() -> Path:
    if OPERATOR_CLIENT_SECRETS.is_file():
        return OPERATOR_CLIENT_SECRETS
    return CLIENT_SECRETS


def _oauth_client_application_type(path: Path) -> str | None:
    """Return 'installed' or 'web' from an OAuth client JSON. Never logs secrets."""
    import json

    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None
    if "installed" in info:
        return "installed"
    if "web" in info:
        return "web"
    return None


def _require_desktop_operator_client(path: Path) -> None:
    """InstalledAppFlow uses a random localhost port; Web clients reject that."""
    kind = _oauth_client_application_type(path)
    if kind == "installed":
        return
    if kind == "web":
        raise FileNotFoundError(
            f"{path} is a Web OAuth client. scripts/oauth_operator.py opens a random "
            "localhost port, which Google rejects with redirect_uri_mismatch. "
            "In GCP → APIs & Services → Credentials, create an OAuth client of type "
            "Desktop, download the JSON, and save it as "
            f"{OPERATOR_CLIENT_SECRETS} "
            "(do not overwrite secrets/oauth_client.json — that Web client is for "
            "user Gmail consent on amazon-profit-oauth)."
        )
    raise FileNotFoundError(
        f"{path} is not a recognized OAuth client JSON "
        "(need a Desktop client with top-level key 'installed')."
    )


def uses_adc_credentials() -> bool:
    """
    True when Drive/Sheets should use the runtime service account (ADC).

    Cloud Run sets ADMIN_USE_ADC=1 (and K_SERVICE). Do NOT treat
    GOOGLE_APPLICATION_CREDENTIALS alone as ADC — local machines often set it
    for AI_Cripping GCS while Drive must still use operator_token.json.
    """
    flag = (os.environ.get("ADMIN_USE_ADC") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    if os.environ.get("K_SERVICE"):
        return True
    return False


def load_operator_credentials() -> GoogleCredentials:
    if uses_adc_credentials():
        creds, _project = google.auth.default(scopes=SCOPES)
        return creds

    return load_operator_oauth_credentials()


def _operator_oauth_missing_source_message() -> str:
    return (
        "operator user OAuth token file not found "
        "(set OPERATOR_TOKEN_GCS_URI or mount secrets/operator_token.json). "
        "Re-run scripts/oauth_operator.py as 26964u@gmail.com, then upload the token to GCS."
    )


def _operator_oauth_unusable_message(reason: str) -> str:
    return (
        f"operator user OAuth token is unusable: {reason}. "
        "Re-run scripts/oauth_operator.py as 26964u@gmail.com, then upload "
        "secrets/operator_token.json to OPERATOR_TOKEN_GCS_URI."
    )


def _summarize_oauth_refresh_failure(exc: BaseException) -> str:
    msg = str(exc).replace("\n", " ").strip()
    if len(msg) > 240:
        msg = msg[:240] + "…"
    lower = msg.lower()
    if "invalid_grant" in lower or "expired or revoked" in lower:
        return (
            "refresh failed (invalid_grant: token expired or revoked). "
            f"Google said: {msg}"
        )
    return f"refresh failed ({type(exc).__name__}: {msg})"


def _load_operator_token_text() -> str | None:
    gcs_uri = (os.environ.get("OPERATOR_TOKEN_GCS_URI") or "").strip()
    if gcs_uri.startswith("gs://"):
        from app.gcs_credentials import gcs_storage_client

        rest = gcs_uri[5:]
        bucket_name, _, blob_name = rest.partition("/")
        blob = gcs_storage_client().bucket(bucket_name).blob(blob_name)
        if not call_with_retry(blob.exists, label="operator_token.gcs.exists"):
            raise FileNotFoundError(f"operator token not in GCS: {gcs_uri}")
        return call_with_retry(
            lambda: blob.download_as_text(encoding="utf-8"),
            label="operator_token.gcs.download",
        )
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8")
    return None


def load_stored_operator_oauth_credentials() -> Credentials:
    """
    Load operator user OAuth from GCS or secrets/operator_token.json and refresh
    if needed. Never starts a browser consent flow.
    """
    import json

    token_text = _load_operator_token_text()
    if not token_text:
        raise FileNotFoundError(_operator_oauth_missing_source_message())

    try:
        info = json.loads(token_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(_operator_oauth_unusable_message("token JSON is invalid")) from exc
    if not isinstance(info, dict):
        raise RuntimeError(_operator_oauth_unusable_message("token JSON is not an object"))

    # Use scopes stored in the token (not the desired SCOPES list), so
    # has_scopes() reflects what was actually granted.
    creds = Credentials.from_authorized_user_info(info)
    if creds.valid and creds.has_scopes(SCOPES):
        return creds

    granted = [s for s in (creds.scopes or []) if s]
    missing = [s for s in SCOPES if s not in granted]
    if missing and creds.valid:
        raise RuntimeError(
            _operator_oauth_unusable_message(
                "missing scopes: " + ", ".join(missing)
            )
        )

    if creds.refresh_token:
        def _refresh() -> Credentials:
            creds.refresh(Request())
            if not creds.has_scopes(SCOPES):
                still_missing = [s for s in SCOPES if s not in (creds.scopes or [])]
                raise RuntimeError(
                    _operator_oauth_unusable_message(
                        "missing scopes after refresh: " + ", ".join(still_missing)
                    )
                )
            _persist_operator_oauth(creds)
            return creds

        try:
            return call_with_retry(_refresh, label="operator.oauth.refresh")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                _operator_oauth_unusable_message(_summarize_oauth_refresh_failure(exc))
            ) from exc

    if missing:
        raise RuntimeError(
            _operator_oauth_unusable_message("missing scopes: " + ", ".join(missing))
        )
    raise RuntimeError(
        _operator_oauth_unusable_message(
            "access token is not valid and no refresh_token is stored"
        )
    )


def probe_operator_oauth() -> dict[str, Any]:
    """Admin UI status. Never starts a browser. Does not log token JSON."""
    try:
        creds = load_stored_operator_oauth_credentials()
        expiry = getattr(creds, "expiry", None)
        return {
            "ok": True,
            "expiry": expiry.isoformat() if expiry is not None else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def load_operator_oauth_credentials() -> Credentials:
    """
    User OAuth for the operator (26964u…). Used for Gmail send and local Drive.
    Never uses the Cloud Run service account — SA cannot send as the operator mailbox.
    On Cloud Run (ADC), a stored token that cannot be refreshed raises.
    Locally, a bad stored token falls through to a browser consent flow.
    """
    try:
        return load_stored_operator_oauth_credentials()
    except Exception:
        if uses_adc_credentials():
            raise
        # Local recovery / scripts/oauth_operator.py: re-consent in a browser.

    secrets_path = _operator_client_secrets_path()
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Missing {OPERATOR_CLIENT_SECRETS} (preferred) or {CLIENT_SECRETS}. "
            "Create an OAuth Desktop client JSON as secrets/oauth_client_desktop.json "
            "(Web client cannot use random localhost ports)."
        )
    _require_desktop_operator_client(secrets_path)
    # Desktop client: any localhost port. Web client needs exact redirect pre-registered.
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    # Force consent when scopes expand so new script.* scopes are granted.
    creds = flow.run_local_server(host="127.0.0.1", port=0, prompt="consent")
    _persist_operator_oauth(creds)
    return creds


def _persist_operator_oauth(creds: Credentials) -> None:
    payload = creds.to_json()
    gcs_uri = (os.environ.get("OPERATOR_TOKEN_GCS_URI") or "").strip()
    if gcs_uri.startswith("gs://"):
        from app.gcs_credentials import gcs_storage_client

        rest = gcs_uri[5:]
        bucket_name, _, blob_name = rest.partition("/")
        call_with_retry(
            lambda: gcs_storage_client()
            .bucket(bucket_name)
            .blob(blob_name)
            .upload_from_string(
                payload, content_type="application/json; charset=utf-8"
            ),
            label="operator_token.gcs.upload",
        )
        return
    SECRETS.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(payload, encoding="utf-8")


def drive_service(creds: GoogleCredentials | None = None):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    c = creds or load_operator_credentials()
    http = AuthorizedHttp(c, http=httplib2.Http(timeout=180))
    return build("drive", "v3", http=http, cache_discovery=False)


def sheets_service(creds: GoogleCredentials | None = None):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    c = creds or load_operator_credentials()
    http = AuthorizedHttp(c, http=httplib2.Http(timeout=180))
    return build("sheets", "v4", http=http, cache_discovery=False)


def find_folder_id(drive, name: str, parent_id: str | None = "root") -> str | None:
    q = [
        f"name = '{name}'",
        "mimeType = 'application/vnd.google-apps.folder'",
        "trashed = false",
    ]
    if parent_id:
        q.append(f"'{parent_id}' in parents")
    resp = execute_with_retry(
        drive.files().list(
            q=" and ".join(q), spaces="drive", fields="files(id, name)", pageSize=10
        ),
        label="drive.files.list.folder",
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def ensure_folder(drive, name: str, parent_id: str | None = "root") -> str:
    existing = find_folder_id(drive, name, parent_id)
    if existing:
        return existing
    meta: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]
    created = execute_with_retry(
        drive.files().create(body=meta, fields="id"),
        label="drive.files.create.folder",
    )
    return created["id"]


def find_spreadsheet_in_folder(drive, title: str, folder_id: str) -> str | None:
    q = (
        f"name = '{title}' and '{folder_id}' in parents and "
        "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    )
    resp = execute_with_retry(
        drive.files().list(q=q, spaces="drive", fields="files(id, name)", pageSize=5),
        label="drive.files.list.spreadsheet",
    )
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def create_spreadsheet_in_folder(drive, sheets, title: str, folder_id: str) -> str:
    existing = find_spreadsheet_in_folder(drive, title, folder_id)
    if existing:
        return existing
    created = execute_with_retry(
        sheets.spreadsheets().create(
            body={"properties": {"title": title}}, fields="spreadsheetId"
        ),
        label="sheets.spreadsheets.create",
    )
    spreadsheet_id = created["spreadsheetId"]
    # Move into target folder (remove from root parent)
    file = execute_with_retry(
        drive.files().get(fileId=spreadsheet_id, fields="parents"),
        label="drive.files.get.parents",
    )
    prev_parents = ",".join(file.get("parents", []))
    execute_with_retry(
        drive.files().update(
            fileId=spreadsheet_id,
            addParents=folder_id,
            removeParents=prev_parents,
            fields="id, parents",
        ),
        label="drive.files.update.parents",
    )
    return spreadsheet_id


def copy_spreadsheet_in_folder(
    drive, source_id: str, title: str, folder_id: str
) -> str:
    """Copy a spreadsheet into folder (template → user yearly book).

    On Cloud Run (ADC / runtime SA), creation uses operator user OAuth instead:
    service accounts have no usable My Drive storage quota, so files.copy would
    return storageQuotaExceeded.
    """
    create_drive = drive
    if uses_adc_credentials():
        create_drive = drive_service(load_operator_oauth_credentials())

    existing = find_spreadsheet_in_folder(create_drive, title, folder_id)
    if existing:
        return existing
    copied = execute_with_retry(
        create_drive.files().copy(
            fileId=source_id,
            body={"name": title, "parents": [folder_id]},
            fields="id",
        ),
        label="drive.files.copy",
    )
    return copied["id"]


def retire_spreadsheet_for_overwrite(drive, file_id: str, title: str) -> str:
    """
    Free the yearly title so a fresh template copy can be created.

    Cloud Run uses a runtime SA that is Editor (writer), not owner — Drive
    ``files.delete`` then returns 403. Prefer delete when allowed; otherwise
    rename (and trash when possible) so ``find_spreadsheet_in_folder`` no longer
    matches the active title.
    """
    from datetime import datetime, timezone

    from googleapiclient.errors import HttpError

    try:
        execute_with_retry(
            drive.files().delete(fileId=file_id),
            label="drive.files.delete",
        )
        return "deleted"
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status not in (403, 404):
            raise
        if status == 404:
            return "missing"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    retired_name = f"{title}.retired.{stamp}"
    execute_with_retry(
        drive.files().update(
            fileId=file_id, body={"name": retired_name}, fields="id,name"
        ),
        label="drive.files.update.rename",
    )
    try:
        execute_with_retry(
            drive.files().update(
                fileId=file_id, body={"trashed": True}, fields="id,trashed"
            ),
            label="drive.files.update.trash",
        )
        return "renamed_trashed"
    except HttpError:
        return "renamed"


def load_users_config() -> dict[str, Any]:
    """Back-compat re-export; prefer app.users_store."""
    from app.users_store import load_users_config as _load

    return _load()


def resolve_operator_folder_id(drive, folder_name: str) -> str:
    """
    Locate User_Acounting (or configured folder).

    OAuth (operator My Drive): search under root, create if missing.
    ADC / service account: search all accessible drives (shared folder);
    do not create a new root folder under the SA.
    """
    if uses_adc_credentials():
        folder_id = find_folder_id(drive, folder_name, parent_id=None)
        if not folder_id:
            raise RuntimeError(
                f"folder {folder_name!r} not reachable by service account; "
                "share it as Editor with the Cloud Run runtime SA"
            )
        return folder_id

    about = execute_with_retry(
        drive.about().get(fields="user(emailAddress)"),
        label="drive.about.get",
    )
    email = (about.get("user") or {}).get("emailAddress") or ""
    from app.users_store import load_users_config as _load

    operator = (_load().get("operator_drive_email") or "").strip()
    if email and operator and email.lower() != operator.lower():
        raise RuntimeError(f"wrong operator account: {email} (expected {operator})")

    return find_folder_id(drive, folder_name) or ensure_folder(drive, folder_name)
