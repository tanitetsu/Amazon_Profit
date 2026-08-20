"""税込価格 layout (live books) and mail 価格+税金 combine."""
from __future__ import annotations

from app.schema import (
    COL,
    DETAIL_FIELDS,
    MONTH_SUMMARY_LABELS,
    OVERVIEW_METRIC_LABELS,
    OVERVIEW_NUM_COLS,
    col_letter,
    points_fallback_from_price_tax,
    tax_included_amount,
)


def test_detail_uses_tax_included_price_without_tax_column() -> None:
    keys = [f.key for f in DETAIL_FIELDS]
    assert "tax" not in keys
    by_key = {f.key: f for f in DETAIL_FIELDS}
    assert by_key["price"].header == "税込価格"
    assert COL["fee"] == COL["price"] + by_key["price"].digits
    assert col_letter(COL["proceeds"]) == "DL"
    assert col_letter(COL["points"]) == "DE"
    assert col_letter(COL["fee"]) == "CV"


def test_summary_and_overview_match_live_tax_included_books() -> None:
    assert MONTH_SUMMARY_LABELS[0] == "税込販売額"
    assert MONTH_SUMMARY_LABELS[1] == "手数料"
    assert "税金" not in MONTH_SUMMARY_LABELS
    assert OVERVIEW_METRIC_LABELS[0] == "販売総額"
    assert OVERVIEW_METRIC_LABELS[1] == "手数料"
    assert OVERVIEW_NUM_COLS == 1 + len(OVERVIEW_METRIC_LABELS)
    assert OVERVIEW_NUM_COLS == 13


def test_points_fallback_uses_price_plus_tax() -> None:
    assert tax_included_amount(2260, 205) == 2465
    assert points_fallback_from_price_tax(2260, 205) == 25
    assert points_fallback_from_price_tax(1000, None) == 10
    assert points_fallback_from_price_tax(None, None) is None
