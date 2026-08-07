"""Provision empty user workbooks on operator Drive."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.google_clients import (
    drive_service,
    find_folder_id,
    load_operator_credentials,
    load_operator_oauth_credentials,
    uses_adc_credentials,
)
from app.schema import (
    TEMPLATE_SPREADSHEET_TITLE,
    gmail_from_user_id,
    spreadsheet_title_from_gmail,
    user_id_from_gmail,
)
from app.sheet_protection import unshare_user
from app.sheets_retry import execute_with_retry
from app.users_store import load_users_config

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_GMAIL_DOMAIN_RE = re.compile(r"^[^@\s]+@gmail\.com$", re.IGNORECASE)
_TITLE_RE = re.compile(r"^amazon-profit_(.+)_(\d{4})\.xlsx$", re.IGNORECASE)
# Register-time ingest only; full catch-up is left to the poller.
_REGISTER_INGEST_MAX = 100


def _require_gmail_address(gmail: str) -> str:
    """Roster / IAP convention: must be a real @gmail.com address."""
    cleaned = (gmail or "").strip()
    if not _EMAIL_RE.match(cleaned):
        raise ValueError(f"invalid email: {cleaned}")
    if not _GMAIL_DOMAIN_RE.match(cleaned):
        raise ValueError(
            f"Gmail アドレスのみ対応です（@gmail.com）: {cleaned}"
        )
    return cleaned


def parse_workbook_title(title: str) -> tuple[str | None, int | None]:
    """Return (user_id, year) from amazon-profit_{user}_{yyyy}.xlsx."""
    m = _TITLE_RE.match((title or "").strip())
    if not m:
        return None, None
    try:
        return m.group(1), int(m.group(2))
    except ValueError:
        return m.group(1), None


def _find_operator_folder_id(drive, folder_name: str) -> str | None:
    parent = None if uses_adc_credentials() else "root"
    return find_folder_id(drive, folder_name, parent_id=parent)


def spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def list_user_workbooks(*, include_template: bool = False) -> list[dict[str, Any]]:
    """
    Active roster (setting/user-list.csv) + matching Drive workbooks.

    Drive-only leftovers (deleted users) are NOT listed — roster is the single list.
    Each item: gmail, user_id, role, year?, title, spreadsheet_id?, url?, on_drive
    """
    from app.clipping_roster import list_active_users

    cfg = load_users_config()
    folder_name = cfg["folder_name"]
    if uses_adc_credentials():
        creds = load_operator_oauth_credentials()
    else:
        creds = load_operator_credentials()
    drive = drive_service(creds)
    folder_id = find_folder_id(drive, folder_name, parent_id="root")
    if not folder_id and uses_adc_credentials():
        folder_id = find_folder_id(drive, folder_name, parent_id=None)

    template_id = (cfg.get("template_spreadsheet_id") or "").strip()
    template_file: dict[str, Any] | None = None
    # user_id -> {year -> drive file}
    by_uid: dict[str, dict[int, dict[str, Any]]] = {}

    if folder_id:
        resp = execute_with_retry(
            drive.files().list(
                q=(
                    f"'{folder_id}' in parents and "
                    "mimeType = 'application/vnd.google-apps.spreadsheet' and "
                    "trashed = false"
                ),
                spaces="drive",
                fields="files(id, name, modifiedTime, webViewLink)",
                pageSize=200,
                orderBy="name",
            ),
            label="drive.files.list.workbooks",
        )
        for f in resp.get("files", []):
            title = f["name"]
            if title == TEMPLATE_SPREADSHEET_TITLE or (
                template_id and f["id"] == template_id
            ):
                template_file = f
                continue
            uid, year = parse_workbook_title(title)
            if not uid or year is None:
                continue
            by_uid.setdefault(uid, {})[year] = f

    rows: list[dict[str, Any]] = []
    for entry in list_active_users():
        uid = entry["user_id"]
        gmail = entry["gmail"]
        role = entry["role"]
        years = by_uid.get(uid) or {}
        if years:
            year = max(years)
            f = years[year]
            sid = f["id"]
            rows.append(
                {
                    "title": f["name"],
                    "spreadsheet_id": sid,
                    "url": f.get("webViewLink") or spreadsheet_url(sid),
                    "modified_time": f.get("modifiedTime"),
                    "on_drive": True,
                    "gmail": gmail,
                    "user_id": uid,
                    "role": role,
                    "year": year,
                    "note": "",
                    "is_template": False,
                }
            )
        else:
            title = spreadsheet_title_from_gmail(gmail, date.today().year)
            _, year = parse_workbook_title(title)
            rows.append(
                {
                    "title": title,
                    "spreadsheet_id": None,
                    "url": None,
                    "modified_time": None,
                    "on_drive": False,
                    "gmail": gmail,
                    "user_id": uid,
                    "role": role,
                    "year": year,
                    "note": "",
                    "is_template": False,
                }
            )

    rows.sort(
        key=lambda r: (
            (r.get("user_id") or r.get("gmail") or "").lower(),
            -(r.get("year") or 0),
        )
    )
    if include_template:
        if template_file:
            sid = template_file["id"]
            tmpl = {
                "title": template_file.get("name") or TEMPLATE_SPREADSHEET_TITLE,
                "spreadsheet_id": sid,
                "url": template_file.get("webViewLink") or spreadsheet_url(sid),
                "modified_time": template_file.get("modifiedTime"),
                "on_drive": True,
                "gmail": None,
                "user_id": None,
                "role": None,
                "year": None,
                "note": "",
                "is_template": True,
            }
        else:
            tmpl = {
                "title": TEMPLATE_SPREADSHEET_TITLE,
                "spreadsheet_id": template_id or None,
                "url": spreadsheet_url(template_id) if template_id else None,
                "modified_time": None,
                "on_drive": bool(template_id),
                "gmail": None,
                "user_id": None,
                "role": None,
                "year": None,
                "note": "",
                "is_template": True,
            }
        rows = [tmpl, *rows]
    return rows


def provision_user(
    gmail: str,
    note: str = "",
    *,
    role: str = "Normal",
    rebuild: bool = False,
    existing_file_action: str | None = None,
) -> dict[str, Any]:
    """
    Create yearly workbook by copying the Drive template (empty data).

    existing_file_action: None → confirm (Admin UI then auto-keeps); \"keep\" → reuse+share;
    \"overwrite\" → wipe+recreate (API escape hatch; Admin UI does not use it).
    role is the AI_Cripping app role (Admin / Exclusive / Normal), not Drive ACL.
    """
    gmail = _require_gmail_address(gmail)

    from app.template_ops import provision_from_template

    return provision_from_template(
        gmail,
        note=note,
        role=role,
        rebuild=rebuild,
        existing_file_action=existing_file_action,
    )


def register_user(
    gmail: str,
    note: str = "",
    *,
    role: str = "Normal",
    rebuild: bool = False,
    existing_file_action: str | None = None,
    base_url: str | None = None,
    max_attempts: int = 5,
) -> dict[str, Any]:
    """
    Full Admin \"add user\" flow with whole-flow retry on transient failures:
    provision (sheet + protect + share + roster + IAP) then consent mail or ingest.

    On mid-flow failure, ``provision_from_template`` rolls back new sheets / new roster
    entries before re-raising so the next attempt can start clean (or ``keep`` if the
    sheet survived a post-provision step such as consent mail).
    """
    import time
    import random

    from app.gmail_oauth import has_gmail_token
    from app.sheets_retry import is_transient
    from app.template_ops import WorkbookExistsError, provision_from_template

    gmail = _require_gmail_address(gmail)

    action = existing_file_action
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            try:
                result = provision_from_template(
                    gmail,
                    note=note,
                    role=role,
                    rebuild=rebuild,
                    existing_file_action=action,
                )
            except WorkbookExistsError:
                # Consent may have failed after sheet+roster succeeded; reuse.
                if action:
                    raise
                action = "keep"
                result = provision_from_template(
                    gmail,
                    note=note,
                    role=role,
                    rebuild=False,
                    existing_file_action="keep",
                )

            result["gmail_linked"] = has_gmail_token(gmail)
            result["consent_email"] = None
            result["consent_email_error"] = None
            result["mail_ingest"] = None
            result["mail_ingest_error"] = None
            result["register_attempts"] = attempt

            if result["gmail_linked"]:
                from app.mail_ingest import ingest_user_mail

                # Cap register-time ingest so Cloud Run / gunicorn do not time out.
                # Remaining mail is picked up by the poller.
                try:
                    result["mail_ingest"] = ingest_user_mail(
                        gmail,
                        max_results=_REGISTER_INGEST_MAX,
                        max_fetch=_REGISTER_INGEST_MAX,
                    )
                except Exception as exc:  # noqa: BLE001
                    result["mail_ingest_error"] = str(exc)
            else:
                from app.consent_mail import send_gmail_consent_email

                result["consent_email"] = send_gmail_consent_email(
                    gmail,
                    sheet_url=result.get("url"),
                    sheet_title=result.get("title"),
                    base_url=base_url,
                )

            return result
        except WorkbookExistsError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            last_exc = exc
            if not is_transient(exc) or attempt >= max_attempts:
                raise
            # Next attempt: reuse sheet if it still exists after partial success.
            if not action:
                action = "keep"
            delay = min(60.0, 2.0 * (2 ** (attempt - 1)))
            delay *= 0.75 + random.random() * 0.5
            print(
                f"register_user: transient "
                f"(attempt {attempt}/{max_attempts}), sleep {delay:.1f}s - "
                f"{type(exc).__name__}",
                flush=True,
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


def deprovision_user(gmail: str) -> dict[str, Any]:
    """
    Revoke amazon-profit + AI_Cripping access for the user:
    - Unshare all yearly workbooks for the user_id
    - Remove from AI_Cripping user-list.csv; append setting/quitted_user.txt
    - Archive setting/user/{id}/ → setting/quitted-user/{id}/ (scraping-data/log kept)
    - Delete Gmail OAuth token + seen ids (stops polling for this user)
    Spreadsheet files are kept.
    """
    gmail = gmail.strip()
    if not _EMAIL_RE.match(gmail):
        raise ValueError(f"invalid email: {gmail}")

    cfg = load_users_config()
    folder_name = cfg["folder_name"]
    uid = user_id_from_gmail(gmail)
    # Normalize to canonical gmail derived from user_id.
    gmail = gmail_from_user_id(uid)
    title = spreadsheet_title_from_gmail(gmail, date.today().year)

    creds = load_operator_credentials()
    drive = drive_service(creds)
    folder_id = _find_operator_folder_id(drive, folder_name)

    unshared_files: list[dict[str, Any]] = []
    if folder_id:
        prefix = f"amazon-profit_{uid}_"
        resp = execute_with_retry(
            drive.files().list(
                q=(
                    f"'{folder_id}' in parents and "
                    "mimeType = 'application/vnd.google-apps.spreadsheet' and "
                    "trashed = false"
                ),
                spaces="drive",
                fields="files(id, name)",
                pageSize=200,
            ),
            label="drive.files.list.deprovision",
        )
        for f in resp.get("files", []):
            name = f.get("name") or ""
            if not name.startswith(prefix):
                continue
            ok = unshare_user(drive, f["id"], gmail)
            unshared_files.append(
                {"title": name, "spreadsheet_id": f["id"], "unshared": ok}
            )

    gmail_token_removed = False
    gmail_seen_removed = False
    try:
        from app.gmail_oauth import delete_gmail_credentials
        from app.mail_ingest import clear_seen_ids

        gmail_token_removed = delete_gmail_credentials(gmail)
        gmail_seen_removed = clear_seen_ids(gmail)
    except Exception:  # noqa: BLE001
        pass

    clipping: dict[str, Any] | None = None
    clipping_error: str | None = None
    try:
        from app.clipping_roster import remove_clipping_user

        clipping = remove_clipping_user(gmail)
    except Exception as exc:  # noqa: BLE001
        clipping_error = str(exc)

    iap: dict[str, Any] | None = None
    iap_error: str | None = None
    try:
        from app.iap_access import revoke_iap_access

        iap = revoke_iap_access(gmail)
    except Exception as exc:  # noqa: BLE001
        iap_error = str(exc)

    primary = next(
        (x for x in unshared_files if x["title"] == title),
        unshared_files[0] if unshared_files else None,
    )
    spreadsheet_id = primary["spreadsheet_id"] if primary else None

    return {
        "gmail": gmail,
        "user_id": uid,
        "title": title,
        "spreadsheet_id": spreadsheet_id,
        "url": spreadsheet_url(spreadsheet_id) if spreadsheet_id else None,
        "file_kept": bool(unshared_files),
        "unshared": any(x.get("unshared") for x in unshared_files),
        "unshared_files": unshared_files,
        "removed_from_config": bool(clipping and clipping.get("removed_from_roster")),
        "gmail_token_removed": gmail_token_removed,
        "gmail_seen_removed": gmail_seen_removed,
        "gmail_poll_disabled": bool(gmail_token_removed or gmail_seen_removed),
        "clipping": clipping,
        "clipping_error": clipping_error,
        "iap": iap,
        "iap_error": iap_error,
    }
