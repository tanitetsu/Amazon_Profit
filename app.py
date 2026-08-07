"""Admin UI for amazon-profit-viewer (local operator console / Cloud Run)."""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request

from app.gmail_oauth import (
    build_gmail_auth_url,
    finish_gmail_oauth,
    has_gmail_token,
    parse_invite_token,
    public_base_url,
)
from app.provision import deprovision_user, list_user_workbooks, register_user
from app.users_store import load_users_config

ROOT = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
)
# Admin JS/CSS change often; avoid browsers keeping a stale admin.js that still
# does res.json() on Cloud Run's plain-text 504 ("upstream request timeout").
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = (
    (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    or "amazon-profit-viewer-local-dev-only"
)


@app.after_request
def _no_cache_admin_static(resp):
    path = request.path or ""
    if path.startswith("/static/admin.") or path.startswith("/static/mail_poll_runs."):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


def _app_surface() -> str:
    """
    admin  = IAP-backed console (default)
    public = unauthenticated Cloud Run: OAuth + mail-poll only
    """
    raw = (os.environ.get("APP_SURFACE") or "admin").strip().lower()
    if raw in ("public", "oauth", "public_oauth"):
        return "public"
    return "admin"


@app.before_request
def _enforce_public_surface() -> tuple[str, int] | None:
    if _app_surface() != "public":
        return None
    path = request.path or "/"
    if path == "/healthz" or path.startswith("/oauth/"):
        return None
    if path.startswith("/api/internal/mail-poll"):
        return None
    return ("Not found", 404)


def _request_base() -> str:
    return public_base_url(request.url_root)


def _authorization_response_url() -> str:
    """
    Full callback URL for oauthlib token exchange.

    Cloud Run terminates TLS; Flask often sees http://… which triggers
    oauthlib InsecureTransportError. Prefer PUBLIC_BASE_URL (https).
    """
    from urllib.parse import urlparse, urlunparse

    raw = request.url
    base = public_base_url(request.url_root).rstrip("/")
    if base.startswith("https://"):
        parsed = urlparse(raw)
        base_p = urlparse(base)
        return urlunparse(
            (
                base_p.scheme,
                base_p.netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
    if proto == "https" and raw.startswith("http://"):
        return "https://" + raw[len("http://") :]
    return raw


def _poll_secret_ok() -> bool:
    expected = (os.environ.get("MAIL_POLL_SECRET") or "").strip()
    if not expected:
        # Local convenience: allow without secret on 127.0.0.1 only
        if request.remote_addr in ("127.0.0.1", "::1"):
            return True
        return False
    got = (
        (request.headers.get("X-Mail-Poll-Secret") or "").strip()
        or (request.args.get("secret") or "").strip()
    )
    return bool(got) and got == expected


def _static_ver() -> int:
    # Bust CDN/browser cache when admin.js/css change (mtime).
    static_ver = 0
    for name in ("admin.js", "admin.css", "mail_poll_runs.js"):
        p = ROOT / "static" / name
        if p.is_file():
            static_ver = max(static_ver, int(p.stat().st_mtime))
    return static_ver or 1


@app.get("/")
def index():
    cfg = load_users_config()
    tid = (cfg.get("template_spreadsheet_id") or "").strip()
    template_url = (
        f"https://docs.google.com/spreadsheets/d/{tid}/edit" if tid else ""
    )
    return render_template(
        "admin.html",
        operator=cfg.get("operator_drive_email", ""),
        folder=cfg.get("folder_name", "User_Acounting"),
        template_url=template_url,
        static_ver=_static_ver(),
    )


@app.get("/mail-poll-runs")
def mail_poll_runs_page():
    cfg = load_users_config()
    return render_template(
        "mail_poll_runs.html",
        operator=cfg.get("operator_drive_email", ""),
        folder=cfg.get("folder_name", "User_Acounting"),
        static_ver=_static_ver(),
    )


@app.get("/api/mail-poll/runs")
def api_mail_poll_runs():
    """List persisted poll runs. Optional date (JST), user_id, errors_only filters."""
    try:
        from app.mail_poll_runs import list_runs, parse_errors_only, parse_run_date

        day = parse_run_date(request.args.get("date"))
        user_id = (request.args.get("user_id") or "").strip() or None
        errors_only = parse_errors_only(request.args.get("errors_only"))
        runs = list_runs(day, user_id=user_id, errors_only=errors_only)
        return jsonify(
            {
                "ok": True,
                "date": day.isoformat() if day else None,
                "user_id": user_id,
                "errors_only": errors_only,
                "count": len(runs),
                "runs": runs,
            }
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/mail-poll/runs/<run_id>")
def api_mail_poll_run_detail(run_id: str):
    try:
        from app.mail_poll_runs import get_run

        record = get_run(run_id)
        if not record:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "run": record})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/users")
def api_users():
    try:
        include = (request.args.get("include_template") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        users = list_user_workbooks(include_template=include)
        for u in users:
            g = (u.get("gmail") or "").strip()
            if g:
                u["gmail_linked"] = has_gmail_token(g)

        # Active roster only (user-list.csv). Quitted / deleted users are absent.
        roster: list[dict] = []
        roster_error = None
        try:
            from app.clipping_roster import load_role_map

            role_map = load_role_map()
            roster = [
                {"user_id": uid, "role": role}
                for uid, role in sorted(role_map.items(), key=lambda x: x[0].lower())
            ]
        except Exception as exc:  # noqa: BLE001
            roster_error = str(exc)

        payload: dict = {"ok": True, "users": users, "roster": roster}
        if roster_error:
            payload["roster_error"] = roster_error
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/users")
def api_add_user():
    """Provision sheet, then email the user a Gmail consent link (Plan B)."""
    body = request.get_json(silent=True) or {}
    gmail = (body.get("gmail") or "").strip()
    note = (body.get("note") or "").strip()
    role = (body.get("role") or "Normal").strip()
    existing_file_action = (body.get("existing_file_action") or "").strip() or None
    if not gmail:
        return jsonify({"ok": False, "error": "gmail is required"}), 400
    try:
        result = register_user(
            gmail,
            note=note,
            role=role,
            existing_file_action=existing_file_action,
            base_url=_request_base(),
        )
        return jsonify({"ok": True, "user": result})
    except Exception as exc:  # noqa: BLE001
        from app.template_ops import ProvisionError, WorkbookExistsError

        if isinstance(exc, WorkbookExistsError):
            return jsonify(
                {
                    "ok": False,
                    "code": "workbook_exists",
                    "error": str(exc),
                    "existing": {
                        "gmail": exc.gmail,
                        "title": exc.title,
                        "spreadsheet_id": exc.spreadsheet_id,
                        "url": exc.url,
                    },
                }
            ), 409
        if isinstance(exc, ValueError):
            return jsonify({"ok": False, "error": str(exc)}), 400
        traceback.print_exc()
        payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        if isinstance(exc, ProvisionError):
            payload["code"] = "provision_rolled_back"
            payload["rollback"] = exc.rollback
        return jsonify(payload), 500


@app.post("/api/users/resend-consent")
def api_resend_consent():
    body = request.get_json(silent=True) or {}
    gmail = (body.get("gmail") or "").strip()
    if not gmail:
        return jsonify({"ok": False, "error": "gmail is required"}), 400
    try:
        from app.consent_mail import send_gmail_consent_email

        sent = send_gmail_consent_email(gmail, base_url=_request_base())
        return jsonify({"ok": True, "consent_email": sent})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.delete("/api/users")
def api_delete_user():
    body = request.get_json(silent=True) or {}
    gmail = (body.get("gmail") or "").strip()
    if not gmail:
        return jsonify({"ok": False, "error": "gmail is required"}), 400
    try:
        result = deprovision_user(gmail)
        try:
            from app.gmail_oauth import delete_gmail_credentials

            result["gmail_token_removed"] = delete_gmail_credentials(gmail)
        except Exception:  # noqa: BLE001
            result["gmail_token_removed"] = False
        return jsonify({"ok": True, "user": result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/internal/mail-poll")
@app.get("/api/internal/mail-poll")
def api_mail_poll():
    """Cloud Scheduler / local cron. Protect with MAIL_POLL_SECRET (or localhost).

    Query:
      max_results — per-user Gmail fetch cap (default 100)
      gmail — optional; poll only this linked address (per-user jobs)
      workers — optional; parallel user polls (default MAIL_POLL_MAX_WORKERS)
    """
    if not _poll_secret_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        from app.mail_poll import poll_all_linked_users

        max_results = int(request.args.get("max_results") or 100)
        only = (request.args.get("gmail") or "").strip() or None
        workers_raw = (request.args.get("workers") or "").strip()
        workers = int(workers_raw) if workers_raw else None
        return jsonify(
            poll_all_linked_users(
                max_results_per_user=max_results,
                max_workers=workers,
                only_gmail=only,
            )
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/oauth/gmail/start")
def oauth_gmail_start():
    """
    End-user entry from consent email.
    ?invite=<signed>  (preferred) or legacy ?gmail=
    """
    invite = (request.args.get("invite") or "").strip()
    gmail = (request.args.get("gmail") or "").strip()
    try:
        if invite:
            gmail = parse_invite_token(invite)
        if not gmail:
            return "invite または gmail が必要です", 400
        auth_url, _ = build_gmail_auth_url(gmail, base_url=_request_base())
        return redirect(auth_url)
    except ValueError as exc:
        return render_template(
            "gmail_linked.html", gmail="", ingest=None, ingest_error=str(exc)
        ), 400
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return f"OAuth start failed: {exc}", 500


@app.get("/oauth/gmail/callback")
def oauth_gmail_callback():
    state = (request.args.get("state") or "").strip()
    if not state:
        return "missing state", 400
    if request.args.get("error"):
        return render_template(
            "gmail_linked.html",
            gmail="",
            ingest=None,
            ingest_error=(
                request.args.get("error_description")
                or request.args.get("error")
                or "denied"
            ),
        ), 400
    try:
        linked = finish_gmail_oauth(
            state=state,
            authorization_response=_authorization_response_url(),
            base_url=_request_base(),
        )
        gmail = linked["gmail"]
        ingest = None
        ingest_error = None
        try:
            from app.mail_ingest import ingest_user_mail

            ingest = ingest_user_mail(gmail)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            ingest_error = str(exc)
        return render_template(
            "gmail_linked.html",
            gmail=gmail,
            ingest=ingest,
            ingest_error=ingest_error,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return render_template(
            "gmail_linked.html",
            gmail="",
            ingest=None,
            ingest_error=str(exc),
        ), 400


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


def main() -> None:
    on_cloud_run = bool(os.environ.get("K_SERVICE"))
    if on_cloud_run or (os.environ.get("ADMIN_BIND_ALL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        host = "0.0.0.0"
        port = int(os.environ.get("PORT") or "8080")
    else:
        host = "127.0.0.1"
        port = int(os.environ.get("PORT") or "5055")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
