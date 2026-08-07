"""Gunicorn / Cloud Run entry (avoids import clash with package name `app`)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from werkzeug.middleware.proxy_fix import ProxyFix

_spec = importlib.util.spec_from_file_location(
    "amazon_profit_admin",
    Path(__file__).resolve().parent / "app.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("failed to load app.py as WSGI module")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
app = _mod.app
# Cloud Run / GFE: trust one hop of X-Forwarded-* so request.url is https
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
