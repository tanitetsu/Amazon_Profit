"""Cross-instance lock so overlapping Scheduler ticks do not pile up."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from app.sheets_retry import call_with_retry

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
LOCAL_LOCK = SECRETS / "mail_poll_lock.json"

# Default under Cloud Run / Scheduler 900s so a dead holder expires before the next pile-up.
DEFAULT_TTL_SEC = 780


def _app_config_gcs_uri() -> str:
    return (
        (os.environ.get("APP_CONFIG_GCS_URI") or "").strip()
        or (os.environ.get("USERS_CONFIG_GCS_URI") or "").strip()
    )


def _lock_gcs_uri() -> str | None:
    explicit = (os.environ.get("MAIL_POLL_LOCK_GCS_URI") or "").strip()
    if explicit:
        return explicit
    users_uri = _app_config_gcs_uri()
    if users_uri.startswith("gs://"):
        bucket = users_uri[5:].split("/", 1)[0]
        return f"gs://{bucket}/mail_poll_lock.json"
    return None


def _gcs_blob(uri: str):
    from app.gcs_credentials import gcs_storage_client

    assert uri.startswith("gs://")
    rest = uri[5:]
    bucket_name, _, blob_name = rest.partition("/")
    return gcs_storage_client().bucket(bucket_name).blob(blob_name)


def _ttl_sec() -> int:
    raw = (os.environ.get("MAIL_POLL_LOCK_TTL_SEC") or "").strip()
    if not raw:
        return DEFAULT_TTL_SEC
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_TTL_SEC


def _read_lock_payload(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _lock_held(data: dict[str, Any] | None, *, now: float, ttl: int) -> bool:
    if not data:
        return False
    try:
        started = float(data.get("started_at_unix") or 0)
    except (TypeError, ValueError):
        return False
    if started <= 0:
        return False
    return (now - started) < ttl


def try_acquire_mail_poll_lock() -> tuple[bool, str | None, str | None]:
    """
    Acquire a short-lived poll lock.

    Returns (acquired, owner_token, reason_if_skipped).
    """
    ttl = _ttl_sec()
    now = time.time()
    owner = secrets.token_hex(8)
    payload = {
        "owner": owner,
        "started_at_unix": now,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ttl_sec": ttl,
    }
    uri = _lock_gcs_uri()
    if uri:
        blob = _gcs_blob(uri)
        existing_text: str | None = None
        if call_with_retry(blob.exists, label="mail_poll_lock.exists"):
            existing_text = call_with_retry(
                lambda: blob.download_as_text(encoding="utf-8"),
                label="mail_poll_lock.download",
            )
        existing = _read_lock_payload(existing_text)
        if _lock_held(existing, now=now, ttl=ttl):
            held_by = str((existing or {}).get("owner") or "?")
            return False, None, f"busy owner={held_by}"
        generation = getattr(blob, "generation", None)
        # Create-only when absent; otherwise overwrite expired lock.
        kwargs: dict[str, Any] = {
            "content_type": "application/json; charset=utf-8",
        }
        if existing_text is None and generation in (None, 0):
            kwargs["if_generation_match"] = 0

        def _upload() -> None:
            blob.upload_from_string(json.dumps(payload), **kwargs)

        try:
            call_with_retry(_upload, label="mail_poll_lock.upload")
        except Exception as exc:  # noqa: BLE001
            # Lost race to another instance.
            return False, None, f"lock_race: {exc}"
        return True, owner, None

    SECRETS.mkdir(parents=True, exist_ok=True)
    if LOCAL_LOCK.is_file():
        existing = _read_lock_payload(LOCAL_LOCK.read_text(encoding="utf-8"))
        if _lock_held(existing, now=now, ttl=ttl):
            held_by = str((existing or {}).get("owner") or "?")
            return False, None, f"busy owner={held_by}"
    LOCAL_LOCK.write_text(json.dumps(payload), encoding="utf-8")
    return True, owner, None


def release_mail_poll_lock(owner: str | None) -> None:
    if not owner:
        return
    uri = _lock_gcs_uri()
    if uri:
        blob = _gcs_blob(uri)
        try:
            if not call_with_retry(blob.exists, label="mail_poll_lock.exists"):
                return
            text = call_with_retry(
                lambda: blob.download_as_text(encoding="utf-8"),
                label="mail_poll_lock.download",
            )
            data = _read_lock_payload(text)
            if not data or data.get("owner") != owner:
                return

            def _delete() -> None:
                blob.delete()

            call_with_retry(_delete, label="mail_poll_lock.delete")
        except Exception:  # noqa: BLE001
            return
        return
    if not LOCAL_LOCK.is_file():
        return
    try:
        data = _read_lock_payload(LOCAL_LOCK.read_text(encoding="utf-8"))
        if data and data.get("owner") == owner:
            LOCAL_LOCK.unlink(missing_ok=True)
    except OSError:
        return
