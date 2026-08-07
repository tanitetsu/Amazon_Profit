"""Clickable order / Mercari title links (Insert-link style for hover preview).

Google Sheets shows URL hover cards (often with thumbnails) for inserted links.
`=HYPERLINK()` formulas stay clickable but usually do NOT show those previews.
Workbooks are ja_JP; if a formula is ever needed, args must use ';'.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.amazon_order import seller_order_detail_url
from app.mercari import mercari_item_url
from app.sheets_retry import batch_update

# Legacy formula separator if HYPERLINK is used (ja_JP).
_ARG_SEP = ";"


def hyperlink_formula(url: str | None, label: str) -> str:
    """Legacy Sheets formula (weak hover preview). Prefer rich_link_cell."""
    text = (label or "").replace('"', '""')
    if url and text:
        return f'=HYPERLINK("{url}"{_ARG_SEP}"{text}")'
    return label or ""


def order_id_url(order_id: str | None) -> str | None:
    return seller_order_detail_url((order_id or "").strip() or None)


def title_url(sku: str | None) -> str | None:
    return mercari_item_url(sku)


def order_id_cell(order_id: str | None) -> str:
    """Display text for 注文番号 (plain; attach URL via apply_rich_links)."""
    return (order_id or "").strip()


def title_cell(title: str | None, sku: str | None = None) -> str:
    """Display text for 商品名 (plain; attach URL via apply_rich_links)."""
    return title or ""


def rich_link_cell(label: str, url: str) -> dict[str, Any]:
    """CellData equivalent of Insert → Link (enables hover preview cards)."""
    return {
        "userEnteredValue": {"stringValue": label},
        "textFormatRuns": [
            {
                "startIndex": 0,
                "format": {"link": {"uri": url}},
            }
        ],
    }


def update_rich_link_request(
    sheet_id: int, row_1based: int, col_0: int, label: str, url: str
) -> dict[str, Any]:
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_1based - 1,
                "endRowIndex": row_1based,
                "startColumnIndex": col_0,
                "endColumnIndex": col_0 + 1,
            },
            "rows": [{"values": [rich_link_cell(label, url)]}],
            "fields": "userEnteredValue,textFormatRuns",
        }
    }


def apply_rich_links(
    sheets_api,
    spreadsheet_id: str,
    sheet_id: int,
    links: Sequence[tuple[int, int, str, str]],
    *,
    label: str = "rich.links",
) -> int:
    """
    Apply Insert-link style hyperlinks.
    links: (row_1based, col_0, display_label, url)
    """
    reqs = [
        update_rich_link_request(sheet_id, row, col0, text, url)
        for row, col0, text, url in links
        if text and url
    ]
    if not reqs:
        return 0
    batch_update(
        sheets_api,
        spreadsheet_id,
        reqs,
        chunk_size=40,
        pace_seconds=0.4,
        label=label,
    )
    return len(reqs)


def order_title_rich_links(
    rows: Sequence[tuple[int, str | None, str | None, str | None]],
    *,
    order_col_0: int,
    title_col_0: int,
) -> list[tuple[int, int, str, str]]:
    """
    Build rich-link specs from (row_1based, order_id, title, sku).
    """
    out: list[tuple[int, int, str, str]] = []
    for row, order_id, title, sku in rows:
        oid = order_id_cell(order_id)
        ou = order_id_url(oid)
        if oid and ou:
            out.append((row, order_col_0, oid, ou))
        t = title_cell(title)
        tu = title_url(sku)
        if t and tu:
            out.append((row, title_col_0, t, tu))
    return out
