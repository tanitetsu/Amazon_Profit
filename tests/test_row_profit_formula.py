"""Unit tests for month row profit formulas."""
from __future__ import annotations

from app.schema import OVERVIEW_METRIC_LABELS
from app.sheet_builder import (
    month_kpi_anchor_a1,
    month_kpi_formulas,
    row_profit_formula,
    row_profit_rate_formula,
)
from app.template_ops import annual_sum_formulas


def test_row_profit_uses_indirect_same_row_anchors() -> None:
    f = row_profit_formula(6)
    assert "INDIRECT(\"CF\"&ROW())" in f
    assert "INDIRECT(\"DL\"&ROW())" in f
    assert "INDIRECT(\"DV\"&ROW())" in f
    assert "INDIRECT(\"EF\"&ROW())" in f
    assert "#REF" not in f
    assert "EF6" not in f


def test_row_profit_rate_uses_indirect() -> None:
    f = row_profit_rate_formula(12)
    assert "INDIRECT(\"EP\"&ROW())" in f
    assert "INDIRECT(\"DL\"&ROW())" in f
    assert "INDIRECT(\"EF\"&ROW())" in f
    assert "DV" not in f
    assert "EP12" not in f
    assert "(INDIRECT(\"EP\"&ROW())-IF(INDIRECT(\"EF\"&ROW())=\"\",0,INDIRECT(\"EF\"&ROW())))/INDIRECT(\"DL\"&ROW())" in f


def test_month_kpi_rate_is_profit_minus_extra_over_proceeds() -> None:
    kpis = month_kpi_formulas()
    rate = kpis[month_kpi_anchor_a1(7)]
    proceeds = month_kpi_anchor_a1(3)
    extra = month_kpi_anchor_a1(5)
    profit = month_kpi_anchor_a1(6)
    assert rate == f'=IF({proceeds}=0,"",({profit}-{extra})/{proceeds})'


def test_annual_rate_is_profit_minus_extra_over_proceeds() -> None:
    annual = annual_sum_formulas()
    rate_i = OVERVIEW_METRIC_LABELS.index("利益率")
    # annual[0] is blank month label cell; metrics start at index 1
    rate = annual[rate_i + 1]
    assert '/E6)' in rate or rate.endswith("/E6)")
    assert "H6-G6" in rate.replace(" ", "")
    assert rate.startswith('=IF(E6=0,"",')
