"""Retry / defer_seen / blank-fill helpers for mail ingest."""

from __future__ import annotations

from datetime import date
from email.message import EmailMessage

from app.mail_ingest import _is_blank_cell, _order_row_values, should_mark_mail_seen
from app.mail_parser import parse_eml_bytes
from app.schema import COL, tax_included_amount


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


def test_order_row_writes_tax_included_price_to_live_columns():
    """Live books: 税込価格=CL, 手数料=CV, Pt=DE, 売上金=DL."""
    vals = _order_row_values(
        order_id="249-9071808-9623823",
        sku="m_m48392027914",
        title="test",
        order_date=date(2026, 8, 19),
        ship_by=None,
        price=tax_included_amount(3180, 289),
        fee=364,
        points=35,
        proceeds=2779,
        cost=850,
        sheet_row=114,
    )
    assert "tax" not in COL
    assert vals[COL["price"] - 1] == 3469
    assert vals[COL["fee"] - 1] == 364
    assert vals[COL["points"] - 1] == 35
    assert vals[COL["proceeds"] - 1] == 2779
    assert COL["price"] == 90
    assert COL["fee"] == 100
    assert COL["points"] == 109
    assert COL["proceeds"] == 116
    assert COL["cost"] == 126


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
