#!/usr/bin/env python3
"""One-time OAuth login as the operator Drive owner (26964u@gmail.com).

Scopes include Drive / Sheets / Apps Script / gmail.send (consent mail).
Re-run after scope changes, or when Cloud Run add-user / consent mail fails
with invalid_grant (refresh token expired or revoked). Upload the new
secrets/operator_token.json to OPERATOR_TOKEN_GCS_URI.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.google_clients import TOKEN_PATH, load_operator_credentials  # noqa: E402


def main() -> None:
    creds = load_operator_credentials()
    print(f"Operator token saved: {TOKEN_PATH}")
    print(f"Valid: {creds.valid}")


if __name__ == "__main__":
    main()
