"""Dedup index must read SKU at layout offset, not row[1]."""

from __future__ import annotations

from app.mail_ingest import _order_sku_from_index_row, _sku_index_in_oid_to_sku_row
from app.schema import COL


def test_sku_index_matches_layout():
    assert _sku_index_in_oid_to_sku_row() == COL["sku"] - COL["order_id"]
    assert _sku_index_in_oid_to_sku_row() == 20


def test_order_sku_from_merged_order_id_row():
    # Values API: only first cell of order-id merge is filled; SKU at index 20.
    row = [""] * 21
    row[0] = "503-6885145-1551844"
    row[1] = ""  # empty merge interior (old bug read this as SKU)
    row[20] = "m_m11274810053"
    oid, sku = _order_sku_from_index_row(row)
    assert oid == "503-6885145-1551844"
    assert sku == "m_m11274810053"


def test_short_row_yields_empty_sku():
    oid, sku = _order_sku_from_index_row(["503-6885145-1551844"])
    assert oid == "503-6885145-1551844"
    assert sku == ""
