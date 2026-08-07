"""Tests for bounded Gmail fetch ordering / caps."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.gmail_fetch import GmailMessage, iter_amazon_mails


def test_iter_amazon_mails_oldest_first_and_max_fetch() -> None:
    creds = MagicMock()
    # newest-first from API
    refs = [{"id": "n1"}, {"id": "n2"}, {"id": "o1"}, {"id": "o2"}]
    seen = {"n2"}

    def fake_raw(_service, mid: str) -> GmailMessage:
        return GmailMessage(id=mid, thread_id="", internal_date_ms=0, raw_bytes=b"x")

    with (
        patch("app.gmail_fetch.gmail_service", return_value=MagicMock()),
        patch("app.gmail_fetch.iter_message_refs", return_value=refs),
        patch("app.gmail_fetch.fetch_raw_message", side_effect=fake_raw) as fetch,
    ):
        ids = [m.id for m in iter_amazon_mails(creds, skip_ids=seen, max_fetch=2)]

    # unseen newest-first: n1,o1,o2 → reverse oldest-first o2,o1,n1 → cap 2 → o2,o1
    assert ids == ["o2", "o1"]
    assert [c.args[1] for c in fetch.call_args_list] == ["o2", "o1"]


def test_iter_amazon_mails_no_metadata_prepass() -> None:
    creds = MagicMock()
    refs = [{"id": "a"}, {"id": "b"}]
    with (
        patch("app.gmail_fetch.gmail_service", return_value=MagicMock()),
        patch("app.gmail_fetch.iter_message_refs", return_value=refs),
        patch(
            "app.gmail_fetch.fetch_raw_message",
            side_effect=lambda _s, mid: GmailMessage(
                id=mid, thread_id="", internal_date_ms=1, raw_bytes=b""
            ),
        ),
    ):
        list(iter_amazon_mails(creds, max_fetch=10))
