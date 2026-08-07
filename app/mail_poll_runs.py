"""Persist and query mail-poll execution records (local + GCS)."""

from __future__ import annotations

import json
import os
import re
import secrets
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.schema import user_id_from_gmail
from app.sheets_retry import call_with_retry

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
RUNS_DIR = SECRETS / "mail_poll_runs"
# Fixed offset avoids requiring the tzdata package on Windows/dev images.
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{6}$")
DEFAULT_RETENTION_DAYS = 30


def _resolve_gcs_credentials_path() -> str | None:
    """Same local SA discovery as clipping_roster (admin bucket is readable by it)."""
    for key in ("AIC_GCS_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS"):
        raw = (os.environ.get(key) or "").strip()
        if raw and Path(raw).is_file():
            return raw
    sibling = ROOT.parent / "AI_Cripping" / "secrets" / "gcs_service_account.json"
    if sibling.is_file():
        return str(sibling)
    local = SECRETS / "aic_gcs_service_account.json"
    if local.is_file():
        return str(local)
    return None


def _storage_client():
    from google.cloud import storage

    cred_path = _resolve_gcs_credentials_path()
    if cred_path:
        return storage.Client.from_service_account_json(cred_path)
    return storage.Client()


def _app_config_gcs_uri() -> str:
    return (
        (os.environ.get("APP_CONFIG_GCS_URI") or "").strip()
        or (os.environ.get("USERS_CONFIG_GCS_URI") or "").strip()
    )


def _runs_gcs_prefix() -> str | None:
    explicit = (os.environ.get("MAIL_POLL_RUNS_GCS_PREFIX") or "").strip().rstrip("/")
    if explicit:
        return explicit
    users_uri = _app_config_gcs_uri()
    if users_uri.startswith("gs://"):
        bucket = users_uri[5:].split("/", 1)[0]
        return f"gs://{bucket}/mail_poll_runs"
    return None


def _gcs_blob(uri: str):
    assert uri.startswith("gs://")
    rest = uri[5:]
    bucket_name, _, blob_name = rest.partition("/")
    return _storage_client().bucket(bucket_name).blob(blob_name)


def _gcs_bucket_and_prefix(prefix_uri: str) -> tuple[Any, str]:
    assert prefix_uri.startswith("gs://")
    rest = prefix_uri[5:]
    bucket_name, _, blob_prefix = rest.partition("/")
    return _storage_client().bucket(bucket_name), blob_prefix.strip("/")


def now_jst() -> datetime:
    return datetime.now(JST)


def parse_run_date(value: str | None) -> date | None:
    """Parse YYYY-MM-DD. Empty/None means no date filter (retention window)."""
    raw = (value or "").strip()
    if not raw:
        return None
    if not _DATE_RE.match(raw):
        raise ValueError("date must be YYYY-MM-DD")
    return date.fromisoformat(raw)


# Alias kept for callers that used the older name.
parse_optional_run_date = parse_run_date


def parse_errors_only(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _run_matches_errors(rec: dict[str, Any]) -> bool:
    """True when the (possibly user-slimmed) run has any failed row or run-level failure."""
    rows = [r for r in (rec.get("results") or []) if isinstance(r, dict)]
    if rows:
        return any(not r.get("ok") for r in rows)
    if int(rec.get("errors") or 0) > 0:
        return True
    return not bool(rec.get("ok"))


def _apply_user_filter(
    records: list[dict[str, Any]], user_id: str | None
) -> list[dict[str, Any]]:
    uid = (user_id or "").strip()
    if not uid:
        return records
    filtered: list[dict[str, Any]] = []
    for rec in records:
        rows = [
            r
            for r in (rec.get("results") or [])
            if isinstance(r, dict) and (r.get("user_id") or "") == uid
        ]
        if not rows:
            continue
        slim = dict(rec)
        slim["results"] = rows
        slim["matched_user_id"] = uid
        filtered.append(slim)
    return filtered


def _sanitize_operator(op: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(op, dict):
        return None
    out: dict[str, Any] = {"ok": bool(op.get("ok", True))}
    if op.get("expiry"):
        out["expiry"] = op["expiry"]
    if op.get("error"):
        out["error"] = str(op["error"])
    return out


def _sanitize_user_row(row: dict[str, Any]) -> dict[str, Any]:
    gmail = (row.get("gmail") or "").strip().lower()
    out: dict[str, Any] = {
        "gmail": gmail,
        "user_id": user_id_from_gmail(gmail) if gmail else "",
        "ok": bool(row.get("ok")),
    }
    for key in ("processed", "parse_miss", "skipped_seen"):
        if key in row and row[key] is not None:
            out[key] = row[key]
    if row.get("error"):
        out["error"] = str(row["error"])
    tok = row.get("token")
    if isinstance(tok, dict):
        out["token_refreshed"] = bool(tok.get("refreshed", True))
        if tok.get("expiry"):
            out["token_expiry"] = tok["expiry"]
        if tok.get("error"):
            out["token_error"] = str(tok["error"])
    return out


def build_run_record(summary: dict[str, Any]) -> dict[str, Any]:
    """Turn poll_all_linked_users / poll_one_linked_user output into a stored record."""
    finished = summary.get("finished_at") or now_jst().isoformat(timespec="seconds")
    # Prefer JST wall-clock for folder / run_id even if finished_at is naive UTC-ish.
    stamp = now_jst()
    run_id = f"{stamp.strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"
    results = [_sanitize_user_row(r) for r in (summary.get("results") or []) if isinstance(r, dict)]
    only = (summary.get("only_gmail") or "").strip().lower() or None
    return {
        "run_id": run_id,
        "date": stamp.date().isoformat(),
        "started_at": summary.get("started_at"),
        "finished_at": finished,
        "ok": bool(summary.get("ok")),
        "linked_users": int(summary.get("linked_users") or len(results)),
        "errors": int(summary.get("errors") or 0),
        "max_workers": summary.get("max_workers"),
        "only_gmail": only,
        "operator_token": _sanitize_operator(summary.get("operator_token")),
        "results": results,
    }


def _local_day_dir(day: date) -> Path:
    return RUNS_DIR / day.isoformat()


def _local_run_path(day: date, run_id: str) -> Path:
    return _local_day_dir(day) / f"{run_id}.json"


def _gcs_run_uri(day: date, run_id: str) -> str | None:
    prefix = _runs_gcs_prefix()
    if not prefix:
        return None
    return f"{prefix}/{day.isoformat()}/{run_id}.json"


def save_poll_run(summary: dict[str, Any], *, retain_days: int | None = None) -> dict[str, Any] | None:
    """
    Persist a poll summary. Returns the stored record, or None on failure
    (never raises — poll response must still succeed).
    """
    try:
        record = build_run_record(summary)
        day = date.fromisoformat(record["date"])
        run_id = record["run_id"]
        payload = json.dumps(record, ensure_ascii=False, indent=2)
        uri = _gcs_run_uri(day, run_id)
        if uri:
            call_with_retry(
                lambda: _gcs_blob(uri).upload_from_string(
                    payload, content_type="application/json; charset=utf-8"
                ),
                label="mail_poll_runs.upload",
            )
        else:
            day_dir = _local_day_dir(day)
            day_dir.mkdir(parents=True, exist_ok=True)
            _local_run_path(day, run_id).write_text(payload, encoding="utf-8")

        days = DEFAULT_RETENTION_DAYS if retain_days is None else max(1, int(retain_days))
        try:
            prune_old_runs(retain_days=days)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        return record
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return None


def _load_json_text(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_runs_from_blobs(blobs: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for blob in blobs:
        name = getattr(blob, "name", "") or ""
        if not name.endswith(".json"):
            continue

        def _download(b=blob) -> str:
            return b.download_as_text(encoding="utf-8")

        text = call_with_retry(_download, label="mail_poll_runs.download")
        rec = _load_json_text(text)
        if rec:
            records.append(rec)
    return records


def _load_runs_local_day(day: date) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    day_dir = _local_day_dir(day)
    if not day_dir.is_dir():
        return records
    for path in sorted(day_dir.glob("*.json"), reverse=True):
        try:
            rec = _load_json_text(path.read_text(encoding="utf-8"))
        except Exception:
            rec = None
        if rec:
            records.append(rec)
    return records


def list_runs_for_date(
    day: date,
    *,
    user_id: str | None = None,
    errors_only: bool = False,
) -> list[dict[str, Any]]:
    """Newest-first runs for one calendar day (JST folder). Optional user / errors filters."""
    records: list[dict[str, Any]] = []
    prefix = _runs_gcs_prefix()
    if prefix:
        bucket, base = _gcs_bucket_and_prefix(prefix)
        day_prefix = f"{base}/{day.isoformat()}/" if base else f"{day.isoformat()}/"

        def _list() -> list[Any]:
            return list(bucket.list_blobs(prefix=day_prefix))

        blobs = call_with_retry(_list, label="mail_poll_runs.list")
        records = _load_runs_from_blobs(blobs)
    else:
        records = _load_runs_local_day(day)

    records = _apply_user_filter(records, user_id)
    if errors_only:
        records = [r for r in records if _run_matches_errors(r)]
    records.sort(key=lambda r: str(r.get("run_id") or ""), reverse=True)
    return records


def list_runs(
    day: date | None = None,
    *,
    user_id: str | None = None,
    errors_only: bool = False,
    retain_days: int | None = None,
) -> list[dict[str, Any]]:
    """
    Newest-first runs. When day is None, scan the retention window (JST, ~30 days).
    """
    if day is not None:
        return list_runs_for_date(day, user_id=user_id, errors_only=errors_only)

    days = max(1, DEFAULT_RETENTION_DAYS if retain_days is None else int(retain_days))
    today = now_jst().date()
    allowed = {today - timedelta(days=i) for i in range(days)}
    records: list[dict[str, Any]] = []
    prefix = _runs_gcs_prefix()
    if prefix:
        bucket, base = _gcs_bucket_and_prefix(prefix)
        root_prefix = f"{base}/" if base else ""

        def _list() -> list[Any]:
            return list(bucket.list_blobs(prefix=root_prefix))

        blobs = call_with_retry(_list, label="mail_poll_runs.list_all")
        kept: list[Any] = []
        for blob in blobs:
            name = getattr(blob, "name", "") or ""
            if not name.endswith(".json"):
                continue
            # …/YYYY-MM-DD/run_id.json
            parts = name.strip("/").split("/")
            if len(parts) < 2:
                continue
            day_s = parts[-2]
            if not _DATE_RE.match(day_s):
                continue
            try:
                blob_day = date.fromisoformat(day_s)
            except ValueError:
                continue
            if blob_day in allowed:
                kept.append(blob)
        records = _load_runs_from_blobs(kept)
    else:
        for d in sorted(allowed, reverse=True):
            records.extend(_load_runs_local_day(d))

    records = _apply_user_filter(records, user_id)
    if errors_only:
        records = [r for r in records if _run_matches_errors(r)]
    records.sort(key=lambda r: str(r.get("run_id") or ""), reverse=True)
    return records


def get_run(run_id: str) -> dict[str, Any] | None:
    rid = (run_id or "").strip()
    if not _RUN_ID_RE.match(rid):
        raise ValueError("invalid run_id")
    day = date(int(rid[0:4]), int(rid[4:6]), int(rid[6:8]))
    uri = _gcs_run_uri(day, rid)
    if uri:
        blob = _gcs_blob(uri)
        if not call_with_retry(blob.exists, label="mail_poll_runs.exists"):
            return None
        text = call_with_retry(
            lambda: blob.download_as_text(encoding="utf-8"),
            label="mail_poll_runs.download",
        )
        return _load_json_text(text)
    path = _local_run_path(day, rid)
    if not path.is_file():
        return None
    return _load_json_text(path.read_text(encoding="utf-8"))


def prune_old_runs(*, retain_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """
    Delete the calendar day that just aged out of the retention window (JST).
    Called after each save so history slides without listing the whole prefix.
    """
    old_day = now_jst().date() - timedelta(days=max(1, retain_days))
    deleted = 0
    prefix = _runs_gcs_prefix()
    if prefix:
        bucket, base = _gcs_bucket_and_prefix(prefix)
        day_prefix = (
            f"{base}/{old_day.isoformat()}/" if base else f"{old_day.isoformat()}/"
        )

        def _list() -> list[Any]:
            return list(bucket.list_blobs(prefix=day_prefix))

        blobs = call_with_retry(_list, label="mail_poll_runs.prune_list")
        for blob in blobs:
            call_with_retry(blob.delete, label="mail_poll_runs.prune_delete")
            deleted += 1
        return deleted

    day_dir = _local_day_dir(old_day)
    if not day_dir.is_dir():
        return 0
    for path in day_dir.glob("*.json"):
        path.unlink(missing_ok=True)
        deleted += 1
    try:
        day_dir.rmdir()
    except OSError:
        pass
    return deleted
