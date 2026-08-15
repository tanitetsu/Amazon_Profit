"""Tests for multi-item order mail parsing and legacy SKU cleanup."""

from __future__ import annotations

from email.message import EmailMessage

from app.legacy_excel import load_legacy_orders
from app.mail_parser import parse_eml_bytes, _parse_order_lines
from app.order_sku import is_placeholder_sku, normalize_sku


def test_normalize_placeholder_sku():
    assert is_placeholder_sku("2点の商品が販売されました")
    assert normalize_sku("2点の商品が販売されました") == ""
    assert normalize_sku("m_m123") == "m_m123"


def test_parse_multi_item_product_blocks():
    text = """
注文確定のお知らせ
2点の商品が販売されました
注文番号：249-1423241-2634233
注文日：2026/03/21

商品：矢車菊　苗　8-10本
SKU：m_m11111111111
数量：1
価格：￥2,260
税金：￥205
Amazon手数料：￥370
売上金：￥1,867
付与されたAmazonポイント：23

商品：ネモフィラ　ブルーベリーアイズ2
SKU：m_m22222222222
数量：1
価格：￥2,660
税金：￥242
Amazon手数料：￥435
売上金：￥2,198
付与されたAmazonポイント：27
出荷予定日：2026/03/31
"""
    lines = _parse_order_lines(text)
    assert len(lines) == 2
    assert lines[0].sku == "m_m11111111111"
    assert "矢車菊" in lines[0].title
    assert lines[0].price == 2260
    assert lines[0].tax == 205
    assert lines[1].sku == "m_m22222222222"
    assert lines[1].tax == 242
    assert "ネモフィラ" in lines[1].title
    assert all(not is_placeholder_sku(L.sku) for L in lines)
    assert all("販売されました" not in (L.title or "") for L in lines)


def test_parse_multi_item_sku_fallback_without_product_label():
    text = """
2点の商品が販売されました
注文番号：250-4219669-1251857
SKU：m_m33333333333
数量：1
価格：￥2,980
売上金：￥2,625
SKU：m_m44444444444
数量：1
価格：￥2,980
売上金：￥2,625
"""
    lines = _parse_order_lines(text)
    assert len(lines) == 2
    assert lines[0].sku == "m_m33333333333"
    assert lines[1].sku == "m_m44444444444"


def test_parse_eml_multi_item_subject():
    msg = EmailMessage()
    msg["Subject"] = "注文確定：テスト"
    msg["From"] = "auto-confirm@amazon.co.jp"
    msg.set_content(
        "2点の商品が販売されました\n"
        "注文番号：249-1423241-2634233\n"
        "注文日：2026/03/21\n"
        "商品：商品A\nSKU：m_m55555555555\n数量：1\n価格：￥1,000\n売上金：￥800\n"
        "商品：商品B\nSKU：m_m66666666666\n数量：1\n価格：￥2,000\n売上金：￥1,600\n"
        "出荷予定日：2026/03/31\n"
    )
    parsed = parse_eml_bytes(msg.as_bytes())
    assert parsed is not None
    assert parsed.kind == "order"
    assert parsed.order_id == "249-1423241-2634233"
    assert len(parsed.lines) == 2
    assert [L.sku for L in parsed.lines] == ["m_m55555555555", "m_m66666666666"]


def test_legacy_excel_clears_placeholder_sku(tmp_path):
    # Use the real download file if present; otherwise skip-style minimal check
    path = r"e:\DownLoad\Amazon利益管理シート① (3).xlsx"
    try:
        rows = load_legacy_orders(path)
    except FileNotFoundError:
        return
    bad = [r for r in rows if is_placeholder_sku(r.sku) or "販売されました" in r.sku]
    assert bad == []
    multi = [r for r in rows if r.order_id == "249-1423241-2634233"]
    assert len(multi) == 2
    assert multi[0].sku == ""
    assert "矢車菊" in multi[0].title
    assert multi[0].cost == 499
    assert "ネモフィラ" in multi[1].title
    assert multi[1].cost == 850
