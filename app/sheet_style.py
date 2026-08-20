"""Visual styling for Overview and month sheets (Book 3)."""

from __future__ import annotations

from typing import Any

from app.schema import (
    CANCEL_HEADER_NOTE,
    CHECKBOX_COLS,
    COL,
    DATA_ROW_HEIGHT_PX,
    DATA_START_ROW,
    DETAIL_CANCEL_HEADER_FONT_PT,
    DETAIL_DATA_ROWS,
    DETAIL_FIELDS,
    DETAIL_SPANS,
    DETAIL_FONT_PT,
    DETAIL_HEADER_FONT_PT,
    EDITABLE_COLS,
    FORMULA_END_ROW,
    HEADER_ROW,
    HINT_ROW,
    KPI_LABEL_ROW,
    KPI_VALUE_ROW,
    MONTH_COL_WIDTHS,
    MONTH_SUMMARY_LABELS,
    MONTH_SUMMARY_MERGES,
    NUM_COLS,
    OVERVIEW_COL_WIDTHS,
    OVERVIEW_COUNT_COLS,
    OVERVIEW_CURRENCY_COLS,
    OVERVIEW_KPI_LABEL_FONT_PT,
    OVERVIEW_KPI_LABEL_ROW,
    OVERVIEW_META_FONT_PT,
    OVERVIEW_KPI_VALUE_ROW,
    OVERVIEW_KPI_VALUE_ROW_HEIGHT_PX,
    OVERVIEW_LABEL_COLOR_KEYS,
    OVERVIEW_MONTH_DATA_START_ROW,
    OVERVIEW_MONTH_HEADER_ROW,
    OVERVIEW_MONTH_ROW_HEIGHT_PX,
    OVERVIEW_NUM_COLS,
    OVERVIEW_RATE_COL,
    OVERVIEW_SECTION_MONTH_ROW,
    OVERVIEW_SECTION_SUMMARY_ROW,
    OVERVIEW_ROW_COUNT,
    OVERVIEW_VALUE_FONT_PT,
    STATUS_FONT_PT,
    STATUS_HEADER_NOTE,
    STATUS_RETURN_FONT_PT,
    SUMMARY_CANCEL_LABEL_FONT_PT,
    SUMMARY_LABEL_FONT_PT,
    SUMMARY_VALUE_FONT_PT,
    col_letter,
)

BG = {"red": 0.96, "green": 0.97, "blue": 0.98}
HERO = {"red": 0.11, "green": 0.14, "blue": 0.19}
HERO_TEXT = {"red": 1, "green": 1, "blue": 1}
MUTED = {"red": 0.40, "green": 0.45, "blue": 0.52}
TABLE_HEAD = {"red": 35 / 255, "green": 45 / 255, "blue": 58 / 255}
EDITABLE_HEAD = {"red": 0.82, "green": 0.90, "blue": 0.98}
EDITABLE_HEAD_TEXT = {"red": 0.12, "green": 0.28, "blue": 0.45}
HAIR = {"red": 0.88, "green": 0.90, "blue": 0.92}
LINE = {"red": 0.55, "green": 0.60, "blue": 0.65}
WHITE = {"red": 1, "green": 1, "blue": 1}
BLACK = {"red": 0.12, "green": 0.12, "blue": 0.12}
# Summary KPI fills (label+value same; from 2026-04)
SUMMARY_BLACK_BG = {"red": 0.12, "green": 0.12, "blue": 0.12}
SUMMARY_BLUE_BG = {"red": 0.15, "green": 0.35, "blue": 0.65}
SUMMARY_ORANGE_BG = {"red": 0.90, "green": 0.45, "blue": 0.05}
SUMMARY_GREEN_BG = {"red": 0.15, "green": 0.50, "blue": 0.28}
COUNT_LABEL_BG = {"red": 239 / 255, "green": 239 / 255, "blue": 242 / 255}
# Overview (canonical: live Overview / 1uvx1Pw…)
SECTION_BG = {"red": 0.95686275, "green": 0.96862745, "blue": 0.9764706}
KPI_LABEL_BG = {"red": 0.9372549, "green": 0.95686275, "blue": 0.96862745}
KPI_VALUE_BG = WHITE
CANCEL_GRAY = {"red": 0.91, "green": 0.91, "blue": 0.91}
RETURN_PINK = {"red": 1.0, "green": 0.92, "blue": 0.92}
DONE_GRAY = {"red": 0.93, "green": 0.93, "blue": 0.93}
NEG_RED = {"red": 0.75, "green": 0.12, "blue": 0.12}


def _border(
    width: int = 1,
    color: dict | None = None,
    style: str = "SOLID",
) -> dict:
    return {"style": style, "width": width, "color": color or HAIR}


def _all_borders(
    width: int = 1,
    color: dict | None = None,
    style: str = "SOLID",
) -> dict:
    b = _border(width, color, style)
    return {"top": b, "bottom": b, "left": b, "right": b}


def _outer_inner_borders(
    *,
    is_first: bool,
    is_last: bool,
    outer_style: str = "SOLID_MEDIUM",
    inner_style: str = "SOLID",
    color: dict | None = None,
) -> dict:
    """Thick outer vertical edges for the summary block; thin between KPIs."""
    c = color or LINE
    thin = _border(1, c, inner_style)
    thick = _border(2, c, outer_style)
    return {
        "top": thick,
        "bottom": thick,
        "left": thick if is_first else thin,
        "right": thick if is_last else thin,
    }


def _paint(sheet_id: int, r0: int, r1: int, c0: int, c1: int, bg: dict | None = None, **fmt) -> dict:
    cell_fmt: dict[str, Any] = {}
    fields: list[str] = []
    if bg is not None:
        cell_fmt["backgroundColor"] = bg
        fields.append("userEnteredFormat.backgroundColor")
    cell_fmt.update(fmt)
    for key, prefix in (
        ("textFormat", "userEnteredFormat.textFormat"),
        ("horizontalAlignment", "userEnteredFormat.horizontalAlignment"),
        ("verticalAlignment", "userEnteredFormat.verticalAlignment"),
        ("borders", "userEnteredFormat.borders"),
        ("wrapStrategy", "userEnteredFormat.wrapStrategy"),
        ("numberFormat", "userEnteredFormat.numberFormat"),
    ):
        if key in fmt:
            fields.append(prefix)
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": r0,
                "endRowIndex": r1,
                "startColumnIndex": c0,
                "endColumnIndex": c1,
            },
            "cell": {"userEnteredFormat": cell_fmt},
            "fields": ",".join(fields),
        }
    }


def _col_widths(sheet_id: int, widths: list[int]) -> list[dict[str, Any]]:
    reqs = []
    for i, px in enumerate(widths):
        reqs.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": i,
                        "endIndex": i + 1,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
        )
    return reqs


def _row_heights(sheet_id: int, rows: list[tuple[int, int, int]]) -> list[dict[str, Any]]:
    """rows: (start_1based, end_1based_inclusive, px)"""
    reqs = []
    for a, b, px in rows:
        reqs.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": a - 1,
                        "endIndex": b,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
        )
    return reqs


def _merge(
    sheet_id: int,
    r0: int,
    r1: int,
    c0: int,
    c1: int,
    merge_type: str = "MERGE_ALL",
) -> dict[str, Any]:
    return {
        "mergeCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": r0,
                "endRowIndex": r1,
                "startColumnIndex": c0,
                "endColumnIndex": c1,
            },
            "mergeType": merge_type,
        }
    }


def _no_borders() -> dict:
    none = {"style": "NONE"}
    return {"top": none, "bottom": none, "left": none, "right": none}


def _note(sheet_id: int, row_0: int, col_0: int, text: str) -> dict[str, Any]:
    return {
        "updateCells": {
            "rows": [{"values": [{"note": text}]}],
            "fields": "note",
            "start": {"sheetId": sheet_id, "rowIndex": row_0, "columnIndex": col_0},
        }
    }


def _checkbox_validation(sheet_id: int, row_start: int, row_end: int) -> list[dict[str, Any]]:
    if row_end < row_start:
        return []
    reqs = []
    for col_1based in CHECKBOX_COLS:
        c0 = col_1based - 1
        reqs.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_start - 1,
                        "endRowIndex": row_end,
                        "startColumnIndex": c0,
                        "endColumnIndex": c0 + 1,
                    },
                    "rule": {
                        "condition": {"type": "BOOLEAN"},
                        "showCustomUi": True,
                        "strict": True,
                    },
                }
            }
        )
    return reqs


def _status_conditional_formats(sheet_id: int) -> list[dict[str, Any]]:
    """
    Priority (index 0 = highest):
    1. 状態 × or - → gray (beats 完了)
    2. 完了 TRUE → light gray (beats ○ / 返品 colors)
    3. 状態 返品 → pink
    """
    rng = {
        "sheetId": sheet_id,
        "startRowIndex": DATA_START_ROW - 1,
        "endRowIndex": FORMULA_END_ROW,
        "startColumnIndex": 0,
        "endColumnIndex": NUM_COLS,
    }
    status_col = col_letter(COL["status"])
    done_col = col_letter(COL["done"])
    r = DATA_START_ROW

    def rule(formula: str, color: dict, index: int) -> dict:
        return {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [rng],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": formula}],
                        },
                        "format": {"backgroundColor": color},
                    },
                },
                "index": index,
            }
        }

    return [
        rule(
            f'=OR(${status_col}{r}="×",${status_col}{r}="-")',
            CANCEL_GRAY,
            0,
        ),
        rule(f"=${done_col}{r}=TRUE", DONE_GRAY, 1),
        rule(f'=${status_col}{r}="返品"', RETURN_PINK, 2),
    ]


def _negative_number_cf(sheet_id: int) -> list[dict[str, Any]]:
    """Red font for negative 利益 / 利益率."""
    reqs = []
    for col_1based in (COL["profit"], COL["profit_rate"]):
        reqs.append(
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [
                            {
                                "sheetId": sheet_id,
                                "startRowIndex": DATA_START_ROW - 1,
                                "endRowIndex": FORMULA_END_ROW,
                                "startColumnIndex": col_1based - 1,
                                "endColumnIndex": col_1based,
                            }
                        ],
                        "booleanRule": {
                            "condition": {
                                "type": "NUMBER_LESS",
                                "values": [{"userEnteredValue": "0"}],
                            },
                            "format": {
                                "textFormat": {"foregroundColor": NEG_RED},
                            },
                        },
                    },
                    "index": 3,
                }
            }
        )
    return reqs


def summary_style_requests(sheet_id: int, month_count: int) -> list[dict[str, Any]]:
    """Overview styles — canonical: live Overview sheet."""
    reqs: list[dict[str, Any]] = []
    n = OVERVIEW_NUM_COLS
    # 0-based
    r_sum = OVERVIEW_SECTION_SUMMARY_ROW - 1
    r_lab = OVERVIEW_KPI_LABEL_ROW - 1
    r_val = OVERVIEW_KPI_VALUE_ROW - 1
    r_mon_sec = OVERVIEW_SECTION_MONTH_ROW - 1
    r_mon_h = OVERVIEW_MONTH_HEADER_ROW - 1
    r_mon_d0 = OVERVIEW_MONTH_DATA_START_ROW - 1
    months = max(month_count, 1)
    month_data_end = r_mon_d0 + months
    row_count = OVERVIEW_ROW_COUNT
    label_fills = {
        "black": SUMMARY_BLACK_BG,
        "blue": SUMMARY_BLUE_BG,
        "orange": SUMMARY_ORANGE_BG,
        "green": SUMMARY_GREEN_BG,
        "count": COUNT_LABEL_BG,
    }
    reqs.append(
        _paint(
            sheet_id,
            0,
            row_count,
            0,
            max(n, 13),
            None,
            borders=_no_borders(),
        )
    )
    reqs.append(
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "hideGridlines": True,
                        "frozenRowCount": 2,
                        "columnCount": max(n, 13),
                        "rowCount": row_count,
                    },
                    "tabColorStyle": {"rgbColor": HERO},
                },
                "fields": (
                    "gridProperties.hideGridlines,"
                    "gridProperties.frozenRowCount,"
                    "gridProperties.columnCount,"
                    "gridProperties.rowCount,"
                    "tabColorStyle"
                ),
            }
        }
    )
    reqs.append(_merge(sheet_id, 0, 1, 0, n))
    reqs.append(_merge(sheet_id, 1, 2, 0, n))
    reqs.append(
        _paint(
            sheet_id,
            0,
            1,
            0,
            n,
            HERO,
            textFormat={"foregroundColor": HERO_TEXT, "fontSize": 18, "bold": True},
            verticalAlignment="MIDDLE",
        )
    )
    reqs.append(
        _paint(
            sheet_id,
            1,
            2,
            0,
            n,
            WHITE,
            textFormat={"foregroundColor": MUTED, "fontSize": OVERVIEW_META_FONT_PT},
            verticalAlignment="BOTTOM",
        )
    )
    reqs.append(
        _paint(
            sheet_id,
            r_sum,
            r_sum + 1,
            0,
            n,
            SECTION_BG,
            textFormat={"foregroundColor": MUTED, "bold": True, "fontSize": 11},
            verticalAlignment="BOTTOM",
        )
    )
    reqs.append(_paint(sheet_id, r_lab, r_lab + 1, 0, 1, SECTION_BG))
    reqs.append(
        _paint(
            sheet_id,
            r_mon_h,
            r_mon_h + 1,
            0,
            1,
            TABLE_HEAD,
            textFormat={"foregroundColor": HERO_TEXT, "bold": True, "fontSize": 10},
            horizontalAlignment="CENTER",
            verticalAlignment="MIDDLE",
            borders=_all_borders(1, LINE),
        )
    )
    for i, key in enumerate(OVERVIEW_LABEL_COLOR_KEYS):
        col = i + 1
        bg = label_fills[key]
        fg = HERO_TEXT if key != "count" else BLACK
        for r0, font_pt in (
            (r_lab, OVERVIEW_KPI_LABEL_FONT_PT),
            (r_mon_h, 10),
        ):
            reqs.append(
                _paint(
                    sheet_id,
                    r0,
                    r0 + 1,
                    col,
                    col + 1,
                    bg,
                    textFormat={
                        "foregroundColor": fg,
                        "bold": True,
                        "fontSize": font_pt,
                    },
                    horizontalAlignment="CENTER",
                    verticalAlignment="MIDDLE",
                    borders=_all_borders(1, LINE),
                )
            )
    reqs.append(_paint(sheet_id, r_val, r_val + 1, 0, 1, SECTION_BG))
    reqs.append(
        _paint(
            sheet_id,
            r_val,
            r_val + 1,
            1,
            n,
            KPI_VALUE_BG,
            textFormat={
                "foregroundColor": HERO,
                "bold": True,
                "fontSize": OVERVIEW_VALUE_FONT_PT,
            },
            horizontalAlignment="CENTER",
            verticalAlignment="MIDDLE",
            borders=_all_borders(1, LINE),
        )
    )
    reqs.append(
        _paint(
            sheet_id,
            r_mon_sec,
            r_mon_sec + 1,
            0,
            n,
            SECTION_BG,
            textFormat={"foregroundColor": MUTED, "bold": True, "fontSize": 11},
            verticalAlignment="BOTTOM",
        )
    )
    reqs.append(
        _paint(
            sheet_id,
            r_mon_d0,
            month_data_end,
            0,
            n,
            WHITE,
            textFormat={"foregroundColor": HERO, "fontSize": 11},
            horizontalAlignment="RIGHT",
            verticalAlignment="MIDDLE",
            borders=_all_borders(1, LINE),
        )
    )

    def _num(r0: int, r1: int, c0: int, c1: int, typ: str, pattern: str) -> dict:
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": r0,
                    "endRowIndex": r1,
                    "startColumnIndex": c0,
                    "endColumnIndex": c1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": typ, "pattern": pattern}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }

    for c in OVERVIEW_CURRENCY_COLS:
        reqs.append(_num(r_val, r_val + 1, c, c + 1, "CURRENCY", "¥#,##0"))
    for c in OVERVIEW_COUNT_COLS:
        reqs.append(_num(r_val, r_val + 1, c, c + 1, "NUMBER", "#,##0"))
    reqs.append(_num(r_val, r_val + 1, OVERVIEW_RATE_COL, OVERVIEW_RATE_COL + 1, "PERCENT", "0.0%"))
    reqs.append(_num(r_mon_d0, month_data_end, 0, 1, "DATE", "yyyy-mm"))
    for c in range(1, n):
        if c == OVERVIEW_RATE_COL:
            reqs.append(_num(r_mon_d0, month_data_end, c, c + 1, "PERCENT", "0.0%"))
        else:
            reqs.append(_num(r_mon_d0, month_data_end, c, c + 1, "NUMBER", "#,##0"))

    reqs.extend(_col_widths(sheet_id, list(OVERVIEW_COL_WIDTHS)))
    month_last_1based = OVERVIEW_MONTH_DATA_START_ROW + months - 1
    reqs.extend(
        _row_heights(
            sheet_id,
            [
                (1, 1, 52),
                (2, 2, 36),
                (OVERVIEW_KPI_LABEL_ROW, OVERVIEW_KPI_LABEL_ROW, 30),
                (
                    OVERVIEW_KPI_VALUE_ROW,
                    OVERVIEW_KPI_VALUE_ROW,
                    OVERVIEW_KPI_VALUE_ROW_HEIGHT_PX,
                ),
                (
                    OVERVIEW_MONTH_HEADER_ROW,
                    month_last_1based,
                    OVERVIEW_MONTH_ROW_HEIGHT_PX,
                ),
            ],
        )
    )
    return reqs


def month_style_requests(
    sheet_id: int,
    data_rows: int | None = None,
    *,
    checkboxes: bool = False,
) -> list[dict[str, Any]]:
    """Style a month sheet.

    data_rows: detail rows to merge/paint (None = full DETAIL_DATA_ROWS through
    FORMULA_END_ROW). checkboxes: when True, BOOLEAN validation on those rows;
    template keeps False — ingest adds ☑ only on appended order rows.
    """
    if data_rows is None:
        data_rows = DETAIL_DATA_ROWS
    reqs: list[dict[str, Any]] = []
    reqs.append(
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "hideGridlines": True,
                        "frozenRowCount": HEADER_ROW,
                        "columnCount": NUM_COLS,
                        "rowCount": FORMULA_END_ROW,
                    },
                },
                "fields": (
                    "gridProperties.hideGridlines,"
                    "gridProperties.frozenRowCount,"
                    "gridProperties.columnCount,"
                    "gridProperties.rowCount"
                ),
            }
        }
    )
    reqs.extend(_col_widths(sheet_id, MONTH_COL_WIDTHS))
    reqs.extend(
        _row_heights(
            sheet_id,
            [
                (1, 1, 40),
                (KPI_LABEL_ROW, KPI_LABEL_ROW, 28),
                (KPI_VALUE_ROW, KPI_VALUE_ROW, 32),
                (HINT_ROW, HINT_ROW, 22),
                (HEADER_ROW, HEADER_ROW, DATA_ROW_HEIGHT_PX),
                # Same height for all detail rows through final row (empty included).
                (DATA_START_ROW, FORMULA_END_ROW, DATA_ROW_HEIGHT_PX),
            ],
        )
    )

    data_end_row = DATA_START_ROW + max(data_rows, 0)
    for r0, r1 in (
        (0, 2),
        (KPI_LABEL_ROW - 1, KPI_VALUE_ROW),
        (HEADER_ROW - 1, HEADER_ROW),
        (DATA_START_ROW - 1, max(data_end_row, DATA_START_ROW)),
    ):
        reqs.append(
            {
                "unmergeCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": r0,
                        "endRowIndex": r1,
                        "startColumnIndex": 0,
                        "endColumnIndex": NUM_COLS,
                    }
                }
            }
        )

    reqs.append(
        _paint(
            sheet_id,
            0,
            max(data_end_row, HEADER_ROW + 1),
            0,
            NUM_COLS,
            BG,
            borders=_no_borders(),
        )
    )

    oid0, oid1 = DETAIL_SPANS["order_id"]
    if oid1 - oid0 > 1:
        reqs.append(_merge(sheet_id, 0, 1, oid0, oid1))
    reqs.append(
        _paint(
            sheet_id,
            0,
            1,
            0,
            NUM_COLS,
            BG,
            textFormat={"bold": True, "fontSize": 22},
            horizontalAlignment="LEFT",
            verticalAlignment="MIDDLE",
            borders=_no_borders(),
        )
    )
    # Hint under summary
    reqs.append(
        _paint(
            sheet_id,
            HINT_ROW - 1,
            HINT_ROW,
            0,
            NUM_COLS,
            BG,
            textFormat={"foregroundColor": MUTED, "fontSize": 9},
            borders=_no_borders(),
        )
    )

    def _kpi_style(i: int) -> tuple[dict, dict]:
        if i in (0, 1, 2):
            return SUMMARY_BLACK_BG, WHITE
        if i == 3:
            return SUMMARY_BLUE_BG, WHITE
        if i in (4, 5):
            return SUMMARY_ORANGE_BG, WHITE
        if i in (6, 7):
            return SUMMARY_GREEN_BG, WHITE
        return COUNT_LABEL_BG, BLACK

    n_kpi = len(MONTH_SUMMARY_MERGES)
    cancel_kpi_i = MONTH_SUMMARY_LABELS.index("キャンセル")
    for i in range(n_kpi):
        c0, c1 = MONTH_SUMMARY_MERGES[i]
        bg, fg = _kpi_style(i)
        borders = _outer_inner_borders(is_first=(i == 0), is_last=(i == n_kpi - 1))
        label = MONTH_SUMMARY_LABELS[i]
        wrap = "WRAP" if "\n" in label else "OVERFLOW_CELL"
        label_pt = (
            SUMMARY_CANCEL_LABEL_FONT_PT if i == cancel_kpi_i else SUMMARY_LABEL_FONT_PT
        )
        if c1 - c0 > 1:
            reqs.append(_merge(sheet_id, KPI_LABEL_ROW - 1, KPI_LABEL_ROW, c0, c1))
            reqs.append(_merge(sheet_id, KPI_VALUE_ROW - 1, KPI_VALUE_ROW, c0, c1))
        reqs.append(
            _paint(
                sheet_id,
                KPI_LABEL_ROW - 1,
                KPI_LABEL_ROW,
                c0,
                c1,
                bg,
                textFormat={
                    "foregroundColor": fg,
                    "bold": True,
                    "fontSize": label_pt,
                },
                horizontalAlignment="CENTER",
                verticalAlignment="MIDDLE",
                borders=borders,
                wrapStrategy=wrap,
            )
        )
        # Row 3 (values): no fill, black text (labels keep colored fills).
        reqs.append(
            _paint(
                sheet_id,
                KPI_VALUE_ROW - 1,
                KPI_VALUE_ROW,
                c0,
                c1,
                WHITE,
                textFormat={
                    "bold": True,
                    "fontSize": SUMMARY_VALUE_FONT_PT,
                    "foregroundColor": BLACK,
                },
                horizontalAlignment="CENTER",
                verticalAlignment="MIDDLE",
                borders=borders,
            )
        )

    def _fmt(col0: int, pattern: str, ntype: str = "NUMBER") -> dict:
        return {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": KPI_VALUE_ROW - 1,
                    "endRowIndex": KPI_VALUE_ROW,
                    "startColumnIndex": col0,
                    "endColumnIndex": col0 + 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": ntype, "pattern": pattern}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }

    for i in (0, 1, 2, 3, 4, 5, 6):
        reqs.append(_fmt(MONTH_SUMMARY_MERGES[i][0], "#,##0"))
    reqs.append(_fmt(MONTH_SUMMARY_MERGES[7][0], "0.0%", "PERCENT"))
    for i in (8, 9, 10, 11):
        reqs.append(_fmt(MONTH_SUMMARY_MERGES[i][0], "#,##0"))

    for f in DETAIL_FIELDS:
        c0, c1 = DETAIL_SPANS[f.key]
        if c1 - c0 > 1:
            reqs.append(_merge(sheet_id, HEADER_ROW - 1, HEADER_ROW, c0, c1))
        bg = EDITABLE_HEAD if f.editable else TABLE_HEAD
        fg = EDITABLE_HEAD_TEXT if f.editable else HERO_TEXT
        wrap = "WRAP" if "\n" in f.header else "OVERFLOW_CELL"
        head_pt = (
            DETAIL_CANCEL_HEADER_FONT_PT
            if f.key == "cancel"
            else DETAIL_HEADER_FONT_PT
        )
        reqs.append(
            _paint(
                sheet_id,
                HEADER_ROW - 1,
                HEADER_ROW,
                c0,
                c1,
                bg,
                textFormat={
                    "foregroundColor": fg,
                    "bold": True,
                    "fontSize": head_pt,
                },
                horizontalAlignment="CENTER",
                verticalAlignment="MIDDLE",
                wrapStrategy=wrap,
            )
        )

    if data_rows > 0:
        r0 = DATA_START_ROW - 1
        r1 = DATA_START_ROW - 1 + data_rows
        for f in DETAIL_FIELDS:
            c0, c1 = DETAIL_SPANS[f.key]
            if c1 - c0 > 1:
                reqs.append(_merge(sheet_id, r0, r1, c0, c1, merge_type="MERGE_ROWS"))
            align = {"left": "LEFT", "center": "CENTER", "right": "RIGHT"}.get(
                f.align, "LEFT"
            )
            reqs.append(
                _paint(
                    sheet_id,
                    r0,
                    r1,
                    c0,
                    c1,
                    WHITE,
                    horizontalAlignment=align,
                    verticalAlignment="MIDDLE",
                    borders=_all_borders(1, HAIR),
                    wrapStrategy="OVERFLOW_CELL",
                    textFormat={"fontSize": DETAIL_FONT_PT},
                )
            )
        # 状態: 20pt center (返品→10 is applied per cell when value is set)
        sc0, sc1 = DETAIL_SPANS["status"]
        reqs.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": r0,
                        "endRowIndex": r1,
                        "startColumnIndex": sc0,
                        "endColumnIndex": sc1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {"fontSize": STATUS_FONT_PT},
                        }
                    },
                    "fields": (
                        "userEnteredFormat.horizontalAlignment,"
                        "userEnteredFormat.verticalAlignment,"
                        "userEnteredFormat.textFormat.fontSize"
                    ),
                }
            }
        )

    for key_a, key_b, pattern in (
        ("price", "extra_cost", "#,##0"),
        ("profit", "profit", "#,##0"),
    ):
        c0 = DETAIL_SPANS[key_a][0]
        c1 = DETAIL_SPANS[key_b][1]
        reqs.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": DATA_START_ROW - 1,
                        "endRowIndex": FORMULA_END_ROW,
                        "startColumnIndex": c0,
                        "endColumnIndex": c1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "NUMBER", "pattern": pattern}
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )
    reqs.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": DATA_START_ROW - 1,
                    "endRowIndex": FORMULA_END_ROW,
                    "startColumnIndex": DETAIL_SPANS["profit_rate"][0],
                    "endColumnIndex": DETAIL_SPANS["profit_rate"][1],
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "PERCENT", "pattern": "0.0%"}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        }
    )
    for key in ("order_date", "ship_by", "ship_date"):
        c0, c1 = DETAIL_SPANS[key]
        reqs.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": DATA_START_ROW - 1,
                        "endRowIndex": FORMULA_END_ROW,
                        "startColumnIndex": c0,
                        "endColumnIndex": c1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "DATE", "pattern": "m/d"},
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                        }
                    },
                    "fields": (
                        "userEnteredFormat.numberFormat,"
                        "userEnteredFormat.horizontalAlignment,"
                        "userEnteredFormat.verticalAlignment"
                    ),
                }
            }
        )

    reqs.append(_note(sheet_id, HEADER_ROW - 1, DETAIL_SPANS["status"][0], STATUS_HEADER_NOTE))
    reqs.append(_note(sheet_id, HEADER_ROW - 1, DETAIL_SPANS["cancel"][0], CANCEL_HEADER_NOTE))

    end_row = DATA_START_ROW + max(data_rows, 0) - 1
    if checkboxes and data_rows > 0:
        reqs.extend(_checkbox_validation(sheet_id, DATA_START_ROW, end_row))
    reqs.extend(_status_conditional_formats(sheet_id))
    reqs.extend(_negative_number_cf(sheet_id))
    return reqs
