"""AI_Cripping GCS roster + per-user seed (Book 4 / canonical user list)."""

from __future__ import annotations

import csv
import io
import logging
import os
from pathlib import Path
from typing import Any

from app.ai_roles import normalize_app_role
from app.schema import gmail_from_user_id, user_id_from_gmail

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
USER_LIST_OBJECT = "setting/user-list.csv"
QUITTED_USER_LIST_OBJECT = "setting/quitted_user.txt"
SETTING_PREFIX = "setting/"
SETTING_USERS_DIR = "setting/user/"
SETTING_QUITTED_DIR = "setting/quitted-user/"
QUITTED_ROOT_KEEP = f"{SETTING_QUITTED_DIR}.keep"
TEMPLATE_USER_ID = "26964u"  # deletion-protected Admin; not the seed copy source
SEED_USER_ID = "asamiodaka.b"
SETTING_TEMPLATE_DIR = "setting/template/"
DEFAULT_AMAZON_FEE = "10"
DEFAULT_PRICE_CSV_HEADER = "メルカリ販売価格,Amazon販売価格,送料(実際)\n"

# Book 4 — folders; user settings live under setting/user/{user_id}/
FOLDER_MARKERS = (
    "setting/user/{user_id}/.keep",
    "scraping-data/{user_id}/.keep",
    "log/{user_id}/.keep",
)


def clipping_gcs_bucket_name() -> str:
    return (
        os.environ.get("AIC_GCS_BUCKET")
        or os.environ.get("GCS_BUCKET")
        or "public-data-for-amazon"
    ).strip()


def _resolve_credentials_path() -> str | None:
    from app.gcs_credentials import resolve_gcs_credentials_path

    return resolve_gcs_credentials_path(root=ROOT)


def clipping_storage_bucket():
    from app.gcs_credentials import gcs_storage_client

    return gcs_storage_client().bucket(clipping_gcs_bucket_name())


def _read_text(bucket, object_name: str) -> str:
    # reload() before download: blob.exists()/cached metadata can pin an old
    # generation and return stale bytes right after upload_from_string.
    from google.api_core.exceptions import NotFound

    from app.sheets_retry import call_with_retry

    blob = bucket.blob(object_name)

    def _download() -> str:
        try:
            blob.reload()
        except NotFound:
            return ""
        return blob.download_as_text(encoding="utf-8")

    return call_with_retry(
        _download,
        label=f"clipping.gcs.download:{object_name}",
    )


def _write_text(bucket, object_name: str, text: str) -> None:
    from app.sheets_retry import call_with_retry

    call_with_retry(
        lambda: bucket.blob(object_name).upload_from_string(
            text, content_type="text/plain; charset=utf-8"
        ),
        label=f"clipping.gcs.upload:{object_name}",
    )


def _exists(bucket, object_name: str) -> bool:
    from app.sheets_retry import call_with_retry

    return bool(
        call_with_retry(
            lambda: bucket.blob(object_name).exists(),
            label=f"clipping.gcs.exists:{object_name}",
        )
    )


def _copy_blob(bucket, src: str, dest: str) -> None:
    from app.sheets_retry import call_with_retry

    src_blob = bucket.blob(src)
    call_with_retry(
        lambda: bucket.copy_blob(src_blob, bucket, dest),
        label=f"clipping.gcs.copy:{src}->{dest}",
    )


def _seed_user_setting_path(filename: str) -> str:
    return f"{SETTING_USERS_DIR}{SEED_USER_ID}/{filename}"


def _legacy_seed_user_setting_path(filename: str) -> str:
    """Pre-migration: setting/asamiodaka.b/…"""
    return f"{SETTING_PREFIX}{SEED_USER_ID}/{filename}"


def _shared_template_path(filename: str) -> str:
    return f"{SETTING_TEMPLATE_DIR}{filename}"


def _template_setting_path(filename: str) -> str:
    """Legacy Admin template path (fallback only)."""
    return f"{SETTING_USERS_DIR}{TEMPLATE_USER_ID}/{filename}"


def _legacy_template_setting_path(filename: str) -> str:
    """Pre-migration: setting/26964u/…"""
    return f"{SETTING_PREFIX}{TEMPLATE_USER_ID}/{filename}"


def _user_setting_prefix(user_id: str) -> str:
    return f"{SETTING_USERS_DIR}{user_id}/"


def _quitted_setting_prefix(user_id: str) -> str:
    return f"{SETTING_QUITTED_DIR}{user_id}/"


def _price_csv_header_only(text: str) -> str:
    for line in (text or "").lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped + "\n"
    return DEFAULT_PRICE_CSV_HEADER



def _ensure_quitted_root(bucket) -> None:
    if not _exists(bucket, QUITTED_ROOT_KEEP):
        _write_text(bucket, QUITTED_ROOT_KEEP, "")


def _prefix_has_objects(bucket, prefix: str) -> bool:
    for _ in bucket.list_blobs(prefix=prefix, max_results=1):
        return True
    return False


def _move_prefix(bucket, src_prefix: str, dest_prefix: str) -> int:
    """Copy all objects under src_prefix to dest_prefix, then delete sources."""
    from app.sheets_retry import call_with_retry

    moved = 0
    blobs = list(
        call_with_retry(
            lambda: list(bucket.list_blobs(prefix=src_prefix)),
            label=f"clipping.gcs.list:{src_prefix}",
        )
    )
    for src in blobs:
        rel = src.name[len(src_prefix) :]
        dest_name = f"{dest_prefix}{rel}"
        call_with_retry(
            lambda s=src, d=dest_name: bucket.copy_blob(s, bucket, d),
            label=f"clipping.gcs.copy:{src.name}",
        )
        call_with_retry(src.delete, label=f"clipping.gcs.delete:{src.name}")
        moved += 1
    return moved


def _delete_prefix(bucket, prefix: str) -> int:
    """Delete all objects under prefix. Returns count deleted."""
    from app.sheets_retry import call_with_retry

    deleted = 0
    blobs = list(
        call_with_retry(
            lambda: list(bucket.list_blobs(prefix=prefix)),
            label=f"clipping.gcs.list:{prefix}",
        )
    )
    for blob in blobs:
        call_with_retry(blob.delete, label=f"clipping.gcs.delete:{blob.name}")
        deleted += 1
    return deleted


def archive_user_settings_to_quitted(bucket, user_id: str) -> dict[str, Any]:
    """
    Move setting/user/{id}/ → setting/quitted-user/{id}/.
    Template Admin 26964u is forbidden.

    If both paths exist, prefer active content: replace quitted with active, then
    clear active (delete leaves settings only under quitted-user/).
    """
    uid = str(user_id).strip()
    if uid == TEMPLATE_USER_ID:
        raise ValueError(f"{TEMPLATE_USER_ID} の削除・退避は禁止です")
    _ensure_quitted_root(bucket)
    src = _user_setting_prefix(uid)
    dest = _quitted_setting_prefix(uid)
    src_exists = _prefix_has_objects(bucket, src)
    dest_exists = _prefix_has_objects(bucket, dest)
    if not src_exists:
        return {
            "user_id": uid,
            "archived": False,
            "reason": "no_active_settings",
            "moved_objects": 0,
        }
    replaced_quitted = 0
    if dest_exists:
        logger.warning(
            "Both active and quitted settings exist for %s; "
            "preferring active and replacing quitted",
            uid,
        )
        replaced_quitted = _delete_prefix(bucket, dest)
    moved = _move_prefix(bucket, src, dest)
    return {
        "user_id": uid,
        "archived": True,
        "reason": "moved_replacing_quitted" if replaced_quitted else "moved",
        "moved_objects": moved,
        "replaced_quitted_objects": replaced_quitted,
        "from": src,
        "to": dest,
    }


def restore_user_settings_from_quitted(bucket, user_id: str) -> dict[str, Any]:
    """
    Move setting/quitted-user/{id}/ → setting/user/{id}/ when quitted exists.
    If active already has objects, prefer active and leave quitted (anomaly).
    """
    uid = str(user_id).strip()
    src = _quitted_setting_prefix(uid)
    dest = _user_setting_prefix(uid)
    src_exists = _prefix_has_objects(bucket, src)
    dest_exists = _prefix_has_objects(bucket, dest)
    if not src_exists:
        return {
            "user_id": uid,
            "restored": False,
            "reason": "no_quitted_settings",
            "moved_objects": 0,
        }
    if dest_exists:
        logger.warning(
            "Active settings already exist for %s while quitted present; prefer active",
            uid,
        )
        return {
            "user_id": uid,
            "restored": False,
            "reason": "both_exist_prefer_active",
            "moved_objects": 0,
            "warning": "setting/user and setting/quitted-user both exist",
        }
    moved = _move_prefix(bucket, src, dest)
    return {
        "user_id": uid,
        "restored": True,
        "reason": "moved",
        "moved_objects": moved,
        "from": src,
        "to": dest,
    }


def parse_user_list_csv(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not (text or "").strip():
        return result
    normalized = strip_blank_lines(text)
    reader = csv.DictReader(io.StringIO(normalized))
    if not reader.fieldnames:
        return result
    fields = {str(h or "").strip(): h for h in reader.fieldnames}
    id_key = fields.get("ユーザーID") or fields.get("user_id") or fields.get("UserID")
    role_key = fields.get("ロール") or fields.get("role") or fields.get("Role")
    if not id_key or not role_key:
        for row in csv.reader(io.StringIO(normalized)):
            if not row or len(row) < 2:
                continue
            if str(row[0]).strip() in {"ユーザーID", "user_id"}:
                continue
            try:
                uid = str(row[0]).strip()
                if not uid:
                    continue
                result[uid] = normalize_app_role(row[1])
            except ValueError:
                continue
        return result
    for row in reader:
        try:
            uid = str(row.get(id_key) or "").strip()
            if not uid:
                continue
            result[uid] = normalize_app_role(row.get(role_key))
        except ValueError:
            continue
    return result


def user_list_csv_payload(users: dict[str, str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["ユーザーID", "ロール"])
    for uid in sorted(u for u in users if str(u).strip()):
        writer.writerow([uid, users[uid]])
    return strip_blank_lines(output.getvalue())


def strip_blank_lines(text: str) -> str:
    """Remove empty / whitespace-only lines; ensure trailing newline when non-empty."""
    lines = [
        ln.rstrip()
        for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if ln.strip() != ""
    ]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def parse_quitted_user_txt(text: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for ln in strip_blank_lines(text).split("\n"):
        uid = ln.strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        ids.append(uid)
    return sorted(ids, key=lambda s: s.lower())


def quitted_user_txt_payload(ids: list[str] | set[str]) -> str:
    cleaned = sorted(
        {str(u).strip() for u in ids if str(u).strip()},
        key=lambda s: s.lower(),
    )
    return strip_blank_lines("\n".join(cleaned))


def load_quitted_user_ids(bucket=None) -> list[str]:
    b = bucket or clipping_storage_bucket()
    return parse_quitted_user_txt(_read_text(b, QUITTED_USER_LIST_OBJECT))


def _write_quitted_user_ids(bucket, ids: list[str] | set[str]) -> None:
    _write_text(bucket, QUITTED_USER_LIST_OBJECT, quitted_user_txt_payload(ids))


def _write_user_list(
    bucket, users: dict[str, str], *, force: bool = False
) -> None:
    """Write canonical CSV. ``force=True`` skips read-compare (upsert path)."""
    payload = user_list_csv_payload(users)
    if not force:
        raw = _read_text(bucket, USER_LIST_OBJECT)
        if strip_blank_lines(raw) == payload:
            return
    _write_text(bucket, USER_LIST_OBJECT, payload)


def read_role_map(bucket=None) -> dict[str, str]:
    """Parse user-list.csv without rewriting (safe for confirm / concurrent use)."""
    b = bucket or clipping_storage_bucket()
    return parse_user_list_csv(_read_text(b, USER_LIST_OBJECT))


def load_role_map(bucket=None) -> dict[str, str]:
    """
    Load roster, optionally normalizing blank lines / bootstrap empty object.

    Never resets a non-empty but unparseable file to Admin-only (would wipe users).
    Callers that only need a snapshot should prefer ``read_role_map``.
    """
    b = bucket or clipping_storage_bucket()
    text = _read_text(b, USER_LIST_OBJECT)
    users = parse_user_list_csv(text)
    if not users:
        if (text or "").strip():
            raise RuntimeError(
                f"{USER_LIST_OBJECT} is present but unparseable; refusing to reset"
            )
        users = {TEMPLATE_USER_ID: "Admin"}
        _write_user_list(b, users, force=True)
    elif strip_blank_lines(text) != user_list_csv_payload(users):
        # Fix blank lines / reorder on read when dirty.
        _write_user_list(b, users, force=True)
    return users


def _confirm_user_in_roster(
    bucket,
    user_id: str,
    *,
    role: str | None = None,
    attempts: int = 6,
) -> bool:
    """Read-only confirm with short retries (avoids stale overwrite via load_role_map)."""
    import time

    uid = str(user_id).strip()
    for i in range(max(1, attempts)):
        users = read_role_map(bucket)
        if uid in users and (role is None or users[uid] == role):
            return True
        if i + 1 < attempts:
            time.sleep(0.15 * (i + 1))
    return False


def list_active_users(*, bucket=None) -> list[dict[str, Any]]:
    """Canonical active roster with derived gmail ({user_id}@gmail.com)."""
    role_map = load_role_map(bucket)
    rows: list[dict[str, Any]] = []
    for uid in sorted(role_map, key=lambda s: s.lower()):
        rows.append(
            {
                "user_id": uid,
                "role": role_map[uid],
                "gmail": gmail_from_user_id(uid),
            }
        )
    return rows


def upsert_clipping_user(gmail: str, role: str) -> dict[str, Any]:
    """Update canonical user-list.csv; drop from quitted_user.txt; restore settings."""
    uid = user_id_from_gmail(gmail)
    app_role = normalize_app_role(role)
    bucket = clipping_storage_bucket()
    users = load_role_map(bucket)
    users[uid] = app_role
    # Always upload after mutate — read-compare can see a stale pre-write generation
    # and skip, or a later load_role_map dirty-rewrite can clobber the upsert.
    _write_user_list(bucket, users, force=True)

    quitted = set(load_quitted_user_ids(bucket))
    removed_from_quitted = uid in quitted
    if removed_from_quitted:
        quitted.discard(uid)
        _write_quitted_user_ids(bucket, quitted)

    restored_info = restore_user_settings_from_quitted(bucket, uid)
    seeded = ensure_user_settings_seeded(bucket, uid, role=app_role)
    confirmed = _confirm_user_in_roster(bucket, uid, role=app_role)
    return {
        "user_id": uid,
        "role": app_role,
        "gmail": gmail_from_user_id(uid),
        "bucket": clipping_gcs_bucket_name(),
        "user_list": USER_LIST_OBJECT,
        "quitted_list": QUITTED_USER_LIST_OBJECT,
        "removed_from_quitted": removed_from_quitted,
        "restored_from_quitted": bool(restored_info.get("restored")),
        "restore": restored_info,
        "seeded": seeded,
        "confirmed_in_roster": confirmed,
    }


def remove_clipping_user(gmail: str) -> dict[str, Any]:
    """Remove from user-list.csv, append quitted_user.txt, archive settings folder."""
    uid = user_id_from_gmail(gmail)
    if uid == TEMPLATE_USER_ID:
        raise ValueError(f"{TEMPLATE_USER_ID} の削除・退避は禁止です")
    bucket = clipping_storage_bucket()
    users = load_role_map(bucket)
    existed = uid in users
    if existed:
        del users[uid]
        _write_user_list(bucket, users, force=True)

    quitted = set(load_quitted_user_ids(bucket))
    added_to_quitted = uid not in quitted
    quitted.add(uid)
    _write_quitted_user_ids(bucket, quitted)

    archive = archive_user_settings_to_quitted(bucket, uid)
    return {
        "user_id": uid,
        "removed_from_roster": existed,
        "added_to_quitted_list": added_to_quitted or True,
        "bucket": clipping_gcs_bucket_name(),
        "user_list": USER_LIST_OBJECT,
        "quitted_list": QUITTED_USER_LIST_OBJECT,
        "scraping_and_log_kept": True,
        "sheet_kept": True,
        "archive": archive,
    }


def _ensure_shared_templates(bucket) -> list[str]:
    """Create setting/template/ feed + header-only price from SEED_USER_ID when missing."""
    created: list[str] = []

    # Bootstrap seed-user exclude from legacy shared file once.
    seed_exclude = _seed_user_setting_path("excluded_user.txt")
    legacy_exclude = f"{SETTING_PREFIX}excluded_user.txt"
    if not _exists(bucket, seed_exclude) and _exists(bucket, legacy_exclude):
        try:
            _copy_blob(bucket, legacy_exclude, seed_exclude)
            created.append(seed_exclude)
        except Exception:
            logger.exception("Failed to bootstrap seed exclude from shared file")

    feed_dest = _shared_template_path("amazon_feed_template.json")
    if not _exists(bucket, feed_dest):
        copied = False
        for src in (
            _seed_user_setting_path("amazon_feed_template.json"),
            _legacy_seed_user_setting_path("amazon_feed_template.json"),
            f"{SETTING_PREFIX}amazon_feed_template.json",
        ):
            if _exists(bucket, src):
                try:
                    _copy_blob(bucket, src, feed_dest)
                    created.append(feed_dest)
                    copied = True
                    break
                except Exception:
                    logger.exception("Failed to seed shared feed from %s", src)
        if not copied:
            _write_text(bucket, feed_dest, "{}\n")
            created.append(feed_dest)

    price_dest = _shared_template_path("price.csv")
    if not _exists(bucket, price_dest):
        header = DEFAULT_PRICE_CSV_HEADER
        for src in (
            _seed_user_setting_path("price.csv"),
            _legacy_seed_user_setting_path("price.csv"),
            f"{SETTING_PREFIX}price.csv",
        ):
            if _exists(bucket, src):
                try:
                    header = _price_csv_header_only(_read_text(bucket, src))
                    break
                except Exception:
                    logger.exception("Failed to read price header from %s", src)
        _write_text(bucket, price_dest, header)
        created.append(price_dest)
    return created


def ensure_user_settings_seeded(
    bucket, user_id: str, *, role: str = "Normal"
) -> list[str]:
    """
    New-user GCS seed. Existing objects are never overwritten / recreated.
    Copy source: setting/user/asamiodaka.b/ (and setting/template/ for feed+price).
    ids_already_got.txt is copied from SEED_USER_ID for every role.
    """
    uid = str(user_id).strip()
    app_role = normalize_app_role(role)  # validate
    _ = app_role
    prefix = _user_setting_prefix(uid)
    created: list[str] = []

    for pattern in FOLDER_MARKERS:
        marker = pattern.format(user_id=uid)
        if not _exists(bucket, marker):
            _write_text(bucket, marker, "")
            created.append(marker)

    created.extend(_ensure_shared_templates(bucket))

    def _try_copy_sources(dest: str, sources: list[str], empty: str = "") -> None:
        if _exists(bucket, dest):
            return
        for src in sources:
            if not src or src == dest:
                continue
            if not _exists(bucket, src):
                continue
            try:
                _copy_blob(bucket, src, dest)
                created.append(dest)
                return
            except Exception:
                logger.exception("Failed to seed %s from %s", dest, src)
        _write_text(bucket, dest, empty)
        created.append(dest)

    for filename in (
        "ng_word.txt",
        "replace_word.txt",
        "excluded_user.txt",
    ):
        dest = f"{prefix}{filename}"
        _try_copy_sources(
            dest,
            [
                _seed_user_setting_path(filename),
                _legacy_seed_user_setting_path(filename),
                f"{SETTING_PREFIX}{uid}/{filename}",
            ],
            empty="",
        )

    ids_dest = f"{prefix}ids_already_got.txt"
    _try_copy_sources(
        ids_dest,
        [
            _seed_user_setting_path("ids_already_got.txt"),
            _legacy_seed_user_setting_path("ids_already_got.txt"),
        ],
        empty="",
    )

    feed_dest = f"{prefix}amazon_feed_template.json"
    _try_copy_sources(
        feed_dest,
        [_shared_template_path("amazon_feed_template.json")],
        empty="{}\n",
    )

    price_dest = f"{prefix}price.csv"
    _try_copy_sources(
        price_dest,
        [_shared_template_path("price.csv")],
        empty=DEFAULT_PRICE_CSV_HEADER,
    )

    for filename, empty_body in (
        ("search_conditions.json", "{}\n"),
        ("queue.txt", "{}\n"),
    ):
        dest = f"{prefix}{filename}"
        if not _exists(bucket, dest):
            _write_text(bucket, dest, empty_body)
            created.append(dest)

    fee_dest = f"{prefix}amazon-fee.txt"
    if not _exists(bucket, fee_dest):
        _write_text(bucket, fee_dest, f"{DEFAULT_AMAZON_FEE}\n")
        created.append(fee_dest)

    dl_dest = f"log/{uid}/log_download.csv"
    if not _exists(bucket, dl_dest):
        _write_text(bucket, dl_dest, "")
        created.append(dl_dest)

    return created
