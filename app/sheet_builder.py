"""Build Overview and month sheet grids (Book 3 unit-column layout)."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Any, Sequence

from app.schema import (
    COL,
    DATA_START_ROW,
    DETAIL_FIELDS,
    DETAIL_SPANS,
    FORMULA_END_ROW,
    HINT_ROW_TEXT,
    KPI_VALUE_ROW,
    MONTH_SUMMARY_ANCHORS_0,
    MONTH_SUMMARY_LABELS,
    MONTH_SUMMARY_MERGES,
    NUM_COLS,
    ORDER_HEADERS,
    OVERVIEW_METRIC_KPI_INDEX,
    OVERVIEW_METRIC_LABELS,
    OVERVIEW_SECTION_MONTH_LABEL,
    OVERVIEW_SECTION_SUMMARY_LABEL,
    STATUS_BUYER_CANCEL,
    STATUS_OPEN,
    STATUS_RETURN,
    STATUS_SELLER_CANCEL,
    col_letter,
    spreadsheet_title_from_gmail,
)
from app.sheet_links import order_id_cell, title_cell


def month_kpi_anchor_a1(index: int, row: int = KPI_VALUE_ROW) -> str:
    return f"{col_letter(MONTH_SUMMARY_ANCHORS_0[index] + 1)}{row}"


def month_kpi_formulas(
    start: int = DATA_START_ROW, end: int = FORMULA_END_ROW
) -> dict[str, Any]:
    st = f"${col_letter(COL['status'])}{start}:${col_letter(COL['status'])}{end}"
    price = f"${col_letter(COL['price'])}{start}:${col_letter(COL['price'])}{end}"
    tax = f"${col_letter(COL['tax'])}{start}:${col_letter(COL['tax'])}{end}"
    fee = f"${col_letter(COL['fee'])}{start}:${col_letter(COL['fee'])}{end}"
    points = f"${col_letter(COL['points'])}{start}:${col_letter(COL['points'])}{end}"
    proceeds = f"${col_letter(COL['proceeds'])}{start}:${col_letter(COL['proceeds'])}{end}"
    cost = f"${col_letter(COL['cost'])}{start}:${col_letter(COL['cost'])}{end}"
    extra = f"${col_letter(COL['extra_cost'])}{start}:${col_letter(COL['extra_cost'])}{end}"
    order_a = (
        f"${col_letter(COL['order_id'])}{start}:${col_letter(COL['order_id'])}{end}"
    )
    shipped = f"${col_letter(COL['shipped'])}{start}:${col_letter(COL['shipped'])}{end}"
    profit_rng = (
        f"${col_letter(COL['profit'])}{start}:${col_letter(COL['profit'])}{end}"
    )
    open_s = STATUS_OPEN

    def a(i: int) -> str:
        return month_kpi_anchor_a1(i)

    sales, tax_c, fee_c, pt, proceeds_c = a(0), a(1), a(2), a(3), a(4)
    cost_c, extra_c, profit, rate = a(5), a(6), a(7), a(8)
    orders, shipped_c, cancel, returned = a(9), a(10), a(11), a(12)

    return {
        sales: f'=SUMIF({st},"{open_s}",{price})',
        tax_c: f'=SUMIF({st},"{open_s}",{tax})',
        fee_c: f'=SUMIF({st},"{open_s}",{fee})',
        pt: f'=SUMIF({st},"{open_s}",{points})',
        proceeds_c: f'=SUMIF({st},"{open_s}",{proceeds})',
        cost_c: f"=SUM({cost})",
        extra_c: f"=SUM({extra})",
        # Sum of detail 利益 (not 売上金−仕入金−諸費用 of summary cells).
        profit: f"=SUM({profit_rng})",
        # (利益 − 諸費用) / 売上金; blank when 売上金 is 0.
        rate: f'=IF({proceeds_c}=0,"",({profit}-{extra_c})/{proceeds_c})',
        orders: f'=COUNTIF({order_a},"<>")',
        shipped_c: f"=COUNTIF({shipped},TRUE)",
        cancel: (
            f'=COUNTIF({st},"{STATUS_BUYER_CANCEL}")'
            f'+COUNTIF({st},"{STATUS_SELLER_CANCEL}")'
        ),
        returned: f'=COUNTIF({st},"{STATUS_RETURN}")',
    }


def row_profit_formula(row: int) -> str:
    """Per-row 利益. `row` kept for callers; refs use ROW() via INDIRECT.

    Editable 仕入金/諸費用 are address-based so Cut / Delete-cells on those
    merges does not bake #REF! into this protected formula (Sheets rewrites
    direct A1 refs when the referenced cell is cut).
    """
    _ = row
    f = col_letter(COL["status"])
    j = col_letter(COL["proceeds"])
    k = col_letter(COL["cost"])
    l = col_letter(COL["extra_cost"])
    st = f'INDIRECT("{f}"&ROW())'
    proceeds = f'INDIRECT("{j}"&ROW())'
    cost = f'INDIRECT("{k}"&ROW())'
    extra = f'INDIRECT("{l}"&ROW())'
    return (
        f'=IF({st}="{STATUS_OPEN}",'
        f'IF(AND({proceeds}<>"",{cost}<>""),{proceeds}-{cost}-IF({extra}="",0,{extra}),""),'
        f'IF(IF({extra}="",0,{extra})=0,"",-IF({extra}="",0,{extra})))'
    )


def row_profit_rate_formula(row: int) -> str:
    """Per-row 利益率 = (利益 − 諸費用) / 売上金. Blank when 売上金 is empty/0."""
    _ = row
    m = col_letter(COL["profit"])
    j = col_letter(COL["proceeds"])
    l = col_letter(COL["extra_cost"])
    profit = f'INDIRECT("{m}"&ROW())'
    proceeds = f'INDIRECT("{j}"&ROW())'
    extra = f'INDIRECT("{l}"&ROW())'
    return (
        f'=IF(AND({profit}<>"",{proceeds}<>"",{proceeds}<>0),'
        f'({profit}-IF({extra}="",0,{extra}))/{proceeds},"")'
    )


def build_summary_grid(
    gmail: str,
    year: int,
    period_start: date | None,
    period_end: date | None,
    month_titles: Sequence[str],
) -> list[list[Any]]:
    from app.schema import OVERVIEW_META_UPDATED_LABEL
    from app.template_ops import annual_sum_formulas

    title = spreadsheet_title_from_gmail(gmail, year)
    start_s = period_start.isoformat() if period_start else "—"
    end_s = period_end.isoformat() if period_end else "—"
    meta = (
        f"ユーザー {gmail}   ·   ファイル {title}   ·   "
        f"期間 {start_s} 〜 {end_s}   ·   "
        f"{OVERVIEW_META_UPDATED_LABEL} {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    def ref(m: str, idx: int) -> str:
        return f"='{m}'!{month_kpi_anchor_a1(idx)}"

    def month_row(m: str) -> list[Any]:
        return [f'="{m}"'] + [ref(m, i) for i in OVERVIEW_METRIC_KPI_INDEX]

    annual = annual_sum_formulas()

    grid: list[list[Any]] = [
        ["ダッシュボード"],
        [meta],
        [],
        [OVERVIEW_SECTION_SUMMARY_LABEL],
        ["", *OVERVIEW_METRIC_LABELS],
        annual,
        [],
        [OVERVIEW_SECTION_MONTH_LABEL],
        ["月", *OVERVIEW_METRIC_LABELS],
    ]
    if not month_titles:
        grid.append(["（データなし）"] + [0] * len(OVERVIEW_METRIC_LABELS))
    else:
        for m in month_titles:
            grid.append(month_row(m))
    return grid


def month_summary_rows() -> tuple[list[Any], list[Any]]:
    labels = [""] * NUM_COLS
    values = [""] * NUM_COLS
    kpis = month_kpi_formulas()
    for i, (c0, _c1) in enumerate(MONTH_SUMMARY_MERGES):
        labels[c0] = MONTH_SUMMARY_LABELS[i]
        values[MONTH_SUMMARY_ANCHORS_0[i]] = kpis[month_kpi_anchor_a1(i)]
    return labels, values


def month_header_row() -> list[Any]:
    row = [""] * NUM_COLS
    for f in DETAIL_FIELDS:
        start, _end = DETAIL_SPANS[f.key]
        row[start] = f.header
    return row


def linked_order_and_title(order_id: str | None, title: str | None, sku: str | None) -> tuple[str, str]:
    """
    Display texts for 注文番号 / 商品名.
    Attach clickable URLs with hover preview via sheet_links.apply_rich_links
    (Insert-link style). Do not use =HYPERLINK for new writes.
    """
    return order_id_cell(order_id), title_cell(title, sku)


def month_sheet_skeleton(month_title: str, data_rows: int = 0) -> list[list[Any]]:
    # Rows: 1 title, 2–3 summary, 4 hint, 5 headers, 6+ data
    row1 = [month_title] + [""] * (NUM_COLS - 1)
    labels, values = month_summary_rows()
    hint = [HINT_ROW_TEXT] + [""] * (NUM_COLS - 1)
    headers = month_header_row()
    grid = [row1, labels, values, hint, headers]
    for i in range(data_rows):
        r = DATA_START_ROW + i
        blank = [""] * NUM_COLS
        blank[COL["status"] - 1] = STATUS_OPEN
        blank[COL["profit"] - 1] = row_profit_formula(r)
        blank[COL["profit_rate"] - 1] = row_profit_rate_formula(r)
        grid.append(blank)
    return grid


def period_from_months(month_titles: Sequence[str]) -> tuple[date | None, date | None]:
    if not month_titles:
        return None, None
    parsed = []
    for t in month_titles:
        y_s, m_s = t.split("-")
        parsed.append((int(y_s), int(m_s)))
    y0, m0 = min(parsed)
    y1, m1 = max(parsed)
    start = date(y0, m0, 1)
    end = date(y1, m1, calendar.monthrange(y1, m1)[1])
    return start, end
