#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for amazon-profit-mail (Flask admin console).
set -euo pipefail
cd "$(dirname "$0")/.."

# The default image ships Python 3.12 but not the venv module.
if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv
fi

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -r requirements-admin.txt pytest

# Local-dev operator settings. Production reads the canonical copy from GCS via
# APP_CONFIG_GCS_URI; locally the app falls back to config/app_config.json
# (gitignored). Seed it from the checked-in example when missing.
if [ ! -f config/app_config.json ]; then
  cp config/app_config.example.json config/app_config.json
fi

echo "amazon-profit-mail environment ready."
