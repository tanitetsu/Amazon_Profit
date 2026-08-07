"""Fetch Amazon seller notification messages via Gmail API."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Iterator

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.sheets_retry import execute_with_retry

# Seller Central / Amazon JP notifications we care about.
# Exclude 「返金手続き開始」 per sheet-and-mail-spec.
GMAIL_SEARCH_QUERY = (
    "from:amazon.co.jp "
    "(subject:注文確定 OR subject:キャンセルリクエスト OR subject:キャンセルの依頼 "
    "OR subject:返品承認 OR subject:A-to-Z OR subject:マーケットプレイス保証 "
    "OR subject:保証による保護) "
    "-subject:返金手続き開始"
)

# Keep Gmail gets from burning the whole Cloud Run request on BrokenPipe storms.
_GMAIL_RETRY_ATTEMPTS = 5
_GMAIL_RETRY_MAX_DELAY = 20.0


@dataclass(frozen=True)
class GmailMessage:
    id: str
    thread_id: str
    internal_date_ms: int
    raw_bytes: bytes


def gmail_service(creds: Credentials):
    """Build Gmail client with a real socket timeout (default build can hang)."""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=60))
    return build("gmail", "v1", http=http, cache_discovery=False)


def iter_message_refs(
    service,
    *,
    query: str = GMAIL_SEARCH_QUERY,
    max_results: int = 1000,
) -> list[dict[str, Any]]:
    """List message id/threadId matching query (newest-first from API)."""
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(out) < max_results:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "q": query,
            "maxResults": min(100, max_results - len(out)),
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = execute_with_retry(
            service.users().messages().list(**kwargs),
            max_attempts=_GMAIL_RETRY_ATTEMPTS,
            max_delay=_GMAIL_RETRY_MAX_DELAY,
            label="gmail.messages.list",
        )
        out.extend(resp.get("messages") or [])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return out


def fetch_raw_message(service, message_id: str) -> GmailMessage:
    msg = execute_with_retry(
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="raw"),
        max_attempts=_GMAIL_RETRY_ATTEMPTS,
        max_delay=_GMAIL_RETRY_MAX_DELAY,
        label=f"gmail.messages.get:{message_id}",
    )
    raw_b64 = msg.get("raw") or ""
    raw = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    return GmailMessage(
        id=msg["id"],
        thread_id=msg.get("threadId") or "",
        internal_date_ms=int(msg.get("internalDate") or 0),
        raw_bytes=raw,
    )


def iter_amazon_mails(
    creds: Credentials,
    *,
    query: str = GMAIL_SEARCH_QUERY,
    max_results: int = 1000,
    max_fetch: int | None = None,
    skip_ids: set[str] | None = None,
) -> Iterator[GmailMessage]:
    """
    Yield matching messages oldest-first within the listed window
    (so cancel/return tend to apply after orders).

    skip_ids: already-processed Gmail message ids.

    Fetches one raw body at a time. Does **not** pre-fetch metadata for the
    whole window (that previously burned the Cloud Run timeout before any
    sheet write). ``max_fetch`` caps how many unseen messages are downloaded
    this call so each 5-minute tick makes bounded progress.
    """
    service = gmail_service(creds)
    refs = iter_message_refs(service, query=query, max_results=max_results)
    skip = skip_ids or set()
    # API returns newest-first; reverse → oldest-first among the window.
    pending_ids = [r["id"] for r in refs if r.get("id") and r["id"] not in skip]
    pending_ids.reverse()
    if max_fetch is not None and max_fetch >= 0:
        pending_ids = pending_ids[: max(0, int(max_fetch))]

    for mid in pending_ids:
        yield fetch_raw_message(service, mid)
