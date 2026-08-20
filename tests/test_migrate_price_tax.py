"""Pure helpers for 販売価格/税金 live migration (paused on 税込価格 schema)."""
from __future__ import annotations

import pytest

from app.migrate_price_tax import (
    EDITABLE_KEYS,
    PRICE_TAX_SPLIT_PAUSED,
    month_headers_migrated,
    overview_labels_migrated,
    price_tax_updates_for_row,
    require_price_tax_schema,
)


def test_split_is_paused_while_schema_has_no_tax() -> None:
    assert PRICE_TAX_SPLIT_PAUSED is True
    with pytest.raises(RuntimeError, match="paused"):
        require_price_tax_schema()


def test_editable_keys_never_include_price() -> None:
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


def test_price_tax_updates_refuse_while_paused() -> None:
    with pytest.raises(RuntimeError, match="paused"):
        price_tax_updates_for_row("2026-04", 6, 2260, 205)
