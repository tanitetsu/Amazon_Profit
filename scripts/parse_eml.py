"""CLI: parse Amazon seller .eml files (no Sheets write)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mail_parser import parse_eml_dir, parse_eml_path, to_dict  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path, help=".eml file or directory")
    args = ap.parse_args()
    p = args.path
    if not p.exists():
        print(f"missing: {p}", file=sys.stderr)
        return 1
    if p.is_dir():
        parsed = parse_eml_dir(p)
    else:
        one = parse_eml_path(p)
        parsed = [one] if one else []
    print(json.dumps([to_dict(x) for x in parsed], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
