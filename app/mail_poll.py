"""Poll Gmail-linked users: refresh tokens, then ingest Amazon mails.

Sheet range locks are never applied on this path (ingest writes values only).
``mail_poll_lock`` is a cross-instance mutex, not a spreadsheet cell lock.
"""

from __future__ import annotations

import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from app.clipping_roster import list_active_users
from app.gmail_oauth import has_gmail_token, list_linked_gmails, maintain_gmail_token
from app.mail_ingest import ingest_user_mail
from app.mail_poll_lock import release_mail_poll_lock, try_acquire_mail_poll_lock
from app.mail_poll_runs import save_poll_run
from app.token_maintain import maintain_operator_oauth_token


def _roster_gmails() -> list[str]:
    return [u["gmail"] for u in list_active_users() if u.get("gmail")]


def _max_workers(requested: int | None = None) -> int:
    # Default 1: httplib2 / googleapiclient are not reliably thread-safe; parallel
    # user polls caused BrokenPipeError storms and 900s hangs on Cloud Run.
    if requested is not None:
        return max(1, min(int(requested), 8))
    raw = (os.environ.get("MAIL_POLL_MAX_WORKERS") or "1").strip()
    try:
        return max(1, min(int(raw), 8))
    except ValueError:
        return 1


def _persist_run(summary: dict[str, Any], *, only_gmail: str | None = None) -> dict[str, Any]:
    if only_gmail:
        summary = {**summary, "only_gmail": only_gmail}
    record = save_poll_run(summary)
    if record and record.get("run_id"):
        summary = {**summary, "run_id": record["run_id"]}
    return summary


def poll_one_linked_user(
    gmail: str,
    *,
    max_results_per_user: int = 100,
    maintain_operator: bool = True,
) -> dict[str, Any]:
    """Refresh token + ingest for a single roster-linked Gmail."""
    gmail = (gmail or "").strip().lower()
    started = datetime.now().isoformat(timespec="seconds")
    operator: dict[str, Any] | None = None
    if maintain_operator:
        operator = maintain_operator_oauth_token()

    row: dict[str, Any] = {"gmail": gmail}
    try:
        if not has_gmail_token(gmail):
            row["ok"] = False
            row["error"] = "gmail_not_linked"
            return _persist_run(
                {
                    "ok": False,
                    "started_at": started,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "linked_users": 0,
                    "errors": 1,
                    "operator_token": operator,
                    "results": [row],
                },
                only_gmail=gmail,
            )
        row["token"] = maintain_gmail_token(gmail)
        summary = ingest_user_mail(gmail, max_results=max_results_per_user)
        row["ok"] = True
        row["processed"] = summary.get("processed", 0)
        row["parse_miss"] = summary.get("parse_miss", 0)
        row["skipped_seen"] = summary.get("skipped_seen", 0)
        if summary.get("truncated"):
            row["truncated"] = True
        errors = 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        row["ok"] = False
        row["error"] = str(exc)
        errors = 1

    return _persist_run(
        {
            "ok": errors == 0 and bool((operator or {}).get("ok", True)),
            "started_at": started,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "linked_users": 1,
            "errors": errors,
            "operator_token": operator,
            "results": [row],
        },
        only_gmail=gmail,
    )


def _poll_user_row(gmail: str, *, max_results_per_user: int) -> dict[str, Any]:
    row: dict[str, Any] = {"gmail": gmail}
    try:
        row["token"] = maintain_gmail_token(gmail)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        row["ok"] = False
        row["error"] = f"token_refresh: {exc}"
        return row
    try:
        summary = ingest_user_mail(gmail, max_results=max_results_per_user)
        row["ok"] = True
        row["processed"] = summary.get("processed", 0)
        row["parse_miss"] = summary.get("parse_miss", 0)
        row["skipped_seen"] = summary.get("skipped_seen", 0)
        if summary.get("truncated"):
            row["truncated"] = True
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        row["ok"] = False
        row["error"] = str(exc)
    return row


def poll_all_linked_users(
    *,
    max_results_per_user: int = 100,
    max_workers: int | None = None,
    only_gmail: str | None = None,
    skip_lock: bool = False,
) -> dict[str, Any]:
    """
    For each roster user with a stored Gmail token:
      1) refresh access token (keeps refresh_token in use)
      2) ingest Amazon mails
    Plus operator OAuth keep-alive for consent-mail / local Drive.

    Do not put folder-wide cancel/status sync here — it OOMs / times out as
    user count grows. Run that (if ever needed) as a separate low-frequency job.

    only_gmail: limit to one address (per-user Scheduler / manual).
    max_workers: parallel user polls (I/O overlap). Default MAIL_POLL_MAX_WORKERS=1.
    """
    if only_gmail:
        return poll_one_linked_user(
            only_gmail,
            max_results_per_user=max_results_per_user,
            maintain_operator=True,
        )

    started = datetime.now().isoformat(timespec="seconds")
    owner: str | None = None
    if not skip_lock:
        acquired, owner, reason = try_acquire_mail_poll_lock()
        if not acquired:
            return _persist_run(
                {
                    "ok": True,
                    "skipped": True,
                    "skip_reason": reason or "busy",
                    "started_at": started,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "linked_users": 0,
                    "errors": 0,
                    "results": [],
                }
            )

    try:
        operator = maintain_operator_oauth_token()

        linked = list_linked_gmails(_roster_gmails())
        workers = _max_workers(max_workers)
        results: list[dict[str, Any]] = []
        errors = 0

        if workers <= 1 or len(linked) <= 1:
            for gmail in linked:
                row = _poll_user_row(gmail, max_results_per_user=max_results_per_user)
                if not row.get("ok"):
                    errors += 1
                results.append(row)
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(linked))) as pool:
                futs = {
                    pool.submit(
                        _poll_user_row, gmail, max_results_per_user=max_results_per_user
                    ): gmail
                    for gmail in linked
                }
                for fut in as_completed(futs):
                    row = fut.result()
                    if not row.get("ok"):
                        errors += 1
                    results.append(row)
            # Stable order matching roster linkage for logs / diffs.
            by_gmail = {r.get("gmail"): r for r in results}
            results = [by_gmail[g] for g in linked if g in by_gmail]

        return _persist_run(
            {
                "ok": errors == 0 and bool(operator.get("ok", True)),
                "started_at": started,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "linked_users": len(linked),
                "errors": errors,
                "max_workers": workers,
                "operator_token": operator,
                "results": results,
            }
        )
    finally:
        release_mail_poll_lock(owner)
