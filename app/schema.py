"""Spreadsheet naming and column layout (canonical: 2026-04 live Book).

Column model: 1 unit column = UNIT_PX. Detail fields merge `digits` units.
Summary fields use explicit unit widths; left edge = SKU start.
"""

from __future__ import annotations

from dataclasses import dataclass

# Live month sheet rows (1-based)
DATA_START_ROW = 6
HEADER_ROW = 5
HINT_ROW = 4
KPI_LABEL_ROW = 2
KPI_VALUE_ROW = 3
# Detail capacity (pre-merged in 月次テンプレート). Final grid row follows.
DETAIL_DATA_ROWS = 2000
FORMULA_END_ROW = DATA_START_ROW + DETAIL_DATA_ROWS - 1  # 2005

# Fonts (from 2026-04 extract; cancel sizes are special-cased)
SUMMARY_LABEL_FONT_PT = 12
SUMMARY_VALUE_FONT_PT = 14
SUMMARY_CANCEL_LABEL_FONT_PT = 10  # サマリー「キャンセル」ラベル（改行なし）
DETAIL_HEADER_FONT_PT = 12
DETAIL_CANCEL_HEADER_FONT_PT = 9  # 詳細ヘッダー「キャンセル」
DETAIL_FONT_PT = 10
STATUS_FONT_PT = 20
STATUS_RETURN_FONT_PT = 10
UNIT_PX = 7


@dataclass(frozen=True)
class FieldSpec:
    key: str
    header: str
    digits: int  # unit columns to merge
    editable: bool = False
    checkbox: bool = False
    align: str = "left"  # left | center | right


# Unit widths + headers from 2026-04
DETAIL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("order_id", "注文番号", 20),
    FieldSpec("sku", "SKU", 17),
    FieldSpec("title", "商品名", 30),
    FieldSpec("order_date", "注文日", 8, align="center"),
    FieldSpec("ship_by", "出荷予定", 8, align="center"),
    FieldSpec("status", "状態", 6, align="center"),
    FieldSpec("price", "税込価格", 10, align="right"),
    FieldSpec("fee", "手数料", 9, align="right"),
    FieldSpec("points", "Pt", 7, align="right"),
    FieldSpec("proceeds", "売上金", 10, align="right"),
    FieldSpec("cost", "仕入金", 10, editable=True, align="right"),
    FieldSpec("extra_cost", "諸費用", 10, editable=True, align="right"),
    FieldSpec("profit", "利益", 10, align="right"),
    FieldSpec("profit_rate", "利益率", 8, align="right"),
    FieldSpec("ship_date", "発送日", 8, editable=True, align="center"),
    FieldSpec("cost_done", "仕入", 6, editable=True, checkbox=True, align="center"),
    FieldSpec("shipped", "発送", 6, editable=True, checkbox=True, align="center"),
    FieldSpec("cancel", "キャン\nセル", 6, editable=True, checkbox=True, align="center"),
    FieldSpec("done", "完了", 6, editable=True, checkbox=True, align="center"),
    FieldSpec("comment", "コメント", 40, editable=True),
)

ORDER_HEADERS = [f.header for f in DETAIL_FIELDS]


def _build_detail_layout() -> tuple[dict[str, int], dict[str, tuple[int, int]], list[int]]:
    col: dict[str, int] = {}
    spans: dict[str, tuple[int, int]] = {}
    widths: list[int] = []
    cursor = 0
    for f in DETAIL_FIELDS:
        start, end = cursor, cursor + f.digits
        spans[f.key] = (start, end)
        col[f.key] = start + 1
        widths.extend([UNIT_PX] * f.digits)
        cursor = end
    return col, spans, widths


COL, DETAIL_SPANS, MONTH_COL_WIDTHS = _build_detail_layout()
NUM_COLS = len(MONTH_COL_WIDTHS)

CHECKBOX_COLS = tuple(COL[f.key] for f in DETAIL_FIELDS if f.checkbox)
EDITABLE_COLS = tuple(COL[f.key] for f in DETAIL_FIELDS if f.editable)

MONTH_SUMMARY_LABELS = [
    "税込販売額",
    "手数料",
    "Pt",
    "売上金",
    "仕入金",
    "諸費用",
    "利益",
    "利益率",
    "注文",
    "発送",
    "キャンセル",
    "返品",
]
# Explicit unit widths from 2026-04 live books (税込価格 layout)
MONTH_SUMMARY_UNITS = [13, 12, 9, 13, 13, 13, 13, 11, 10, 10, 10, 10]

SUMMARY_START_COL_0 = DETAIL_SPANS["sku"][0]


def _build_summary_merges() -> tuple[list[tuple[int, int]], list[int]]:
    merges: list[tuple[int, int]] = []
    anchors: list[int] = []
    cursor = SUMMARY_START_COL_0
    for n in MONTH_SUMMARY_UNITS:
        start, end = cursor, cursor + n
        if end > NUM_COLS:
            end = NUM_COLS
            start = max(0, end - n)
        merges.append((start, end))
        anchors.append(start)
        cursor = end
    return merges, anchors


MONTH_SUMMARY_MERGES, MONTH_SUMMARY_ANCHORS_0 = _build_summary_merges()
MONTH_SUMMARY_END_COL_0 = MONTH_SUMMARY_MERGES[-1][1]

SUMMARY_GROUP_SALES = (0, 1, 2, 3)
SUMMARY_GROUP_COST = (4, 5, 6, 7)
SUMMARY_GROUP_COUNT = (8, 9, 10, 11)

# Dashboard / template layout (canonical: Drive template workbook)
OVERVIEW_NUM_COLS = 13
OVERVIEW_COL_WIDTHS = [100, 120, 100, 80, 120, 110, 100, 120, 90, 80, 80, 100, 80]
# 1-based rows
OVERVIEW_SECTION_SUMMARY_ROW = 4
OVERVIEW_KPI_LABEL_ROW = 5
OVERVIEW_KPI_VALUE_ROW = 6
OVERVIEW_SECTION_MONTH_ROW = 8
OVERVIEW_MONTH_HEADER_ROW = 9
OVERVIEW_MONTH_DATA_START_ROW = 10
OVERVIEW_MONTH_SLOTS = 12  # annual SUM covers this many month rows
OVERVIEW_ROW_COUNT = OVERVIEW_MONTH_DATA_START_ROW + OVERVIEW_MONTH_SLOTS - 1  # fixed
OVERVIEW_SECTION_SUMMARY_LABEL = "サマリー"
OVERVIEW_SECTION_MONTH_LABEL = "月別内訳"
OVERVIEW_META_FONT_PT = 11  # Dashboard row 2 (meta line)
OVERVIEW_KPI_LABEL_FONT_PT = 11  # annual KPI labels (row 5); month headers stay 10
OVERVIEW_VALUE_FONT_PT = 14
OVERVIEW_KPI_VALUE_ROW_HEIGHT_PX = 42
OVERVIEW_MONTH_ROW_HEIGHT_PX = 32  # header + data rows
OVERVIEW_META_UPDATED_LABEL = "最終自動更新"
OVERVIEW_METRIC_LABELS = [
    "販売総額",
    "手数料",
    "Pt",
    "売上金",
    "仕入金",
    "諸費用",
    "利益",
    "利益率",
    "注文",
    "発送",
    "キャンセル",
    "返品",
]
# Month-sheet MONTH_SUMMARY index for each Overview metric (1:1)
OVERVIEW_METRIC_KPI_INDEX: list[int] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
# Label fill keys → sheet_style (white text except count)
OVERVIEW_LABEL_COLOR_KEYS = [
    "black",
    "black",
    "black",
    "blue",
    "orange",
    "orange",
    "green",
    "green",
    "count",
    "count",
    "count",
    "count",
]
# Sheet col 0-based (A=0)
OVERVIEW_CURRENCY_COLS = (1, 2, 4, 5, 6, 7)
OVERVIEW_COUNT_COLS = (3, 9, 10, 11, 12)
OVERVIEW_RATE_COL = 8  # 年間 = (利益−諸費用)/売上金 → I6=(H6-G6)/E6
OVERVIEW_SUMMARY_LABELS = OVERVIEW_METRIC_LABELS
OVERVIEW_MONTH_LABELS = OVERVIEW_METRIC_LABELS

SUMMARY_SHEET = "ダッシュボード"
MONTH_TEMPLATE_SHEET = "月次テンプレート"
TEMPLATE_SPREADSHEET_TITLE = "amazon-profit_TEMPLATE.xlsx"

STATUS_OPEN = "○"
STATUS_BUYER_CANCEL = "×"
STATUS_SELLER_CANCEL = "-"
STATUS_RETURN = "返品"

DATA_ROW_HEIGHT_PX = 32
HINT_ROW_TEXT = "青い列のみ編集可能です（値の上書きのみ。切り取り・セル削除はしないでください）"
STATUS_HEADER_NOTE = (
    "○：取引成立中\n"
    "×：購入者からのキャンセル\n"
    "-：あなたによるキャンセル\n"
    "返品：返品要求"
)
CANCEL_HEADER_NOTE = "あなたがキャンセルを行う場合にのみチェック"


def col_letter(col_1based: int) -> str:
    n = col_1based
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def field_span(key: str) -> tuple[int, int]:
    return DETAIL_SPANS[key]


def user_id_from_gmail(gmail: str) -> str:
    local, _, _ = gmail.strip().partition("@")
    if not local:
        raise ValueError(f"invalid gmail: {gmail!r}")
    return local


def gmail_from_user_id(user_id: str) -> str:
    """IAP / Auto Clipper convention: user_id is the Gmail local-part."""
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("empty user_id")
    return f"{uid}@gmail.com"


def spreadsheet_title_from_gmail(gmail: str, year: int | None = None) -> str:
    from datetime import date

    y = year if year is not None else date.today().year
    return f"amazon-profit_{user_id_from_gmail(gmail)}_{y:04d}.xlsx"


def month_sheet_title(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def points_fallback(price: float | int | None) -> int | None:
    if price is None:
        return None
    p = float(price)
    if p < 0:
        return None
    return int(p * 0.01 + 0.5)


def tax_included_amount(
    price: float | int | None, tax: float | int | None
) -> int | None:
    """Mail 価格+税金 → 税込価格列へ書く合算。"""
    if price is None and tax is None:
        return None
    return int(price or 0) + int(tax or 0)


def points_fallback_from_price_tax(
    price: float | int | None, tax: float | int | None
) -> int | None:
    """Pt missing → 1% of 税込価格, half-up."""
    return points_fallback(tax_included_amount(price, tax))


def apps_script_cancel_onedit_source() -> str:
    """Cancel☑ → 状態（値）。テンプレに UI 保存した bound simple onEdit を copy で継承。"""
    st = COL["status"]
    ca = COL["cancel"]
    start = DATA_START_ROW
    fn = STATUS_FONT_PT
    fr = STATUS_RETURN_FONT_PT
    # Minimal sheet calls; font size restored (返品=小 / 他=大).
    return f"""/** Cancel☑ → 状態。☑ON→- / OFFかつ-→○ */
function onEdit(e) {{
  if (!e || !e.range) return;
  var r = e.range;
  if (r.getNumRows() !== 1) return;
  var sh = r.getSheet();
  if (!/^\\d{{4}}-\\d{{2}}$/.test(sh.getName())) return;
  var row = r.getRow(), col = r.getColumn();
  if (row < {start}) return;
  if (col === {st}) {{
    var cell = sh.getRange(row, {st});
    cell.setFontSize(String(cell.getValue() || '') === '返品' ? {fr} : {fn});
    return;
  }}
  if (col !== {ca}) return;
  var on = e.value === true || e.value === 'TRUE' || e.value === 'true' || e.value === 1 || e.value === '1';
  var status = sh.getRange(row, {st});
  var v = String(status.getValue() || '');
  if (on) {{
    if (v !== '×' && v !== '返品' && v !== '-') status.setValue('-');
  }} else if (v === '-') {{
    status.setValue('○');
  }}
  var out = String(status.getValue() || '');
  status.setFontSize(out === '返品' ? {fr} : {fn});
}}
"""

