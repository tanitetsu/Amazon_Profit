"""Retry / defer_seen / blank-fill helpers for mail ingest."""

from __future__ import annotations

import inspect
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from app.mail_ingest import (
    _apply_status_mail,
    _is_blank_cell,
    should_mark_mail_seen,
)
from app.mail_parser import ParsedMail, parse_eml_bytes
from app.schema import STATUS_BUYER_CANCEL


def test_should_mark_seen_respects_defer():
    assert should_mark_mail_seen({"action": "order"}) is True
    assert should_mark_mail_seen({"action": "status", "defer_seen": False}) is True
    assert should_mark_mail_seen({"action": "status", "defer_seen": True}) is False
    assert should_mark_mail_seen({"defer_seen": 1}) is False


def test_is_blank_cell():
    assert _is_blank_cell(None) is True
    assert _is_blank_cell("") is True
    assert _is_blank_cell("  ") is True
    assert _is_blank_cell(0) is False
    assert _is_blank_cell("○") is False
    assert _is_blank_cell("=A1") is False


def test_parse_order_halfwidth_colons():
    msg = EmailMessage()
    msg["Subject"] = "注文確定：テスト"
    msg["From"] = "auto-confirm@amazon.co.jp"
    msg.set_content(
        "注文番号: 249-1423241-2634233\n"
        "注文日: 2026/03/21\n"
        "商品：商品A\nSKU：m_m55555555555\n数量：1\n価格：￥1,000\n売上金：￥800\n"
        "出荷予定日: 2026/03/31\n"
    )
    parsed = parse_eml_bytes(msg.as_bytes())
    assert parsed is not None
    assert parsed.order_id == "249-1423241-2634233"
    assert parsed.order_date == "2026/03/21"
    assert parsed.ship_by == "2026/03/31"
    assert len(parsed.lines) == 1
    assert parsed.lines[0].price == 1000


def test_status_result_shape_defer_when_no_rows():
    # Document the contract used by ingest_user_mail (no live Sheets).
    result = {
        "action": "status",
        "status": "×",
        "order_id": "249-1423241-2634233",
        "updated": 0,
        "defer_seen": True,
        "reason": "no_matching_row",
    }
    assert should_mark_mail_seen(result) is False


def test_poll_ingest_source_does_not_lock_cells():
    import app.mail_ingest as ingest
    import app.mail_poll as poll

    ingest_src = inspect.getsource(ingest)
    poll_src = inspect.getsource(poll)
    assert "lock_cancel_checkbox" not in ingest_src
    assert "addProtectedRange" not in ingest_src
    assert "apply_protections" not in ingest_src
    assert "lock_cancel_checkbox" not in poll_src
    assert "addProtectedRange" not in poll_src
    assert "apply_protections" not in poll_src


def test_apply_status_mail_writes_values_without_protection():
    parsed = ParsedMail(
        kind="cancel_request",
        subject="キャンセル",
        order_id="111-2222222-3333333",
        sku="m_m1",
    )
    sheets = MagicMock()
    drive = MagicMock()
    row = {
        "month": "2026-08",
        "sheet_id": 7,
        "row": 6,
        "order_id": "111-2222222-3333333",
        "sku": "m_m1",
    }
    with (
        patch("app.mail_ingest.find_spreadsheet_in_folder", return_value="sid"),
        patch("app.mail_ingest._sheet_meta", return_value={"2026-08": 7}),
        patch("app.mail_ingest._index_order_rows", return_value=[row]),
        patch("app.mail_ingest.values_batch_update") as values,
        patch("app.mail_ingest.batch_update") as batch,
        patch("app.mail_ingest.touch_last_auto_update"),
    ):
        result = _apply_status_mail(
            sheets,
            drive,
            gmail="user@gmail.com",
            folder_id="folder",
            parsed=parsed,
            status=STATUS_BUYER_CANCEL,
            operator_email="ops@gmail.com",
        )

    assert result["updated"] >= 1
    assert values.called
    for call in batch.call_args_list:
        for req in call.args[2]:
            assert "addProtectedRange" not in req
            assert "deleteProtectedRange" not in req
            assert "updateProtectedRange" not in req
            assert "repeatCell" in req
