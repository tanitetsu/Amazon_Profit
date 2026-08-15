"""販売価格 / 税金 layout and mail fallback."""
from __future__ import annotations

from app.schema import (
    COL,
    DETAIL_FIELDS,
    MONTH_SUMMARY_LABELS,
    MONTH_SUMMARY_UNITS,
    OVERVIEW_METRIC_LABELS,
    OVERVIEW_NUM_COLS,
    points_fallback_from_price_tax,
    tax_included_amount,
)


def test_detail_price_tax_headers_and_tax_width_matches_fee() -> None:
    keys = [f.key for f in DETAIL_FIELDS]
    assert keys.index("tax") == keys.index("price") + 1
    by_key = {f.key: f for f in DETAIL_FIELDS}
    assert by_key["price"].header == "販売価格"
    assert by_key["tax"].header == "税金"
    assert by_key["tax"].digits == by_key["fee"].digits
    assert by_key["tax"].editable is False
    assert COL["tax"] == COL["price"] + by_key["price"].digits


def test_summary_and_overview_include_tax() -> None:
    assert MONTH_SUMMARY_LABELS[0] == "販売価格"
    assert MONTH_SUMMARY_LABELS[1] == "税金"
    assert MONTH_SUMMARY_LABELS[2] == "手数料"
    assert MONTH_SUMMARY_UNITS[1] == MONTH_SUMMARY_UNITS[2]
    assert OVERVIEW_METRIC_LABELS[0] == "販売価格"
    assert OVERVIEW_METRIC_LABELS[1] == "税金"
    assert OVERVIEW_NUM_COLS == 1 + len(OVERVIEW_METRIC_LABELS)


def test_points_fallback_uses_price_plus_tax_not_computed_split() -> None:
    assert tax_included_amount(2260, 205) == 2465
    assert points_fallback_from_price_tax(2260, 205) == 25
    assert points_fallback_from_price_tax(1000, None) == 10
    assert points_fallback_from_price_tax(None, None) is None
