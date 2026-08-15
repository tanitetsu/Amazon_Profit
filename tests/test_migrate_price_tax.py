"""Pure helpers for 販売価格/税金 live migration."""
from __future__ import annotations

from app.migrate_price_tax import (
    EDITABLE_KEYS,
    month_headers_migrated,
    overview_labels_migrated,
    price_tax_updates_for_row,
)
from app.schema import COL


def test_editable_keys_never_include_price_or_tax() -> None:
    assert "price" not in EDITABLE_KEYS
    assert "tax" not in EDITABLE_KEYS
    assert "cost" in EDITABLE_KEYS
    assert "comment" in EDITABLE_KEYS


def test_month_headers_migrated() -> None:
    assert month_headers_migrated("販売価格", "税金") is True
    assert month_headers_migrated("税込価格", "手数料") is False
    assert month_headers_migrated("税込価格", "") is False
    assert month_headers_migrated("販売価格", "手数料") is False


def test_overview_labels_migrated() -> None:
    assert overview_labels_migrated(["販売価格", "税金", "手数料"]) is True
    assert overview_labels_migrated(["販売総額", "手数料", "Pt"]) is False
    assert overview_labels_migrated(["販売価格", "手数料"]) is False


def test_price_tax_updates_only_those_columns() -> None:
    from app.schema import col_letter

    updates = price_tax_updates_for_row(
        month="2026-04",
        row_1=6,
        price=2260,
        tax=205,
    )
    ranges = {u["range"] for u in updates}
    assert ranges == {
        f"'2026-04'!{col_letter(COL['price'])}6",
        f"'2026-04'!{col_letter(COL['tax'])}6",
    }
    assert {u["values"][0][0] for u in updates} == {2260, 205}


def test_price_tax_updates_skip_when_both_missing() -> None:
    assert price_tax_updates_for_row("2026-04", 6, None, None) == []
