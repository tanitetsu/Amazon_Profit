#!/usr/bin/env python3
"""
Migrate setting/{user_id}/ → setting/user/{user_id}/ and optionally delete legacy flat.

Usage:
  python scripts/migrate_gcs_setting_user_prefix.py           # migrate only
  python scripts/migrate_gcs_setting_user_prefix.py --delete-legacy-flat
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clipping_roster import (  # noqa: E402
    SETTING_PREFIX,
    SETTING_USERS_DIR,
    clipping_gcs_bucket_name,
    clipping_storage_bucket,
)

RESERVED = frozenset({"user", "prompt", "market-info"})

LEGACY_FLAT_OBJECTS = (
    "setting/ng_word.txt",
    "setting/replace_word.txt",
    "setting/search_conditions.json",
    "setting/queue.txt",
    "setting/price.csv",
    "setting/amazon_feed_template.json",
    "setting/ids_already_got.txt",
)


def migrate_nested(bucket) -> dict[str, int]:
    """Copy setting/{uid}/… → setting/user/{uid}/… when dest missing; then delete sources."""
    moved = 0
    skipped = 0
    deleted_src = 0
    # Collect user segments under setting/ that are not reserved
    prefixes: set[str] = set()
    for blob in bucket.list_blobs(prefix=SETTING_PREFIX):
        name = blob.name or ""
        rest = name[len(SETTING_PREFIX) :]
        if not rest or "/" not in rest:
            continue
        seg, _, _tail = rest.partition("/")
        if seg in RESERVED:
            continue
        prefixes.add(seg)

    for uid in sorted(prefixes):
        old_prefix = f"{SETTING_PREFIX}{uid}/"
        new_prefix = f"{SETTING_USERS_DIR}{uid}/"
        for blob in bucket.list_blobs(prefix=old_prefix):
            src = blob.name
            rel = src[len(old_prefix) :]
            if not rel:
                continue
            dest = f"{new_prefix}{rel}"
            if bucket.blob(dest).exists():
                skipped += 1
            else:
                bucket.copy_blob(blob, bucket, dest)
                moved += 1
                print(f"  copy {src} → {dest}", flush=True)
            blob.delete()
            deleted_src += 1
            print(f"  del  {src}", flush=True)
    return {"users": len(prefixes), "moved": moved, "skipped_dest": skipped, "deleted_src": deleted_src}


def delete_legacy_flat(bucket) -> list[str]:
    removed: list[str] = []
    for obj in LEGACY_FLAT_OBJECTS:
        blob = bucket.blob(obj)
        if blob.exists():
            blob.delete()
            removed.append(obj)
            print(f"  del flat {obj}", flush=True)
    return removed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--delete-legacy-flat",
        action="store_true",
        help="Delete setting/*.txt|json|csv legacy flat objects (option A)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    bucket = clipping_storage_bucket()
    print(f"bucket={clipping_gcs_bucket_name()}", flush=True)
    if args.dry_run:
        print("dry-run: listing candidate user segments…", flush=True)
        segs: set[str] = set()
        for blob in bucket.list_blobs(prefix=SETTING_PREFIX):
            rest = (blob.name or "")[len(SETTING_PREFIX) :]
            if "/" in rest:
                seg = rest.split("/", 1)[0]
                if seg not in RESERVED:
                    segs.add(seg)
        print("legacy nested users:", sorted(segs), flush=True)
        print("legacy flat present:", flush=True)
        for obj in LEGACY_FLAT_OBJECTS:
            print(f"  {obj}: {bucket.blob(obj).exists()}", flush=True)
        return 0

    stats = migrate_nested(bucket)
    print("migrate:", stats, flush=True)
    if args.delete_legacy_flat:
        removed = delete_legacy_flat(bucket)
        print("deleted_legacy_flat:", removed, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
