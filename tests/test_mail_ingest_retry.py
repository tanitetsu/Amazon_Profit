"""Retry / defer_seen / blank-fill helpers for mail ingest."""

from __future__ import annotations

from email.message import EmailMessage

from app.mail_ingest import _is_blank_cell, should_mark_mail_seen
from app.mail_parser import parse_eml_bytes


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
