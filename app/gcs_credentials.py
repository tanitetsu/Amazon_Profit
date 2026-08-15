"""Resolve GCS SA credentials from env (file path or JSON body).

Cursor Environment secrets are a text field, so ``AIC_GCS_CREDENTIALS`` may be
the service-account JSON itself rather than a filesystem path. Never log the
JSON body.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
_ENV_KEYS = ("AIC_GCS_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS")
_MATERIALIZED_NAME = "amazon-profit-aic-gcs-sa.json"


def _looks_like_json_object(raw: str) -> bool:
    return raw.startswith("{")


def materialize_credentials_value(raw: str) -> str | None:
    """Return a filesystem path for a path-or-JSON env value. Does not log raw."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if Path(raw).is_file():
        return raw
    if not _looks_like_json_object(raw):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("GCS credentials env value looks like JSON but is not valid JSON")
        return None
    if not isinstance(parsed, dict):
        logger.warning("GCS credentials env value is JSON but not an object")
        return None
    return _write_materialized(raw)


def _write_materialized(raw: str) -> str:
    dest = Path(tempfile.gettempdir()) / _MATERIALIZED_NAME
    dest.write_text(raw, encoding="utf-8")
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    return str(dest)


def apply_resolved_credentials_env(path: str) -> None:
    """Point ADC file-path consumers at the resolved SA file (never JSON text)."""
    current = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if current != path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path


def resolve_gcs_credentials_path(*, root: Path | None = None) -> str | None:
    """Discover SA JSON: env path, env JSON body, then local sibling files."""
    root = root or ROOT
    for key in _ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        path = materialize_credentials_value(raw)
        if path:
            apply_resolved_credentials_env(path)
            return path
        if not _looks_like_json_object(raw):
            logger.warning(
                "GCS credentials env %s is set but is not a readable file or JSON object",
                key,
            )
    sibling = root.parent / "AI_Cripping" / "secrets" / "gcs_service_account.json"
    if sibling.is_file():
        apply_resolved_credentials_env(str(sibling))
        return str(sibling)
    local = root / "secrets" / "aic_gcs_service_account.json"
    if local.is_file():
        apply_resolved_credentials_env(str(local))
        return str(local)
    return None


def gcs_storage_client():
    """storage.Client using the resolved SA file when one exists."""
    from google.cloud import storage

    cred_path = resolve_gcs_credentials_path()
    if cred_path:
        return storage.Client.from_service_account_json(cred_path)
    return storage.Client()
