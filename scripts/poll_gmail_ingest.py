#!/usr/bin/env python3
"""Local / Task Scheduler: poll Gmail ingest once (or loop every 5 minutes)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.mail_poll import poll_all_linked_users  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Poll Amazon mail ingest for linked users")
    p.add_argument(
        "--loop",
        action="store_true",
        help="Repeat forever every --interval-sec (default 300)",
    )
    p.add_argument("--interval-sec", type=int, default=300)
    p.add_argument("--max-results", type=int, default=100)
    args = p.parse_args()

    while True:
        summary = poll_all_linked_users(max_results_per_user=args.max_results)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        if not args.loop:
            return 0 if summary.get("ok") else 1
        time.sleep(max(30, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
