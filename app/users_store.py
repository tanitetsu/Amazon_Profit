"""Operator / Drive settings (config/app_config.json or GCS).

User roster is NOT stored here — canonical list is AI_Cripping GCS
``setting/user-list.csv`` (see ``app.clipping_roster``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP_CONFIG_PATH = ROOT / "config" / "app_config.json"
# Legacy local path (migrated on first load if app_config missing).
_LEGACY_USERS_PATH = ROOT / "config" / "users.json"

_GS_URI_RE = re.compile(r"^gs://([^/]+)/(.+)$")


def app_config_gcs_uri() -> str | None:
    for key in ("APP_CONFIG_GCS_URI", "USERS_CONFIG_GCS_URI"):
        uri = (os.environ.get(key) or "").strip()
        if uri:
            return uri
    return None


def users_config_gcs_uri() -> str | None:
    """Alias kept for gmail_oauth bucket derivation."""
    return app_config_gcs_uri()


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    m = _GS_URI_RE.match(uri)
    if not m:
        raise ValueError(f"invalid config GCS URI: {uri!r} (want gs://bucket/path)")
    return m.group(1), m.group(2)


def _gcs_blob(uri: str):
    from google.cloud import storage

    bucket_name, blob_name = _parse_gs_uri(uri)
    client = storage.Client()
    return client.bucket(bucket_name).blob(blob_name)


def _strip_users_key(cfg: dict[str, Any]) -> dict[str, Any]:
    """Drop legacy users[] — roster lives in user-list.csv."""
    out = dict(cfg)
    out.pop("users", None)
    return out


def load_users_config() -> dict[str, Any]:
    """Load operator Drive settings (name kept for call-site compatibility)."""
    from app.sheets_retry import call_with_retry

    uri = app_config_gcs_uri()
    if uri:
        blob = _gcs_blob(uri)
        if not call_with_retry(blob.exists, label="app_config.gcs.exists"):
            raise FileNotFoundError(f"app config not found in GCS: {uri}")
        text = call_with_retry(
            lambda: blob.download_as_text(encoding="utf-8"),
            label="app_config.gcs.download",
        )
        return _strip_users_key(json.loads(text))
    if APP_CONFIG_PATH.is_file():
        return _strip_users_key(json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8")))
    if _LEGACY_USERS_PATH.is_file():
        cfg = _strip_users_key(
            json.loads(_LEGACY_USERS_PATH.read_text(encoding="utf-8"))
        )
        save_users_config(cfg)
        return cfg
    raise FileNotFoundError(f"app config missing: {APP_CONFIG_PATH}")


def save_users_config(cfg: dict[str, Any]) -> None:
    from app.sheets_retry import call_with_retry

    clean = _strip_users_key(cfg)
    text = json.dumps(clean, ensure_ascii=False, indent=2) + "\n"
    uri = app_config_gcs_uri()
    if uri:
        blob = _gcs_blob(uri)
        call_with_retry(
            lambda: blob.upload_from_string(
                text, content_type="application/json; charset=utf-8"
            ),
            label="app_config.gcs.upload",
        )
        return
    APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    APP_CONFIG_PATH.write_text(text, encoding="utf-8")
