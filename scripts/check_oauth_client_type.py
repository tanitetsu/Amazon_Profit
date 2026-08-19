#!/usr/bin/env python3
"""Print OAuth client file type only (web vs installed). Never prints secrets.

Run on the PC before scripts/oauth_operator.py:

  .\\.venv\\Scripts\\python.exe scripts\\check_oauth_client_type.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
FILES = (
    SECRETS / "oauth_client_desktop.json",
    SECRETS / "oauth_client.json",
)


def _kind(path: Path) -> str:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing"
    except json.JSONDecodeError:
        return "invalid-json"
    if not isinstance(info, dict):
        return "not-object"
    if "installed" in info:
        return "installed (Desktop — oauth_operator.py 用)"
    if "web" in info:
        return "web (Web — ユーザー同意メール用。運営ログインには使えない)"
    return "unknown-keys:" + ",".join(sorted(info.keys()))


def main() -> int:
    print(f"secrets dir: {SECRETS}")
    desktop_ok = False
    for path in FILES:
        rel = path.relative_to(ROOT)
        if not path.is_file():
            print(f"{rel}: ない")
            continue
        kind = _kind(path)
        print(f"{rel}: {kind}")
        if kind.startswith("installed"):
            desktop_ok = True
    print()
    if desktop_ok:
        print("OK: Desktop JSON があります。scripts/oauth_operator.py を再実行できます。")
        return 0
    print(
        "NG: Desktop JSON がありません。GCP 認証情報で種類「デスクトップアプリ」の "
        "クライアントを新規作成し、ダウンロードしたファイルを "
        "secrets/oauth_client_desktop.json として保存してください。"
        " oauth_client.json は上書きしないでください。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
