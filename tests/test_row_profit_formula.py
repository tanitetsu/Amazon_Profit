"""Unit tests for month row profit formulas."""
from __future__ import annotations

from app.schema import COL, OVERVIEW_METRIC_LABELS, col_letter
from app.sheet_builder import (
    month_kpi_anchor_a1,
    month_kpi_formulas,
    row_profit_formula,
    row_profit_rate_formula,
)
from app.template_ops import annual_sum_formulas


def test_row_profit_uses_indirect_same_row_anchors() -> None:
    f = row_profit_formula(6)
    assert f'INDIRECT("{col_letter(COL["status"])}"&ROW())' in f
    assert f'INDIRECT("{col_letter(COL["proceeds"])}"&ROW())' in f
    assert f'INDIRECT("{col_letter(COL["cost"])}"&ROW())' in f
    assert f'INDIRECT("{col_letter(COL["extra_cost"])}"&ROW())' in f
    assert "#REF" not in f
    assert f'{col_letter(COL["extra_cost"])}6' not in f


def test_row_profit_rate_is_profit_over_cost() -> None:
    f = row_profit_rate_formula(12)
    profit = col_letter(COL["profit"])
    cost = col_letter(COL["cost"])
    assert f'INDIRECT("{profit}"&ROW())' in f
    assert f'INDIRECT("{cost}"&ROW())' in f
    assert col_letter(COL["proceeds"]) not in f
    assert col_letter(COL["extra_cost"]) not in f
    assert f"{profit}12" not in f
    assert f"{cost}12" not in f
    assert (
        f'IF(AND(INDIRECT("{profit}"&ROW())<>"",'
        f'INDIRECT("{cost}"&ROW())<>"",'
        f'INDIRECT("{cost}"&ROW())<>0),'
        f'INDIRECT("{profit}"&ROW())/INDIRECT("{cost}"&ROW()),"")'
    ) in f


def test_month_kpi_rate_is_profit_minus_extra_over_proceeds() -> None:
    kpis = month_kpi_formulas()
    rate = kpis[month_kpi_anchor_a1(7)]
    proceeds = month_kpi_anchor_a1(3)
    extra = month_kpi_anchor_a1(5)
    profit = month_kpi_anchor_a1(6)
    assert rate == f'=IF({proceeds}=0,"",({profit}-{extra})/{proceeds})'


def test_month_kpi_sales_sumif_uses_price_not_tax() -> None:
    kpis = month_kpi_formulas()
    sales = kpis[month_kpi_anchor_a1(0)]
    assert "SUMIF" in sales
    assert col_letter(COL["price"]) in sales
    assert "tax" not in COL


def test_annual_rate_is_profit_minus_extra_over_proceeds() -> None:
    annual = annual_sum_formulas()
    rate_i = OVERVIEW_METRIC_LABELS.index("利益率")
    # annual[0] is blank month label cell; metrics start at index 1
    rate = annual[rate_i + 1]
    assert '/E6)' in rate or rate.endswith("/E6)")
    assert "H6-G6" in rate.replace(" ", "")
    assert rate.startswith('=IF(E6=0,"",')
